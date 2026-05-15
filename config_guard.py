from database import get_sys_config
from logger import add_log


DRIVE_CONFIGS = {
    "115": {
        "label": "115网盘",
        "auth_key": "cookie_115",
        "auth_label": "115 Cookie",
        "save_dir_key": "drive115_save_dir",
        "save_dir_label": "115 默认保存目录 ID",
    },
    "aliyun": {
        "label": "阿里云盘",
        "auth_key": "token_aliyun",
        "auth_label": "阿里云 Refresh Token",
        "save_dir_key": "aliyun_save_dir",
        "save_dir_label": "阿里云默认保存目录 ID",
    },
    "quark": {
        "label": "夸克网盘",
        "auth_key": "cookie_quark",
        "auth_label": "夸克 Cookie",
        "save_dir_key": "quark_save_dir",
        "save_dir_label": "夸克默认保存目录 ID",
    },
    "123": {
        "label": "123云盘",
        "auth_key": "drive123_client_id",
        "auth_label": "123云盘 Client ID",
        "save_dir_key": "drive123_save_dir",
        "save_dir_label": "123云盘默认保存目录 ID",
    },
}


def normalize_drive_type(drive_type):
    value = (drive_type or "115").lower()
    if value in {"115", "cloud115"}:
        return "115"
    if value in {"aliyun", "alipan", "aliyundrive"}:
        return "aliyun"
    if value == "quark":
        return "quark"
    if value in {"123", "123pan", "pan123", "123cloud"}:
        return "123"
    return value


def _is_set(value):
    return bool(str(value or "").strip())


def get_drive_config_status(drive_type, config=None):
    config = config or get_sys_config()
    drive_type = normalize_drive_type(drive_type)
    meta = DRIVE_CONFIGS.get(drive_type)
    if not meta:
        return {
            "drive_type": drive_type,
            "label": drive_type,
            "known": False,
            "auth_ok": False,
            "save_dir_ok": False,
            "ready": False,
            "missing": [f"未知网盘类型: {drive_type}"],
            "save_dir": "",
        }

    auth_ok = _is_set(config.get(meta["auth_key"]))
    if drive_type == "123":
        auth_ok = auth_ok and _is_set(config.get("drive123_client_secret"))
    save_dir = str(config.get(meta["save_dir_key"]) or "").strip()
    save_dir_ok = _is_set(save_dir)
    missing = []
    if not auth_ok:
        missing.append(meta["auth_label"])
        if drive_type == "123":
            missing.append("123云盘 Client Secret")
    if not save_dir_ok:
        missing.append(meta["save_dir_label"])
    return {
        "drive_type": drive_type,
        "label": meta["label"],
        "known": True,
        "auth_ok": auth_ok,
        "save_dir_ok": save_dir_ok,
        "ready": auth_ok and save_dir_ok,
        "missing": missing,
        "save_dir": save_dir,
        **meta,
    }


def require_drive_ready(drive_type, config=None, require_auth=True, require_save_dir=True):
    status = get_drive_config_status(drive_type, config)
    missing = []
    if not status.get("known"):
        missing.extend(status.get("missing") or [])
    if require_auth and not status.get("auth_ok"):
        missing.append(status.get("auth_label") or "授权配置")
    if require_save_dir and not status.get("save_dir_ok"):
        missing.append(status.get("save_dir_label") or "默认保存目录")
    if missing:
        label = status.get("label") or drive_type
        return False, f"{label} 未配置完整：{', '.join(dict.fromkeys(missing))}"
    return True, "ready"


def log_startup_drive_config_status():
    config = get_sys_config()
    for drive_type in ("115", "aliyun", "quark", "123"):
        status = get_drive_config_status(drive_type, config)
        if status["ready"]:
            add_log("SUCCESS", f"【启动检查】{status['label']} 已配置，默认保存目录: {status['save_dir']}")
        else:
            add_log(
                "WARNING",
                f"【启动检查】{status['label']} 未配置完整，相关转存、网盘文件、STRM 内置节点功能将跳过。缺少: {', '.join(status['missing'])}",
            )
