import json
import os
import time
from datetime import datetime

from database import DB_DIR
from logger import add_log


RUNTIME_DIR = os.path.join(DB_DIR, "strm_runtime")
VALID_STATES = {"idle", "running", "paused", "stopped", "completed", "failed"}


class StrmControlStopped(Exception):
    pass


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_runtime_dir():
    os.makedirs(RUNTIME_DIR, exist_ok=True)


def _state_path(config_id):
    _ensure_runtime_dir()
    return os.path.join(RUNTIME_DIR, f"config_{int(config_id)}.json")


def read_strm_state(config_id):
    path = _state_path(config_id)
    if not os.path.exists(path):
        return {"config_id": int(config_id), "state": "idle", "pid": None, "message": "", "updated_at": ""}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["config_id"] = int(config_id)
        data["state"] = data.get("state") if data.get("state") in VALID_STATES else "idle"
        return data
    except Exception:
        return {"config_id": int(config_id), "state": "idle", "pid": None, "message": "", "updated_at": ""}


def write_strm_state(config_id, state, message="", pid=None, **extra):
    if state not in VALID_STATES:
        state = "idle"
    previous = read_strm_state(config_id)
    data = {
        **previous,
        **extra,
        "config_id": int(config_id),
        "state": state,
        "message": message,
        "updated_at": _now(),
    }
    if pid is not None:
        data["pid"] = pid
    path = _state_path(config_id)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return data


def start_strm_job(config_id, pid=None):
    return write_strm_state(
        config_id,
        "running",
        "STRM 生成任务运行中",
        pid=pid or os.getpid(),
        started_at=_now(),
        finished_at="",
    )


def finish_strm_job(config_id, state="completed", message="STRM 生成任务已完成"):
    return write_strm_state(config_id, state, message, finished_at=_now())


def request_strm_action(config_id, action):
    action = str(action or "").lower()
    if action == "pause":
        state = write_strm_state(config_id, "paused", "用户已暂停 STRM 生成任务")
        add_log("WARNING", f"STRM 生成任务已暂停，节点 ID: {config_id}", module="strm")
        return state
    if action == "resume":
        state = write_strm_state(config_id, "running", "用户已继续 STRM 生成任务")
        add_log("INFO", f"STRM 生成任务已继续，节点 ID: {config_id}", module="strm")
        return state
    if action == "stop":
        state = write_strm_state(config_id, "stopped", "用户已结束 STRM 生成任务")
        add_log("WARNING", f"STRM 生成任务收到结束指令，节点 ID: {config_id}", module="strm")
        return state
    raise ValueError("不支持的 STRM 控制动作")


def check_strm_control(config_id, phase=""):
    paused_logged = False
    while True:
        state = read_strm_state(config_id).get("state")
        if state == "stopped":
            raise StrmControlStopped("STRM 生成任务已被用户结束")
        if state != "paused":
            return
        if not paused_logged:
            suffix = f" ({phase})" if phase else ""
            add_log("WARNING", f"STRM 生成任务已暂停{suffix}，等待继续或结束指令。", module="strm")
            paused_logged = True
        time.sleep(2)

