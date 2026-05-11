import mimetypes
import os
import time
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from alist_integration import ALIST_BASE_URL, get_alist_admin_token
from internal_drives import INTERNAL_DRIVE_PROVIDERS
from logger import add_log


play_router = APIRouter()
PLAY_CHUNK_SIZE = int(os.environ.get("CINELINK_PLAY_CHUNK_SIZE", str(4 * 1024 * 1024)))
PLAY_CONNECT_TIMEOUT = float(os.environ.get("CINELINK_PLAY_CONNECT_TIMEOUT", "10"))
PLAY_READ_TIMEOUT = float(os.environ.get("CINELINK_PLAY_READ_TIMEOUT", "120"))
PLAY_LOG_REQUESTS = os.environ.get("CINELINK_PLAY_LOG_REQUESTS", "1").lower() not in {"0", "false", "no"}
PLAY_SESSION = requests.Session()
PLAY_ADAPTER = HTTPAdapter(pool_connections=32, pool_maxsize=128, max_retries=0)
PLAY_SESSION.mount("http://", PLAY_ADAPTER)
PLAY_SESSION.mount("https://", PLAY_ADAPTER)
PLAY_PROVIDERS = {
    "quark": INTERNAL_DRIVE_PROVIDERS["quark"],
    "aliyun": INTERNAL_DRIVE_PROVIDERS["aliyun"],
    "115": INTERNAL_DRIVE_PROVIDERS["115"],
}
ALIST_PUBLIC_URL = os.environ.get("CINELINK_ALIST_PUBLIC_URL", ALIST_BASE_URL).rstrip("/")
ALIST_SIGN_CACHE_TTL = int(os.environ.get("CINELINK_ALIST_SIGN_CACHE_TTL", "300"))
ALIST_SIGN_CACHE = {}


def get_alist_signed_url(path: str):
    normalized = "/" + "/".join([part for part in path.strip("/").split("/") if part])
    parent_path = "/" + "/".join(normalized.strip("/").split("/")[:-1])
    if parent_path == "/":
        parent_path = "/"
    file_name = normalized.strip("/").split("/")[-1]
    cache_key = normalized
    cached = ALIST_SIGN_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        sign = cached[1]
    else:
        sign = ""
        token = get_alist_admin_token()
        if token:
            try:
                res = PLAY_SESSION.post(
                    f"{ALIST_BASE_URL}/api/fs/list",
                    headers={"Authorization": token},
                    json={"path": parent_path, "page": 1, "per_page": 500, "refresh": False},
                    timeout=(PLAY_CONNECT_TIMEOUT, PLAY_READ_TIMEOUT),
                )
                data = res.json()
                if data.get("code") == 200:
                    item = next((x for x in data.get("data", {}).get("content") or [] if x.get("name") == file_name), None)
                    sign = item.get("sign", "") if item else ""
            except Exception as exc:
                add_log("WARNING", f"銆怉List 播放銆戣鍙栫鍚嶅け璐? {normalized} -> {exc}")
        ALIST_SIGN_CACHE[cache_key] = (time.time() + ALIST_SIGN_CACHE_TTL, sign)

    encoded_path = "/".join([quote(part) for part in normalized.strip("/").split("/") if part])
    url = f"{ALIST_PUBLIC_URL}/d/{encoded_path}"
    if sign:
        url += f"?sign={quote(sign)}"
    return url


@play_router.get("/play/aliyun_preview/{file_path:path}")
def play_aliyun_preview(file_path: str):
    path = "/" + (file_path or "").strip("/")
    provider = PLAY_PROVIDERS["aliyun"]
    item = provider.resolve_item(path)
    if not item:
        raise HTTPException(status_code=404, detail="Aliyun file not found")
    url = provider.get_preview_url(item.get("file_id"))
    if not url:
        raise HTTPException(status_code=502, detail="Unable to resolve Aliyun preview URL")
    return RedirectResponse(url=url, status_code=302)


@play_router.get("/play/quark_preview/{file_path:path}")
def play_quark_preview(file_path: str):
    return RedirectResponse(url=get_alist_signed_url(f"/quark/{file_path}"), status_code=302)


def get_play_target(drive_type, file_path, resolve_url=True):
    path = "/" + (file_path or "").strip("/")
    if drive_type == "quark":
        provider = PLAY_PROVIDERS["quark"]
        item = provider.resolve_item(path)
        if not item:
            raise HTTPException(status_code=404, detail="Quark file not found")
        url = provider.get_download_url(item.get("fid")) if resolve_url else None
        headers = provider.get_download_headers()
        size = provider.get_size(item)
        name = provider.get_name(item, path)
    elif drive_type == "aliyun":
        provider = PLAY_PROVIDERS["aliyun"]
        item = provider.resolve_item(path)
        if not item:
            raise HTTPException(status_code=404, detail="Aliyun file not found")
        url = provider.get_download_url(item.get("file_id")) if resolve_url else None
        headers = provider.get_download_headers()
        size = provider.get_size(item)
        name = provider.get_name(item, path)
    elif drive_type == "115":
        provider = PLAY_PROVIDERS["115"]
        item = provider.resolve_item(path)
        if not item:
            raise HTTPException(status_code=404, detail="115 file not found")
        url = provider.get_download_url(item.get("pc")) if resolve_url else None
        headers = provider.get_download_headers()
        size = provider.get_size(item)
        name = provider.get_name(item, path)
    else:
        raise HTTPException(status_code=404, detail="Unsupported drive type")

    if resolve_url and not url:
        raise HTTPException(status_code=502, detail="Unable to resolve download URL")
    return url, headers, size, name


@play_router.get("/play/{drive_type}/{file_path:path}")
def play_file(drive_type: str, file_path: str, request: Request):
    started_at = time.perf_counter()
    url, upstream_headers, size, name = get_play_target(drive_type, file_path)
    resolved_at = time.perf_counter()
    headers = dict(upstream_headers or {})
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    upstream = PLAY_SESSION.get(url, headers=headers, stream=True, timeout=(PLAY_CONNECT_TIMEOUT, PLAY_READ_TIMEOUT))
    upstream_at = time.perf_counter()
    try:
        upstream.raise_for_status()
    except Exception as exc:
        upstream.close()
        add_log("ERROR", f"【播放代理】{drive_type} 请求上游失败: {name} range={range_header or '-'} -> {exc}")
        raise HTTPException(status_code=502, detail=f"Upstream playback request failed: {exc}") from exc

    content_type = upstream.headers.get("Content-Type")
    if not content_type or content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

    response_headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "Cache-Control": "no-cache",
    }
    for key in ("Content-Length", "Content-Range", "ETag", "Last-Modified"):
        value = upstream.headers.get(key)
        if value:
            response_headers[key] = value
    if "Content-Length" not in response_headers and size:
        response_headers["Content-Length"] = str(size)
    if PLAY_LOG_REQUESTS:
        add_log(
            "INFO",
            (
                f"【播放代理】OPEN {drive_type} {upstream.status_code} {name} "
                f"range={range_header or '-'} content_length={response_headers.get('Content-Length', '-')} "
                f"resolve={resolved_at - started_at:.3f}s upstream_ttfb={upstream_at - resolved_at:.3f}s"
            ),
        )

    def body():
        total_bytes = 0
        try:
            while True:
                chunk = upstream.raw.read(PLAY_CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                yield chunk
        finally:
            upstream.close()
            if PLAY_LOG_REQUESTS:
                finished_at = time.perf_counter()
                elapsed = max(finished_at - started_at, 0.001)
                speed = total_bytes / elapsed / 1024 / 1024
                add_log(
                    "INFO",
                    (
                        f"【播放代理】{drive_type} {upstream.status_code} {name} "
                        f"range={range_header or '-'} bytes={total_bytes} "
                        f"resolve={resolved_at - started_at:.3f}s "
                        f"upstream_ttfb={upstream_at - resolved_at:.3f}s "
                        f"total={finished_at - started_at:.3f}s speed={speed:.2f}MB/s"
                    ),
                )

    return StreamingResponse(body(), status_code=upstream.status_code, headers=response_headers, media_type=content_type)


@play_router.head("/play/{drive_type}/{file_path:path}")
def play_file_head(drive_type: str, file_path: str):
    started_at = time.perf_counter()
    _url, _headers, size, name = get_play_target(drive_type, file_path, resolve_url=False)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "Content-Length": str(size),
        "Cache-Control": "no-cache",
    }
    if PLAY_LOG_REQUESTS:
        add_log("INFO", f"【播放代理】HEAD {drive_type} {name} size={size} resolve={time.perf_counter() - started_at:.3f}s")
    return Response(status_code=200, headers=headers)
