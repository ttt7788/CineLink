import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import requests

from database import get_sys_config
from config_guard import require_drive_ready
from logger import add_log


BASE_DIR = Path(__file__).resolve().parent
ALIST_BIN = Path(os.environ.get("CINELINK_ALIST_BIN", str(BASE_DIR / "bin" / "alist.exe")))
ALIST_DATA_DIR = Path(os.environ.get("CINELINK_ALIST_DATA_DIR", str(BASE_DIR / "data" / "alist")))
ALIST_PORT = int(os.environ.get("CINELINK_ALIST_PORT", "5244"))
ALIST_BASE_URL = os.environ.get("CINELINK_ALIST_INTERNAL_URL", f"http://127.0.0.1:{ALIST_PORT}").rstrip("/")
_TOKEN_CACHE = {"value": "", "expires_at": 0}
_TOKEN_LOCK = threading.Lock()


def get_alist_admin_token():
    with _TOKEN_LOCK:
        if _TOKEN_CACHE["value"] and _TOKEN_CACHE["expires_at"] > time.time():
            return _TOKEN_CACHE["value"]
    if not ALIST_BIN.exists():
        return ""
    try:
        proc = subprocess.run(
            [str(ALIST_BIN), "--data", str(ALIST_DATA_DIR), "admin", "token"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15,
        )
    except Exception as exc:
        add_log("ERROR", f"【内置AList】读取 Admin Token 失败: {exc}")
        return ""
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    match = re.search(r"Admin token:\s*(\S+)", text)
    token = match.group(1).strip() if match else ""
    if token:
        with _TOKEN_LOCK:
            _TOKEN_CACHE["value"] = token
            _TOKEN_CACHE["expires_at"] = time.time() + 300
    return token


def _request(method, path, token, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = token
    return requests.request(method, f"{ALIST_BASE_URL}{path}", headers=headers, timeout=20, **kwargs)


def _storage_payload(mount_path, driver, addition):
    return {
        "mount_path": mount_path,
        "order": 0,
        "driver": driver,
        "cache_expiration": 30,
        "addition": json.dumps(addition, ensure_ascii=False),
        "remark": "CineLink 内置 AList 自动同步",
        "disabled": False,
        "disable_index": False,
        "enable_sign": False,
        "order_by": "name",
        "order_direction": "asc",
        "extract_folder": "front",
        "web_proxy": True,
        "webdav_policy": "native_proxy",
        "proxy_range": True,
        "down_proxy_url": "",
        "down_proxy_sign": True,
    }


def _disabled_storage_payload(mount_path, driver, addition, remark):
    payload = _storage_payload(mount_path, driver, addition)
    payload["disabled"] = True
    payload["remark"] = remark
    return payload


def get_aliyun_drive_id(refresh_token):
    if not refresh_token:
        return ""
    try:
        res = requests.post(
            "https://auth.alipan.com/v2/account/token",
            json={"refresh_token": refresh_token, "grant_type": "refresh_token"},
            headers={
                "Content-Type": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            },
            timeout=25,
        )
        try:
            data = res.json()
        except Exception:
            data = {}
        if res.status_code >= 400 or not data.get("access_token"):
            add_log("ERROR", f"【内置AList】阿里云盘 Token 校验失败: {data.get('message') or data.get('code') or res.status_code}")
            return ""

        user_info = {}
        try:
            user_res = requests.post(
                "https://user.aliyundrive.com/v2/user/get",
                json={},
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Authorization": f"Bearer {data.get('access_token')}",
                },
                timeout=25,
            )
            user_info = user_res.json() if user_res.status_code < 400 else {}
        except Exception:
            user_info = {}

        new_refresh_token = data.get("refresh_token") or ""
        if new_refresh_token and new_refresh_token != refresh_token:
            try:
                from database import get_db

                conn = get_db()
                conn.execute(
                    "REPLACE INTO system_configs (config_key, config_value) VALUES ('token_aliyun', ?)",
                    (new_refresh_token,),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        return (
            user_info.get("resource_drive_id")
            or user_info.get("backup_drive_id")
            or data.get("default_drive_id")
            or data.get("default_sbox_drive_id")
            or ""
        )
    except Exception as exc:
        add_log("ERROR", f"【内置AList】阿里云盘 Drive ID 获取异常: {exc}")
        return ""


def _upsert_storage(token, existing, payload):
    current = next((item for item in existing if item.get("mount_path") == payload["mount_path"]), None)
    if current:
        if current.get("driver") and current.get("driver") != payload.get("driver"):
            res = _request("POST", f"/api/admin/storage/delete?id={current.get('id')}", token)
            try:
                body = res.json()
            except Exception:
                body = {"code": res.status_code, "message": res.text[:300]}
            if body.get("code") != 200:
                add_log("ERROR", f"【内置AList】删除旧存储失败: {payload['mount_path']} -> {body.get('message') or body}")
                return
            current = None

    if current:
        merged = dict(current)
        merged.update(payload)
        payload = merged
        res = _request("POST", "/api/admin/storage/update", token, json=payload)
        action = "更新"
    else:
        res = _request("POST", "/api/admin/storage/create", token, json=payload)
        action = "创建"
    try:
        body = res.json()
    except Exception:
        body = {"code": res.status_code, "message": res.text[:300]}
    if body.get("code") == 200:
        add_log("INFO", f"【内置AList】{action}存储成功: {payload['mount_path']} ({payload['driver']})")
    else:
        add_log("ERROR", f"【内置AList】{action}存储失败: {payload['mount_path']} -> {body.get('message') or body}")


def sync_alist_storages():
    token = get_alist_admin_token()
    if not token:
        add_log("WARNING", "【内置AList】未取得 Admin Token，跳过存储同步。")
        return

    try:
        existing = []
        last_body = None
        for _ in range(10):
            res = _request("GET", "/api/admin/storage/list?page=1&per_page=100", token)
            body = res.json()
            last_body = body
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                existing = data.get("content") or []
                break
            time.sleep(1)
        else:
            add_log("ERROR", f"【内置AList】读取存储列表失败: {last_body}")
            return
    except Exception as exc:
        add_log("ERROR", f"【内置AList】读取存储列表失败: {exc}")
        return

    cfg = get_sys_config()
    payloads = []
    quark_cookie = (cfg.get("cookie_quark") or "").strip()
    quark_ready, quark_msg = require_drive_ready("quark", cfg)
    if quark_cookie and quark_ready:
        payloads.append(
            _storage_payload(
                "/quark",
                "Quark",
                {
                    "cookie": quark_cookie,
                    "root_folder_id": "0",
                    "order_by": "none",
                    "order_direction": "asc",
                    "use_transcoding_address": True,
                    "only_list_video_file": False,
                    "addition_version": 2,
                },
            )
        )
    elif quark_cookie:
        add_log("WARNING", f"【内置AList】跳过夸克挂载同步：{quark_msg}")

    cloud115_cookie = (cfg.get("cookie_115") or "").strip()
    cloud115_qrcode_token = (cfg.get("alist_115_qrcode_token") or "").strip()
    cloud115_qrcode_source = (cfg.get("alist_115_qrcode_source") or "web").strip() or "web"
    cloud115_cookie_source = (cfg.get("alist_115_cookie_source") or "").strip()
    mobile_cookie_sources = {"android", "ios", "qandroid", "qios", "linux", "windows", "mac"}
    cloud115_ready, cloud115_msg = require_drive_ready("115", cfg)
    if cloud115_qrcode_token and cloud115_ready:
        payloads.append(
            _storage_payload(
                "/115",
                "115 Cloud",
                {
                    "cookie": "",
                    "qrcode_token": cloud115_qrcode_token,
                    "qrcode_source": cloud115_qrcode_source,
                    "page_size": 1000,
                    "limit_rate": 2,
                    "root_folder_id": "0",
                },
            )
        )
    elif cloud115_cookie and cloud115_cookie_source in mobile_cookie_sources and cloud115_ready:
        payloads.append(
            _storage_payload(
                "/115",
                "115 Cloud",
                {
                    "cookie": cloud115_cookie,
                    "qrcode_token": "",
                    "qrcode_source": cloud115_cookie_source,
                    "page_size": 1000,
                    "limit_rate": 2,
                    "root_folder_id": "0",
                },
            )
        )
    elif cloud115_cookie and cloud115_ready:
        payloads.append(
            _disabled_storage_payload(
                "/115",
                "115 Cloud",
                {
                    "cookie": cloud115_cookie,
                    "qrcode_token": "",
                    "qrcode_source": "linux",
                    "page_size": 1000,
                    "limit_rate": 2,
                    "root_folder_id": "0",
                },
                "CineLink 内置 AList 自动同步：Cookie 模式被 115 判定重复登录，暂时禁用，等待扫码 Token 接入。",
            )
        )
    elif cloud115_cookie or cloud115_qrcode_token:
        add_log("WARNING", f"【内置AList】跳过 115 挂载同步：{cloud115_msg}")

    aliyun_token = (cfg.get("token_aliyun") or "").strip()
    aliyun_ready, aliyun_msg = require_drive_ready("aliyun", cfg)
    if aliyun_token and aliyun_ready:
        drive_id = get_aliyun_drive_id(aliyun_token)
        if drive_id:
            payloads.append(
                _storage_payload(
                    "/aliyun",
                    "Aliyundrive",
                    {
                        "root_folder_id": "root",
                        "refresh_token": aliyun_token,
                        "device_id": drive_id,
                        "order_by": "name",
                        "order_direction": "ASC",
                        "rapid_upload": False,
                        "internal_upload": False,
                    },
                )
            )
    elif aliyun_token:
        add_log("WARNING", f"【内置AList】跳过阿里云盘挂载同步：{aliyun_msg}")

    drive123_client_id = (cfg.get("drive123_client_id") or "").strip()
    drive123_client_secret = (cfg.get("drive123_client_secret") or "").strip()
    drive123_ready, drive123_msg = require_drive_ready("123", cfg)
    if drive123_client_id and drive123_client_secret and drive123_ready:
        payloads.append(
            _storage_payload(
                "/123",
                "123 Open",
                {
                    "root_folder_id": cfg.get("drive123_save_dir", "0") or "0",
                    "client_id": drive123_client_id,
                    "client_secret": drive123_client_secret,
                    "private_key": "",
                    "uid": 0,
                    "valid_duration": 30,
                },
            )
        )
    elif drive123_client_id or drive123_client_secret:
        add_log("WARNING", f"【内置AList】跳过 123云盘挂载同步：{drive123_msg}")

    for payload in payloads:
        _upsert_storage(token, existing, payload)

    try:
        _request("POST", "/api/admin/storage/load_all", token)
    except Exception as exc:
        add_log("WARNING", f"【内置AList】刷新存储失败: {exc}")
