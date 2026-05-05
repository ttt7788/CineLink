import mimetypes

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from internal_webdav import AliyunWebDavProvider, Drive115WebDavProvider, QuarkWebDavProvider


play_router = APIRouter()
PLAY_PROVIDERS = {
    "quark": QuarkWebDavProvider(),
    "aliyun": AliyunWebDavProvider(),
    "115": Drive115WebDavProvider(),
}


def get_play_target(drive_type, file_path):
    path = "/" + (file_path or "").strip("/")
    if drive_type == "quark":
        provider = PLAY_PROVIDERS["quark"]
        item = provider.resolve_item(path)
        if not item:
            raise HTTPException(status_code=404, detail="Quark file not found")
        url = provider.get_download_url(item.get("fid"))
        headers = provider.get_download_headers()
        size = int(item.get("size") or 0)
        name = item.get("file_name") or item.get("name") or path
    elif drive_type == "aliyun":
        provider = PLAY_PROVIDERS["aliyun"]
        item = provider.resolve_item(path)
        if not item:
            raise HTTPException(status_code=404, detail="Aliyun file not found")
        url = provider.get_download_url(item.get("file_id"))
        headers = {}
        size = int(item.get("size") or 0)
        name = item.get("name") or path
    elif drive_type == "115":
        provider = PLAY_PROVIDERS["115"]
        item = provider.resolve_item(path)
        if not item:
            raise HTTPException(status_code=404, detail="115 file not found")
        url = provider.get_download_url(item.get("pc"))
        headers = provider.get_download_headers()
        size = int(item.get("s") or item.get("size") or 0)
        name = item.get("n") or item.get("fn") or item.get("name") or path
    else:
        raise HTTPException(status_code=404, detail="Unsupported drive type")

    if not url:
        raise HTTPException(status_code=502, detail="Unable to resolve download URL")
    return url, headers, size, name


@play_router.get("/play/{drive_type}/{file_path:path}")
def play_file(drive_type: str, file_path: str, request: Request):
    url, upstream_headers, size, name = get_play_target(drive_type, file_path)
    headers = dict(upstream_headers or {})
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    upstream = requests.get(url, headers=headers, stream=True, timeout=(10, 120))
    try:
        upstream.raise_for_status()
    except Exception as exc:
        upstream.close()
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

    def body():
        try:
            while True:
                chunk = upstream.raw.read(1024 * 1024)
                if not chunk:
                    break
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=response_headers, media_type=content_type)


@play_router.head("/play/{drive_type}/{file_path:path}")
def play_file_head(drive_type: str, file_path: str):
    _url, _headers, size, name = get_play_target(drive_type, file_path)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "Content-Length": str(size),
        "Cache-Control": "no-cache",
    }
    return Response(status_code=200, headers=headers)
