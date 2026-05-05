import asyncio
import mimetypes
import os
import threading
import time
from datetime import datetime, timezone
from socketserver import ThreadingMixIn
from urllib.parse import unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import requests
from wsgidav import util
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider
from wsgidav.wsgidav_app import WsgiDAVApp

from database import get_sys_config
from aliyun_drive_mobile import AliyunDrive
from drive_api import Drive115, QuarkDrive
from logger import add_log


WEBDAV_HOST = os.environ.get("CINELINK_WEBDAV_BIND_HOST", "127.0.0.1")

try:
    WEBDAV_PORT = int(os.environ.get("CINELINK_WEBDAV_PORT", "8088"))
except ValueError:
    WEBDAV_PORT = 8088

try:
    DOWNLOAD_URL_CACHE_TTL = int(os.environ.get("CINELINK_DOWNLOAD_URL_CACHE_TTL", "300"))
except ValueError:
    DOWNLOAD_URL_CACHE_TTL = 300


class DownloadUrlCache:
    def __init__(self, ttl=DOWNLOAD_URL_CACHE_TTL):
        self.ttl = ttl
        self.lock = threading.RLock()
        self.items = {}

    def get(self, key):
        with self.lock:
            cached = self.items.get(key)
            if not cached:
                return None
            expires_at, url = cached
            if expires_at < time.time():
                self.items.pop(key, None)
                return None
            return url

    def set(self, key, url):
        if not url:
            return
        with self.lock:
            self.items[key] = (time.time() + self.ttl, url)


class RemoteRangeStream:
    def __init__(self, url, headers=None, timeout=(10, 120)):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.position = 0
        self.response = None
        self.raw = None

    def seek(self, offset, whence=0):
        if whence == 0:
            self.position = max(0, int(offset))
        elif whence == 1:
            self.position = max(0, self.position + int(offset))
        else:
            raise OSError("RemoteRangeStream does not support seek from end")
        if self.response:
            self.close()
        return self.position

    def tell(self):
        return self.position

    def _open(self):
        if self.response:
            return
        headers = dict(self.headers)
        if self.position > 0:
            headers["Range"] = f"bytes={self.position}-"
        self.response = requests.get(self.url, headers=headers, stream=True, timeout=self.timeout)
        self.response.raise_for_status()
        if self.position > 0 and self.response.status_code != 206:
            self.response.close()
            raise RuntimeError("Remote server ignored Range request")
        self.raw = self.response.raw

    def read(self, size=-1):
        self._open()
        data = self.raw.read(size)
        self.position += len(data or b"")
        return data

    def close(self):
        if self.response:
            self.response.close()
        self.response = None
        self.raw = None


class ThreadingWsgiServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def run_async(coro):
    return asyncio.run(coro)


def parse_aliyun_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


class AliyunPathCache:
    def __init__(self, ttl=30):
        self.ttl = ttl
        self.lock = threading.RLock()
        self.children = {}
        self.path_items = {"/": {"file_id": "root", "name": "", "type": "folder"}}

    def get_children(self, parent_id):
        with self.lock:
            cached = self.children.get(parent_id)
            if not cached:
                return None
            expires_at, items = cached
            if expires_at < time.time():
                self.children.pop(parent_id, None)
                return None
            return items

    def set_children(self, parent_id, items):
        with self.lock:
            self.children[parent_id] = (time.time() + self.ttl, items)

    def get_path(self, path):
        with self.lock:
            return self.path_items.get(path)

    def set_path(self, path, item):
        with self.lock:
            self.path_items[path] = item

    def clear(self):
        with self.lock:
            self.children.clear()
            self.path_items = {"/": {"file_id": "root", "name": "", "type": "folder"}}


class AliyunWebDavProvider(DAVProvider):
    def __init__(self):
        super().__init__()
        self.cache = AliyunPathCache()
        self.download_cache = DownloadUrlCache()

    def is_readonly(self):
        return True

    def _drive(self):
        token = get_sys_config().get("token_aliyun", "").strip()
        if not token:
            return None
        return AliyunDrive(token)

    def list_children(self, parent_id):
        cached = self.cache.get_children(parent_id)
        if cached is not None:
            return cached

        drive = self._drive()
        if not drive:
            add_log("WARNING", "【内置WebDAV】未配置阿里云盘 Refresh Token，/aliyun 暂不可浏览。")
            return []

        items, msg = run_async(drive.list_files(parent_id))
        if msg != "success":
            add_log("ERROR", f"【内置WebDAV】阿里云盘目录读取失败: {msg}")
            return []
        self.cache.set_children(parent_id, items)
        return items

    def get_download_url(self, file_id):
        cached = self.download_cache.get(file_id)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_download_url(file_id))
        if not url:
            add_log("ERROR", f"【内置WebDAV】阿里云盘下载地址获取失败: {msg}")
        else:
            self.download_cache.set(file_id, url)
        return url

    def resolve_item(self, path):
        if not path or path == "/":
            return self.cache.get_path("/")

        normalized = "/" + "/".join([p for p in path.strip("/").split("/") if p])
        cached = self.cache.get_path(normalized)
        if cached:
            return cached

        parent_id = "root"
        current_path = ""
        current_item = None
        for raw_part in normalized.strip("/").split("/"):
            part = unquote(raw_part)
            children = self.list_children(parent_id)
            current_item = next((item for item in children if item.get("name") == part), None)
            if not current_item:
                return None
            current_path = util.join_uri(current_path or "/", part)
            self.cache.set_path(current_path, current_item)
            parent_id = current_item.get("file_id")
        return current_item

    def get_resource_inst(self, path, environ):
        item = self.resolve_item(path)
        if not item:
            return None
        if item.get("type") == "folder":
            return AliyunCollection(path or "/", environ, item)
        return AliyunFile(path, environ, item)


class AliyunCollection(DAVCollection):
    def __init__(self, path, environ, item):
        super().__init__(path, environ)
        self.item = item

    def get_member_names(self):
        items = self.provider.list_children(self.item.get("file_id", "root"))
        return [item.get("name", "") for item in items if item.get("name")]

    def get_member(self, name):
        child_path = util.join_uri(self.path, name)
        item = self.provider.resolve_item(child_path)
        if not item:
            return None
        if item.get("type") == "folder":
            return AliyunCollection(child_path, self.environ, item)
        return AliyunFile(child_path, self.environ, item)

    def get_creation_date(self):
        return parse_aliyun_time(self.item.get("created_at"))

    def get_last_modified(self):
        return parse_aliyun_time(self.item.get("updated_at"))

    def prevent_locking(self):
        return True


class AliyunFile(DAVNonCollection):
    def __init__(self, path, environ, item):
        super().__init__(path, environ)
        self.item = item
        self._response = None

    def get_content_length(self):
        return int(self.item.get("size") or 0)

    def get_content_type(self):
        mime, _ = mimetypes.guess_type(self.item.get("name") or self.path)
        return mime or "application/octet-stream"

    def get_creation_date(self):
        return parse_aliyun_time(self.item.get("created_at"))

    def get_last_modified(self):
        return parse_aliyun_time(self.item.get("updated_at"))

    def support_ranges(self):
        return True

    def support_etag(self):
        return True

    def get_etag(self):
        parts = [
            str(self.item.get("file_id") or ""),
            str(self.item.get("updated_at") or ""),
            str(self.item.get("size") or 0),
        ]
        return "-".join(parts)

    def get_content(self):
        url = self.provider.get_download_url(self.item.get("file_id"))
        if not url:
            raise RuntimeError("Unable to resolve Aliyun download URL")
        return RemoteRangeStream(url)

    def prevent_locking(self):
        return True


class QuarkPathCache:
    def __init__(self, ttl=30):
        self.ttl = ttl
        self.lock = threading.RLock()
        self.children = {}
        self.path_items = {"/": {"fid": "0", "file_name": "", "file_type": 0, "dir": True}}

    def get_children(self, parent_id):
        with self.lock:
            cached = self.children.get(parent_id)
            if not cached:
                return None
            expires_at, items = cached
            if expires_at < time.time():
                self.children.pop(parent_id, None)
                return None
            return items

    def set_children(self, parent_id, items):
        with self.lock:
            self.children[parent_id] = (time.time() + self.ttl, items)

    def get_path(self, path):
        with self.lock:
            return self.path_items.get(path)

    def set_path(self, path, item):
        with self.lock:
            self.path_items[path] = item


def quark_is_folder(item):
    return bool(item.get("dir")) or item.get("file_type") == 0


def quark_name(item):
    return item.get("file_name") or item.get("name") or ""


def parse_quark_time(value):
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value > 100000000000:
        value = value / 1000
    return value


class QuarkWebDavProvider(DAVProvider):
    def __init__(self):
        super().__init__()
        self.cache = QuarkPathCache()
        self.download_cache = DownloadUrlCache()

    def is_readonly(self):
        return True

    def _drive(self):
        cookie = get_sys_config().get("cookie_quark", "").strip()
        if not cookie:
            return None
        return QuarkDrive(cookie)

    def list_children(self, parent_id):
        cached = self.cache.get_children(parent_id)
        if cached is not None:
            return cached

        drive = self._drive()
        if not drive:
            add_log("WARNING", "【内置WebDAV】未配置夸克 Cookie，/quark 暂不可浏览。")
            return []

        items, msg = run_async(drive.list_files(parent_id))
        if msg != "success":
            add_log("ERROR", f"【内置WebDAV】夸克目录读取失败: {msg}")
            return []
        self.cache.set_children(parent_id, items)
        return items

    def get_download_url(self, file_id):
        cached = self.download_cache.get(file_id)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_download_url(file_id))
        if not url:
            add_log("ERROR", f"【内置WebDAV】夸克下载地址获取失败: {msg}")
        else:
            self.download_cache.set(file_id, url)
        return url

    def get_download_headers(self):
        cookie = get_sys_config().get("cookie_quark", "").strip()
        return {
            "Cookie": cookie,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 "
                "Electron/18.3.5.12-a038f7b798 Safari/537.36 Channel/pckk_other_ch"
            ),
            "Referer": "https://pan.quark.cn/",
            "Origin": "https://pan.quark.cn",
        }

    def resolve_item(self, path):
        if not path or path == "/":
            return self.cache.get_path("/")

        normalized = "/" + "/".join([p for p in path.strip("/").split("/") if p])
        cached = self.cache.get_path(normalized)
        if cached:
            return cached

        parent_id = "0"
        current_path = ""
        current_item = None
        for raw_part in normalized.strip("/").split("/"):
            part = unquote(raw_part)
            children = self.list_children(parent_id)
            current_item = next((item for item in children if quark_name(item) == part), None)
            if not current_item:
                return None
            current_path = util.join_uri(current_path or "/", part)
            self.cache.set_path(current_path, current_item)
            parent_id = current_item.get("fid")
        return current_item

    def get_resource_inst(self, path, environ):
        item = self.resolve_item(path)
        if not item:
            return None
        if quark_is_folder(item):
            return QuarkCollection(path or "/", environ, item)
        return QuarkFile(path, environ, item)


class QuarkCollection(DAVCollection):
    def __init__(self, path, environ, item):
        super().__init__(path, environ)
        self.item = item

    def get_member_names(self):
        items = self.provider.list_children(self.item.get("fid", "0"))
        return [quark_name(item) for item in items if quark_name(item)]

    def get_member(self, name):
        child_path = util.join_uri(self.path, name)
        item = self.provider.resolve_item(child_path)
        if not item:
            return None
        if quark_is_folder(item):
            return QuarkCollection(child_path, self.environ, item)
        return QuarkFile(child_path, self.environ, item)

    def get_creation_date(self):
        return parse_quark_time(self.item.get("created_at"))

    def get_last_modified(self):
        return parse_quark_time(self.item.get("updated_at"))

    def prevent_locking(self):
        return True


class QuarkFile(DAVNonCollection):
    def __init__(self, path, environ, item):
        super().__init__(path, environ)
        self.item = item
        self._response = None

    def get_content_length(self):
        return int(self.item.get("size") or 0)

    def get_content_type(self):
        mime, _ = mimetypes.guess_type(quark_name(self.item) or self.path)
        return mime or "application/octet-stream"

    def get_creation_date(self):
        return parse_quark_time(self.item.get("created_at"))

    def get_last_modified(self):
        return parse_quark_time(self.item.get("updated_at"))

    def support_ranges(self):
        return True

    def support_etag(self):
        return True

    def get_etag(self):
        parts = [
            str(self.item.get("fid") or ""),
            str(self.item.get("updated_at") or ""),
            str(self.item.get("size") or 0),
        ]
        return "-".join(parts)

    def get_content(self):
        url = self.provider.get_download_url(self.item.get("fid"))
        if not url:
            raise RuntimeError("Unable to resolve Quark download URL")
        return RemoteRangeStream(url, headers=self.provider.get_download_headers())

    def prevent_locking(self):
        return True


class Drive115PathCache:
    def __init__(self, ttl=30):
        self.ttl = ttl
        self.lock = threading.RLock()
        self.children = {}
        self.path_items = {"/": {"cid": "0", "n": "", "is_dir": True}}

    def get_children(self, parent_id):
        with self.lock:
            cached = self.children.get(parent_id)
            if not cached:
                return None
            expires_at, items = cached
            if expires_at < time.time():
                self.children.pop(parent_id, None)
                return None
            return items

    def set_children(self, parent_id, items):
        with self.lock:
            self.children[parent_id] = (time.time() + self.ttl, items)

    def get_path(self, path):
        with self.lock:
            return self.path_items.get(path)

    def set_path(self, path, item):
        with self.lock:
            self.path_items[path] = item


def drive115_is_folder(item):
    return bool(item.get("is_dir")) or (bool(item.get("cid")) and not item.get("fid"))


def drive115_name(item):
    return item.get("n") or item.get("fn") or item.get("name") or ""


def parse_115_time(value):
    if not value:
        return None
    value = str(value).strip()
    try:
        if value.isdigit():
            return int(value)
        return datetime.strptime(value, "%Y-%m-%d %H:%M").timestamp()
    except (TypeError, ValueError):
        return None


class Drive115WebDavProvider(DAVProvider):
    def __init__(self):
        super().__init__()
        self.cache = Drive115PathCache()
        self.download_cache = DownloadUrlCache()

    def is_readonly(self):
        return True

    def _drive(self):
        cookie = get_sys_config().get("cookie_115", "").strip()
        if not cookie:
            return None
        return Drive115(cookie)

    def list_children(self, parent_id):
        cached = self.cache.get_children(parent_id)
        if cached is not None:
            return cached

        drive = self._drive()
        if not drive:
            add_log("WARNING", "【内置WebDAV】未配置 115 Cookie，/115 暂不可浏览。")
            return []

        items, msg = run_async(drive.list_files(parent_id))
        if msg != "success":
            add_log("ERROR", f"【内置WebDAV】115 目录读取失败: {msg}")
            return []
        self.cache.set_children(parent_id, items)
        return items

    def get_download_url(self, pickcode):
        cached = self.download_cache.get(pickcode)
        if cached:
            return cached
        drive = self._drive()
        if not drive:
            return None
        url, msg = run_async(drive.get_download_url(pickcode))
        if not url:
            add_log("ERROR", f"【内置WebDAV】115 下载地址获取失败: {msg}")
        else:
            self.download_cache.set(pickcode, url)
        return url

    def get_download_headers(self):
        return {"User-Agent": ""}

    def resolve_item(self, path):
        if not path or path == "/":
            return self.cache.get_path("/")

        normalized = "/" + "/".join([p for p in path.strip("/").split("/") if p])
        cached = self.cache.get_path(normalized)
        if cached:
            return cached

        parent_id = "0"
        current_path = ""
        current_item = None
        for raw_part in normalized.strip("/").split("/"):
            part = unquote(raw_part)
            children = self.list_children(parent_id)
            current_item = next((item for item in children if drive115_name(item) == part), None)
            if not current_item:
                return None
            current_path = util.join_uri(current_path or "/", part)
            self.cache.set_path(current_path, current_item)
            parent_id = current_item.get("cid")
        return current_item

    def get_resource_inst(self, path, environ):
        item = self.resolve_item(path)
        if not item:
            return None
        if drive115_is_folder(item):
            return Drive115Collection(path or "/", environ, item)
        return Drive115File(path, environ, item)


class Drive115Collection(DAVCollection):
    def __init__(self, path, environ, item):
        super().__init__(path, environ)
        self.item = item

    def get_member_names(self):
        items = self.provider.list_children(self.item.get("cid", "0"))
        return [drive115_name(item) for item in items if drive115_name(item)]

    def get_member(self, name):
        child_path = util.join_uri(self.path, name)
        item = self.provider.resolve_item(child_path)
        if not item:
            return None
        if drive115_is_folder(item):
            return Drive115Collection(child_path, self.environ, item)
        return Drive115File(child_path, self.environ, item)

    def get_creation_date(self):
        return parse_115_time(self.item.get("tp") or self.item.get("t"))

    def get_last_modified(self):
        return parse_115_time(self.item.get("te") or self.item.get("tu") or self.item.get("t"))

    def prevent_locking(self):
        return True


class Drive115File(DAVNonCollection):
    def __init__(self, path, environ, item):
        super().__init__(path, environ)
        self.item = item
        self._response = None

    def get_content_length(self):
        return int(self.item.get("s") or self.item.get("size") or 0)

    def get_content_type(self):
        mime, _ = mimetypes.guess_type(drive115_name(self.item) or self.path)
        return mime or "application/octet-stream"

    def get_creation_date(self):
        return parse_115_time(self.item.get("tp") or self.item.get("t"))

    def get_last_modified(self):
        return parse_115_time(self.item.get("te") or self.item.get("tu") or self.item.get("t"))

    def support_ranges(self):
        return True

    def support_etag(self):
        return True

    def get_etag(self):
        parts = [
            str(self.item.get("fid") or ""),
            str(self.item.get("pc") or ""),
            str(self.item.get("te") or self.item.get("tu") or ""),
            str(self.item.get("s") or 0),
        ]
        return "-".join(parts)

    def get_content(self):
        url = self.provider.get_download_url(self.item.get("pc"))
        if not url:
            raise RuntimeError("Unable to resolve 115 download URL")
        return RemoteRangeStream(url, headers=self.provider.get_download_headers())

    def prevent_locking(self):
        return True


class InternalWebDavServer:
    def __init__(self, host=WEBDAV_HOST, port=WEBDAV_PORT):
        self.host = host
        self.port = port
        self.httpd = None
        self.thread = None

    def start(self):
        config = {
            "host": self.host,
            "port": self.port,
            "provider_mapping": {
                "/115": Drive115WebDavProvider(),
                "/aliyun": AliyunWebDavProvider(),
                "/quark": QuarkWebDavProvider(),
            },
            "http_authenticator": {
                "domain_controller": "wsgidav.dc.simple_dc.SimpleDomainController",
                "accept_basic": True,
                "accept_digest": True,
                "default_to_digest": False,
            },
            "simple_dc": {"user_mapping": {"*": True}},
            "dir_browser": {"enable": True, "response_trailer": False},
            "lock_storage": False,
            "verbose": 0,
        }
        app = WsgiDAVApp(config)
        WSGIRequestHandler.server_version = "CineLinkWebDAV/0.1"
        self.httpd = make_server(
            self.host,
            self.port,
            app,
            server_class=ThreadingWsgiServer,
            handler_class=WSGIRequestHandler,
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="CineLinkWebDAV", daemon=True)
        self.thread.start()
        add_log("INFO", f"【内置WebDAV】已启动: http://{self.host}:{self.port}/115、/aliyun、/quark")

    def stop(self):
        if not self.httpd:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        add_log("WARNING", "【内置WebDAV】服务已停止。")


_server = None


def start_internal_webdav():
    global _server
    if _server:
        return _server
    _server = InternalWebDavServer()
    try:
        _server.start()
    except OSError as e:
        add_log("ERROR", f"【内置WebDAV】启动失败，端口可能被占用: {e}")
        _server = None
    return _server


def stop_internal_webdav():
    global _server
    if _server:
        _server.stop()
        _server = None
