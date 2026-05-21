import datetime
import time
from typing import List

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from aliyun_drive_mobile import AliyunDrive, _safe_json
from config_guard import get_drive_config_status, require_drive_ready
from database import get_db, get_sys_config
from drive_api import QuarkDrive
from logger import add_log
from p115_runtime import ensure_p115_runtime_home

router = APIRouter()

DRIVE_ORDER = ("115", "aliyun", "quark", "123")
DRIVE_LABELS = {
    "115": "115网盘",
    "aliyun": "阿里云盘",
    "quark": "夸克网盘",
    "123": "123云盘",
}
QUARK_PC_API_URL = "https://drive-pc.quark.cn/1/clouddrive"
QUARK_PC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 "
    "Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"
)


class RecycleDriveReq(BaseModel):
    drive_type: str


class RecycleConfigReq(BaseModel):
    enabled: str = "0"
    drives: List[str] = []
    interval_hours: int = 24
    password_115: str = ""


def _set_config_values(values):
    conn = get_db()
    conn.executemany(
        "REPLACE INTO system_configs (config_key, config_value) VALUES (?, ?)",
        list(values.items()),
    )
    conn.commit()
    conn.close()


def _normalize_drive_type(drive_type):
    drive_type = str(drive_type or "").strip()
    return drive_type if drive_type in DRIVE_ORDER else "115"


def _format_time(value):
    if value in (None, ""):
        return ""
    try:
        ts = int(float(value))
        if ts > 1000000000000:
            ts = ts / 1000
        if ts > 1000000000:
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        pass
    return str(value).replace("T", " ").replace("Z", "")


def _format_api_error(data, fallback):
    if not isinstance(data, dict):
        return fallback
    message = data.get("message") or data.get("msg") or data.get("error") or fallback
    code = data.get("code") or data.get("status")
    request_id = (
        data.get("requestId")
        or data.get("request_id")
        or (data.get("metadata") or {}).get("request_id")
        or (data.get("data") or {}).get("request_id")
    )
    parts = [str(message)]
    if code not in (None, "", 0, 200):
        parts.append(f"code={code}")
    if request_id:
        parts.append(f"requestId={request_id}")
    return "，".join(parts)


def get_recycle_config(config=None):
    config = config or get_sys_config()
    drives = [
        item.strip()
        for item in str(config.get("plugin_recycle_drives") or "115,aliyun,quark").split(",")
        if item.strip() in DRIVE_ORDER
    ]
    if not drives:
        drives = list(DRIVE_ORDER)
    try:
        interval_hours = int(config.get("plugin_recycle_interval_hours") or 24)
    except (TypeError, ValueError):
        interval_hours = 24
    return {
        "enabled": str(config.get("plugin_recycle_enabled") or "0"),
        "drives": drives,
        "interval_hours": max(interval_hours, 1),
        "password_115": str(config.get("plugin_recycle_115_password") or ""),
        "last_run": str(config.get("plugin_recycle_last_run") or ""),
    }


def _normalize_item(drive_type, item):
    if drive_type == "115":
        item_type = str(item.get("type") or "")
        return {
            "id": str(item.get("id") or item.get("fid") or item.get("file_id") or item.get("rid") or ""),
            "name": item.get("file_name") or item.get("name") or item.get("n") or item.get("fn") or "未命名",
            "type": "folder" if item_type in {"0", "folder"} or item.get("is_dir") or item.get("is_folder") else "file",
            "size": item.get("file_size") or item.get("size") or item.get("s") or 0,
            "trashed_at": _format_time(item.get("delete_time") or item.get("dtime") or item.get("time") or item.get("t") or ""),
        }
    if drive_type == "aliyun":
        return {
            "id": str(item.get("file_id") or ""),
            "name": item.get("name") or "未命名",
            "type": item.get("type") or "file",
            "size": item.get("size") or 0,
            "trashed_at": _format_time(item.get("trashed_at") or item.get("updated_at") or ""),
        }
    return {
        "id": str(item.get("record_id") or item.get("fid") or ""),
        "name": item.get("file_name") or item.get("name") or "未命名",
        "type": "folder" if item.get("file_type") == 0 else "file",
        "size": item.get("size") or 0,
        "trashed_at": _format_time(item.get("delete_time") or item.get("created_at") or item.get("updated_at") or ""),
    }


async def _list_115_recycle(cookie, limit=100):
    if not cookie:
        return [], "未配置 115 Cookie"
    ensure_p115_runtime_home()
    from p115client import P115Client

    client = P115Client(cookie)
    data = client.recyclebin_list({"limit": limit, "offset": 0})
    if data.get("state") is False or data.get("code") not in (None, 0, 200):
        return [], data.get("error") or data.get("msg") or data.get("message") or "115 回收站读取失败"
    items = data.get("data") or data.get("list") or data.get("items") or []
    if isinstance(items, dict):
        items = items.get("list") or items.get("data") or []
    return [_normalize_item("115", item) for item in items], "success"


async def _empty_115_recycle(cookie, password=""):
    if not cookie:
        return False, "未配置 115 Cookie"
    ensure_p115_runtime_home()
    from p115client import P115Client

    payload = {"password": password or "000000"}
    data = P115Client(cookie).recyclebin_clean_app(payload)
    if data.get("state") is False or data.get("code") not in (None, 0, 200):
        return False, data.get("error") or data.get("msg") or data.get("message") or "115 回收站清空失败"
    return True, "115 回收站清空任务已提交"


async def _list_aliyun_recycle(refresh_token, limit=100):
    api = AliyunDrive(refresh_token)
    ok, msg = await api._refresh_access_token()
    if not ok:
        return [], msg
    items = []
    marker = None
    async with httpx.AsyncClient(timeout=api.timeout) as client:
        while len(items) < limit:
            payload = {"drive_id": api.default_drive_id, "limit": min(100, limit - len(items))}
            if marker:
                payload["marker"] = marker
            res = await client.post(f"{api.api_url}/v2/recyclebin/list", json=payload, headers=api._auth_headers())
            data = _safe_json(res)
            if res.status_code >= 400 or data.get("code"):
                return [], api._format_error(data, "阿里云盘回收站读取失败")
            items.extend(data.get("items") or [])
            marker = data.get("next_marker")
            if not marker:
                break
    return [_normalize_item("aliyun", item) for item in items], "success"


async def _empty_aliyun_recycle(refresh_token):
    api = AliyunDrive(refresh_token)
    ok, msg = await api._refresh_access_token()
    if not ok:
        return False, msg
    async with httpx.AsyncClient(timeout=api.timeout) as client:
        res = await client.post(
            f"{api.api_url}/v2/recyclebin/clear",
            json={"drive_id": api.default_drive_id},
            headers=api._auth_headers(),
        )
        data = _safe_json(res)
        if res.status_code >= 400 or data.get("code"):
            return False, api._format_error(data, "阿里云盘回收站清空失败")
    return True, "阿里云盘回收站清空任务已提交"


def _quark_headers(api):
    headers = api.headers.copy()
    headers.update(
        {
            "origin": "https://pan.quark.cn",
            "referer": "https://pan.quark.cn/",
            "user-agent": QUARK_PC_USER_AGENT,
        }
    )
    return headers


def _quark_base_params(api):
    return {
        "pr": "ucpro",
        "fr": "pc",
        "uc_param_str": "",
    }


async def _list_quark_recycle_raw(cookie, limit=100):
    if not cookie:
        return [], "未配置夸克 Cookie"
    api = QuarkDrive(cookie)
    items = []
    page = 1
    async with httpx.AsyncClient(timeout=api.timeout) as client:
        while len(items) < limit:
            size = min(100, limit - len(items))
            params = _quark_base_params(api)
            params.update({"_page": page, "_size": size})
            res = await client.get(
                f"{QUARK_PC_API_URL}/file/recycle/list",
                params=params,
                headers=_quark_headers(api),
            )
            api._sync_response_cookies(res)
            data = _safe_json(res)
            if data.get("code") != 0:
                return [], _format_api_error(data, "夸克回收站读取失败")
            page_items = data.get("data", {}).get("list") or []
            items.extend(page_items)
            if len(page_items) < size:
                break
            page += 1
    return items, "success"


async def _list_quark_recycle(cookie, limit=100):
    items, msg = await _list_quark_recycle_raw(cookie, limit)
    if msg != "success":
        return [], msg
    return [_normalize_item("quark", item) for item in items], "success"


def _chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


async def _empty_quark_recycle(cookie):
    api = QuarkDrive(cookie)
    headers = _quark_headers(api)
    total_cleaned = 0
    last_error = ""
    async with httpx.AsyncClient(timeout=api.timeout) as client:
        for _ in range(20):
            raw_items, msg = await _list_quark_recycle_raw(cookie, 500)
            if msg != "success":
                return False, msg
            if not raw_items:
                if total_cleaned:
                    return True, f"夸克回收站已清空，共清理 {total_cleaned} 条"
                return True, "夸克回收站为空"

            record_ids = [str(item.get("record_id") or "") for item in raw_items]
            record_ids = [record_id for record_id in record_ids if record_id]
            if not record_ids:
                return False, "夸克回收站未返回可清理的 record_id"

            # 夸克 PC 端接口使用 record_id 列表；分批能避开回收站记录较多时的 inner error。
            for batch in _chunked(record_ids, 50):
                payload = {"select_mode": 2, "record_list": batch}
                res = await client.post(
                    f"{QUARK_PC_API_URL}/file/recycle/remove",
                    params=_quark_base_params(api),
                    json=payload,
                    headers=headers,
                )
                api._sync_response_cookies(res)
                data = _safe_json(res)
                if data.get("code") != 0:
                    last_error = _format_api_error(data, "夸克回收站清空失败")
                    break
                total_cleaned += len(batch)
            if last_error:
                break

    if last_error:
        prefix = f"已清理 {total_cleaned} 条，" if total_cleaned else ""
        return False, f"{prefix}{last_error}"
    return False, f"夸克回收站仍有剩余记录，已清理 {total_cleaned} 条，请稍后重试"


async def list_recyclebin(drive_type, config=None):
    config = config or get_sys_config()
    drive_type = _normalize_drive_type(drive_type)
    ready, msg = require_drive_ready(drive_type, config, require_save_dir=False)
    if not ready:
        return [], msg
    if drive_type == "115":
        return await _list_115_recycle(config.get("cookie_115", ""))
    if drive_type == "aliyun":
        return await _list_aliyun_recycle(config.get("token_aliyun", ""))
    if drive_type == "123":
        return [], "123云盘 Open API 暂未提供回收站读取接口"
    return await _list_quark_recycle(config.get("cookie_quark", ""))


async def empty_recyclebin(drive_type, config=None):
    config = config or get_sys_config()
    drive_type = _normalize_drive_type(drive_type)
    ready, msg = require_drive_ready(drive_type, config, require_save_dir=False)
    if not ready:
        return False, msg
    if drive_type == "115":
        return await _empty_115_recycle(config.get("cookie_115", ""), config.get("plugin_recycle_115_password", ""))
    if drive_type == "aliyun":
        return await _empty_aliyun_recycle(config.get("token_aliyun", ""))
    if drive_type == "123":
        return False, "123云盘 Open API 暂未提供回收站清空接口"
    return await _empty_quark_recycle(config.get("cookie_quark", ""))


@router.get("/api/plugins/recycle/config")
def api_recycle_config():
    config = get_sys_config()
    plugin_config = get_recycle_config(config)
    return {
        **plugin_config,
        "drive_status": [get_drive_config_status(drive_type, config) for drive_type in DRIVE_ORDER],
    }


@router.post("/api/plugins/recycle/config")
def api_save_recycle_config(req: RecycleConfigReq):
    drives = [drive for drive in req.drives if drive in DRIVE_ORDER] or list(DRIVE_ORDER)
    _set_config_values(
        {
            "plugin_recycle_enabled": "1" if str(req.enabled) == "1" else "0",
            "plugin_recycle_drives": ",".join(drives),
            "plugin_recycle_interval_hours": str(max(int(req.interval_hours or 24), 1)),
            "plugin_recycle_115_password": req.password_115 or "",
        }
    )
    state_text = "启用" if str(req.enabled) == "1" else "停用"
    add_log("INFO", f"【插件·回收站】配置已更新：{state_text}，网盘 {','.join(drives)}")
    return {"code": 200, "message": "回收站插件配置已保存"}


@router.post("/api/plugins/recycle/list")
async def api_recycle_list(req: RecycleDriveReq):
    drive_type = _normalize_drive_type(req.drive_type)
    items, msg = await list_recyclebin(drive_type)
    return {"code": 200 if msg == "success" else 500, "data": items, "msg": msg, "drive_type": drive_type}


@router.post("/api/plugins/recycle/empty")
async def api_recycle_empty(req: RecycleDriveReq):
    drive_type = _normalize_drive_type(req.drive_type)
    success, msg = await empty_recyclebin(drive_type)
    add_log("SUCCESS" if success else "ERROR", f"【插件·回收站】{DRIVE_LABELS[drive_type]} 清空结果: {msg}")
    return {"code": 200 if success else 500, "msg": msg, "drive_type": drive_type}


async def auto_empty_recyclebin_if_due():
    config = get_sys_config()
    plugin_config = get_recycle_config(config)
    if plugin_config["enabled"] != "1":
        return
    now = time.time()
    try:
        last_run = float(plugin_config.get("last_run") or 0)
    except (TypeError, ValueError):
        last_run = 0
    interval_seconds = max(int(plugin_config["interval_hours"]), 1) * 3600
    if last_run and now - last_run < interval_seconds:
        return
    _set_config_values({"plugin_recycle_last_run": str(int(now))})
    add_log("INFO", "【插件·回收站】开始执行定时自动清空。")
    for drive_type in plugin_config["drives"]:
        try:
            success, msg = await empty_recyclebin(drive_type, config)
            add_log("SUCCESS" if success else "ERROR", f"【插件·回收站】{DRIVE_LABELS[drive_type]} 定时清空: {msg}")
        except Exception as exc:
            add_log("ERROR", f"【插件·回收站】{DRIVE_LABELS.get(drive_type, drive_type)} 定时清空异常: {exc}")
