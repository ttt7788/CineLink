import asyncio
import mimetypes
import threading
import time
from datetime import datetime
from urllib.parse import unquote

from aliyun_drive_mobile import AliyunDrive
from database import get_sys_config
from drive_api import Drive115, QuarkDrive, Drive123Open
from logger import add_log


DOWNLOAD_URL_CACHE_TTL = 300
PATH_CACHE_TTL = 3600


class TimedCache:
    def __init__(self, ttl):
        self.ttl = ttl
        self.lock = threading.RLock()
        self.items = {}

    def get(self, key):
        with self.lock:
            item = self.items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < time.time():
                self.items.pop(key, None)
                return None
            return value

    def set(self, key, value):
        if value is None:
            return
        with self.lock:
            self.items[key] = (time.time() + self.ttl, value)


class PathCache:
    def __init__(self, root_item):
        self.children = TimedCache(PATH_CACHE_TTL)
        self.paths = {"/": root_item}
        self.lock = threading.RLock()

    def get_path(self, path):
        with self.lock:
            return self.paths.get(path)

    def set_path(self, path, item):
        with self.lock:
            self.paths[path] = item


def run_async(coro):
    return asyncio.run(coro)


def join_path(parent, name):
    return "/" + "/".join([part for part in f"{parent.rstrip('/')}/{name}".split("/") if part])


def normalize_path(path):
    return "/" + "/".join([part for part in (path or "/").strip("/").split("/") if part])


def parse_timestamp(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            value = int(value)
            return value / 1000 if value > 100000000000 else value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class AliyunProvider:
    def __init__(self):
        self.cache = PathCache({"file_id": "root", "name": "", "type": "folder"})
        self.download_cache = TimedCache(DOWNLOAD_URL_CACHE_TTL)
        self.preview_cache = TimedCache(DOWNLOAD_URL_CACHE_TTL)

    def _drive(self):
        token = get_sys_config().get("token_aliyun", "").strip()
        return AliyunDrive(token) if token else None

    def list_children(self, parent_id):
        cached = self.cache.children.get(parent_id)
        if cached is not None:
            return cached
        drive = self._drive()
        if not drive:
            add_log("WARNING", "【内置网盘】未配置阿里云盘 Refresh Token，aliyun 暂不可浏览。")
            return []
        items, msg = run_async(drive.list_files(parent_id))
        if msg != "success":
            add_log("ERROR", f"【内置网盘】阿里云盘目录读取失败: {msg}")
            return []
        self.cache.children.set(parent_id, items)
        return items

    def resolve_item(self, path):
        normalized = normalize_path(path)
        cached = self.cache.get_path(normalized)
        if cached:
            return cached
        parent_id = "root"
        current_path = "/"
        current_item = None
        for raw_part in normalized.strip("/").split("/"):
            part = unquote(raw_part)
            current_item = next((item for item in self.list_children(parent_id) if item.get("name") == part), None)
            if not current_item:
                return None
            current_path = join_path(current_path, part)
            self.cache.set_path(current_path, current_item)
            parent_id = current_item.get("file_id")
        return current_item

    def get_download_url(self, file_id):
        cached = self.download_cache.get(file_id)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_download_url(file_id))
        if not url:
            add_log("ERROR", f"【内置网盘】阿里云盘下载地址获取失败: {msg}")
        self.download_cache.set(file_id, url)
        return url

    def get_preview_url(self, file_id):
        cached = self.preview_cache.get(file_id)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_preview_url(file_id))
        if not url:
            add_log("WARNING", f"【内置网盘】阿里云盘转码播放地址获取失败: {msg}")
        self.preview_cache.set(file_id, url)
        return url

    def get_download_headers(self):
        return {
            "Referer": "https://www.alipan.com/",
            "Origin": "https://www.alipan.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    def get_size(self, item):
        return int(item.get("size") or 0)

    def get_name(self, item, fallback):
        return item.get("name") or fallback


class QuarkProvider:
    def __init__(self):
        self.cache = PathCache({"fid": "0", "file_name": "", "file_type": 0, "dir": True})
        self.download_cache = TimedCache(DOWNLOAD_URL_CACHE_TTL)

    def _drive(self):
        cookie = get_sys_config().get("cookie_quark", "").strip()
        return QuarkDrive(cookie) if cookie else None

    def list_children(self, parent_id):
        cached = self.cache.children.get(parent_id)
        if cached is not None:
            return cached
        drive = self._drive()
        if not drive:
            add_log("WARNING", "【内置网盘】未配置夸克 Cookie，quark 暂不可浏览。")
            return []
        items, msg = run_async(drive.list_files(parent_id))
        if msg != "success":
            add_log("ERROR", f"【内置网盘】夸克目录读取失败: {msg}")
            return []
        self.cache.children.set(parent_id, items)
        return items

    def resolve_item(self, path):
        normalized = normalize_path(path)
        cached = self.cache.get_path(normalized)
        if cached:
            return cached
        parent_id = "0"
        current_path = "/"
        current_item = None
        for raw_part in normalized.strip("/").split("/"):
            part = unquote(raw_part)
            current_item = next((item for item in self.list_children(parent_id) if self.get_name(item, "") == part), None)
            if not current_item:
                return None
            current_path = join_path(current_path, part)
            self.cache.set_path(current_path, current_item)
            parent_id = current_item.get("fid")
        return current_item

    def get_download_url(self, file_id):
        cached = self.download_cache.get(file_id)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_download_url(file_id))
        if not url:
            add_log("ERROR", f"【内置网盘】夸克下载地址获取失败: {msg}")
        self.download_cache.set(file_id, url)
        return url

    def get_download_headers(self):
        cookie = get_sys_config().get("cookie_quark", "").strip()
        return {
            "Cookie": cookie,
            "Referer": "https://pan.quark.cn",
            "Origin": "https://pan.quark.cn",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 "
                "Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch"
            ),
        }

    def get_size(self, item):
        return int(item.get("size") or 0)

    def get_name(self, item, fallback):
        return item.get("file_name") or item.get("name") or fallback


class Drive115Provider:
    def __init__(self):
        self.cache = PathCache({"cid": "0", "n": "", "is_dir": True})
        self.download_cache = TimedCache(DOWNLOAD_URL_CACHE_TTL)

    def _drive(self):
        cookie = get_sys_config().get("cookie_115", "").strip()
        return Drive115(cookie) if cookie else None

    def list_children(self, parent_id):
        cached = self.cache.children.get(parent_id)
        if cached is not None:
            return cached
        drive = self._drive()
        if not drive:
            add_log("WARNING", "【内置网盘】未配置 115 Cookie，115 暂不可浏览。")
            return []
        items, msg = run_async(drive.list_files(parent_id))
        if msg != "success":
            add_log("ERROR", f"【内置网盘】115 目录读取失败: {msg}")
            return []
        self.cache.children.set(parent_id, items)
        return items

    def resolve_item(self, path):
        normalized = normalize_path(path)
        cached = self.cache.get_path(normalized)
        if cached:
            return cached
        parent_id = "0"
        current_path = "/"
        current_item = None
        for raw_part in normalized.strip("/").split("/"):
            part = unquote(raw_part)
            current_item = next((item for item in self.list_children(parent_id) if self.get_name(item, "") == part), None)
            if not current_item:
                return None
            current_path = join_path(current_path, part)
            self.cache.set_path(current_path, current_item)
            parent_id = current_item.get("cid")
        return current_item

    def get_download_url(self, pickcode):
        cached = self.download_cache.get(pickcode)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_download_url(pickcode))
        if not url:
            add_log("ERROR", f"【内置网盘】115 下载地址获取失败: {msg}")
        self.download_cache.set(pickcode, url)
        return url

    def get_download_headers(self):
        return {"User-Agent": ""}

    def get_size(self, item):
        return int(item.get("s") or item.get("size") or 0)

    def get_name(self, item, fallback):
        return item.get("n") or item.get("fn") or item.get("name") or fallback


class Drive123Provider:
    def __init__(self):
        self.cache = PathCache({"fileId": "0", "filename": "", "type": 1})
        self.download_cache = TimedCache(DOWNLOAD_URL_CACHE_TTL)

    def _drive(self):
        cfg = get_sys_config()
        client_id = (cfg.get("drive123_client_id") or "").strip()
        client_secret = (cfg.get("drive123_client_secret") or "").strip()
        return Drive123Open(client_id, client_secret) if client_id and client_secret else None

    def list_children(self, parent_id):
        cached = self.cache.children.get(parent_id)
        if cached is not None:
            return cached
        drive = self._drive()
        if not drive:
            add_log("WARNING", "【内置网盘】未配置 123云盘 Client ID / Secret，123 暂不可浏览。")
            return []
        items, msg = run_async(drive.list_files(parent_id))
        if msg != "success":
            add_log("ERROR", f"【内置网盘】123云盘目录读取失败: {msg}")
            return []
        self.cache.children.set(parent_id, items)
        return items

    def resolve_item(self, path):
        normalized = normalize_path(path)
        cached = self.cache.get_path(normalized)
        if cached:
            return cached
        parent_id = "0"
        current_path = "/"
        current_item = None
        for raw_part in normalized.strip("/").split("/"):
            part = unquote(raw_part)
            current_item = next((item for item in self.list_children(parent_id) if self.get_name(item, "") == part), None)
            if not current_item:
                return None
            current_path = join_path(current_path, part)
            self.cache.set_path(current_path, current_item)
            parent_id = str(current_item.get("fileId"))
        return current_item

    def get_download_url(self, file_id):
        cached = self.download_cache.get(file_id)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_download_url(file_id))
        if not url:
            add_log("ERROR", f"【内置网盘】123云盘下载地址获取失败: {msg}")
        self.download_cache.set(file_id, url)
        return url

    def get_download_headers(self):
        return {"User-Agent": "Mozilla/5.0"}

    def get_size(self, item):
        return int(item.get("size") or 0)

    def get_name(self, item, fallback):
        return item.get("filename") or item.get("fileName") or fallback


INTERNAL_DRIVE_PROVIDERS = {
    "115": Drive115Provider(),
    "aliyun": AliyunProvider(),
    "quark": QuarkProvider(),
    "123": Drive123Provider(),
}


def guess_content_type(name):
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
