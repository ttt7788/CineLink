import os
import subprocess
import time
from pathlib import Path

import requests

from logger import add_log


BASE_DIR = Path(__file__).resolve().parent
ALIST_ENABLED = os.environ.get("CINELINK_ALIST_ENABLED", "1").lower() not in {"0", "false", "no"}
ALIST_BIN = Path(os.environ.get("CINELINK_ALIST_BIN", str(BASE_DIR / "bin" / "alist.exe")))
ALIST_DATA_DIR = Path(os.environ.get("CINELINK_ALIST_DATA_DIR", str(BASE_DIR / "data" / "alist")))
ALIST_BIND_HOST = os.environ.get("CINELINK_ALIST_BIND_HOST", os.environ.get("CINELINK_ALIST_HOST", "127.0.0.1"))
ALIST_CHECK_HOST = os.environ.get("CINELINK_ALIST_CHECK_HOST", "127.0.0.1")
ALIST_PORT = int(os.environ.get("CINELINK_ALIST_PORT", "5244"))
ALIST_PUBLIC_URL = os.environ.get("CINELINK_ALIST_PUBLIC_URL", f"http://127.0.0.1:{ALIST_PORT}").rstrip("/")

_process = None


def _creation_flags():
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _is_running():
    try:
        res = requests.get(f"http://{ALIST_CHECK_HOST}:{ALIST_PORT}/api/public/settings", timeout=2)
        return res.status_code < 500
    except Exception:
        return False


def start_alist_sidecar():
    global _process
    if not ALIST_ENABLED:
        add_log("INFO", "【内置AList】已禁用。")
        return None
    if _is_running():
        add_log("INFO", f"【内置AList】检测到已运行: {ALIST_PUBLIC_URL}")
        return None
    if not ALIST_BIN.exists():
        add_log("WARNING", f"【内置AList】未找到可执行文件: {ALIST_BIN}")
        return None

    ALIST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = ALIST_DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = open(log_dir / "sidecar.out.log", "ab", buffering=0)
    stderr = open(log_dir / "sidecar.err.log", "ab", buffering=0)

    env = os.environ.copy()
    env.setdefault("ALIST_FORCE", "false")
    env.setdefault("ALIST_SCHEME_HTTP_PORT", str(ALIST_PORT))
    env.setdefault("ALIST_SCHEME_ADDRESS", ALIST_BIND_HOST)

    args = [str(ALIST_BIN), "--data", str(ALIST_DATA_DIR), "server"]
    try:
        _process = subprocess.Popen(
            args,
            cwd=str(BASE_DIR),
            stdout=stdout,
            stderr=stderr,
            env=env,
            creationflags=_creation_flags(),
        )
    except Exception as exc:
        add_log("ERROR", f"【内置AList】启动失败: {exc}")
        return None

    for _ in range(20):
        if _is_running():
            add_log("INFO", f"【内置AList】已启动: {ALIST_PUBLIC_URL}，数据目录: {ALIST_DATA_DIR}")
            return _process
        if _process.poll() is not None:
            add_log("ERROR", f"【内置AList】进程提前退出，退出码: {_process.returncode}")
            return _process
        time.sleep(0.5)

    add_log("WARNING", f"【内置AList】已拉起但健康检查暂未通过: {ALIST_PUBLIC_URL}")
    return _process


def stop_alist_sidecar():
    global _process
    if not _process:
        return
    if _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
    add_log("WARNING", "【内置AList】服务已停止。")
    _process = None
