import os
from pathlib import Path


def ensure_p115_runtime_home() -> Path:
    """Prepare a writable HOME before importing p115client."""
    data_dir = Path(os.environ.get("CINELINK_DATA_DIR") or "data").expanduser()
    home_dir = Path(os.environ.get("CINELINK_P115_HOME") or data_dir / "p115_home").expanduser()
    cache_dir = home_dir / ".p115client.cache.d"

    home_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home_dir)
    os.environ.setdefault("XDG_CACHE_HOME", str(data_dir / "cache"))
    return cache_dir
