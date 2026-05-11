from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx


PANCHECK_PLATFORM_MAP = {
    "quark": "quark",
    "aliyun": "aliyun",
    "alipan": "aliyun",
    "115": "pan115",
    "pan115": "pan115",
}


def infer_pancheck_platform(drive_type: str = "", url: str = "") -> Optional[str]:
    raw_type = (drive_type or "").lower()
    link = (url or "").lower()
    if "quark" in raw_type or "pan.quark.cn" in link or "pan.qoark.cn" in link:
        return "quark"
    if (
        "aliyun" in raw_type
        or "alipan" in raw_type
        or "alipan.com" in link
        or "aliyundrive.com" in link
    ):
        return "aliyun"
    if raw_type in {"115", "pan115"} or "115.com/s/" in link or "115cdn.com/s/" in link:
        return "pan115"
    return None


def _clean_pwd(pwd: str = "") -> str:
    pwd = (pwd or "").strip()
    if pwd.lower() in {"none", "null", "no", "n/a", "na", "-"} or pwd in {"无", "暂无", "无提取码"}:
        return ""
    return pwd


def _append_query_param(url: str, key: str, value: str) -> str:
    if not value:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get(key):
        return url
    query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def normalize_link_for_check(url: str, platform: Optional[str], pwd: str = "") -> str:
    link = (url or "").strip()
    pwd = _clean_pwd(pwd)
    if not link:
        return link
    if platform == "quark":
        return _append_query_param(link, "pwd", pwd)
    if platform == "pan115":
        return _append_query_param(link, "password", pwd)
    return link


def _result(valid: Optional[bool], status: str, message: str, source: str, platform: Optional[str], **extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "valid": valid,
        "status": status,
        "message": message,
        "source": source,
        "platform": platform or "",
    }
    data.update(extra)
    return data


def _link_matches(target: str, links: Any) -> bool:
    if not isinstance(links, list):
        return False
    normalized_target = (target or "").strip().rstrip("/")
    return any(str(item).strip().rstrip("/") == normalized_target for item in links)


async def _check_with_pancheck(domain: str, url: str, platform: str) -> Optional[Dict[str, Any]]:
    if not domain:
        return None
    endpoint = f"{domain.rstrip('/')}/api/v1/links/check"
    payload = {"links": [url], "selected_platforms": [platform]}
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        res = await client.post(endpoint, json=payload)
        res.raise_for_status()
        data = res.json()

    if _link_matches(url, data.get("valid_links")):
        return _result(True, "valid", "链接有效", "pancheck", platform, raw=data)
    if _link_matches(url, data.get("invalid_links")):
        return _result(False, "invalid", "链接失效或提取码错误", "pancheck", platform, raw=data)
    if data.get("invalid_format_count", 0) > 0 and not data.get("pending_links") and not data.get("valid_links"):
        return _result(False, "invalid", "链接格式无法识别", "pancheck", platform, raw=data)
    if _link_matches(url, data.get("pending_links")):
        return _result(None, "pending", "PanCheck 暂未完成检测", "pancheck", platform, raw=data)
    return _result(None, "unknown", "PanCheck 未返回明确检测结果", "pancheck", platform, raw=data)


def _extract_share_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    return parts[-1]


async def _check_quark(url: str, pwd: str = "") -> Dict[str, Any]:
    pwd_id = _extract_share_id(url)
    if not pwd_id:
        return _result(False, "invalid", "无法解析夸克分享 ID", "internal", "quark")

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://pan.quark.cn",
        "referer": f"https://pan.quark.cn/s/{pwd_id}",
        "user-agent": "Mozilla/5.0",
    }
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        token_res = await client.post(
            "https://pan.quark.cn/1/clouddrive/share/sharepage/token",
            json={"pwd_id": pwd_id, "passcode": _clean_pwd(pwd)},
            headers=headers,
        )
        if token_res.status_code != 200:
            return _result(False, "invalid", f"夸克检测失败 HTTP {token_res.status_code}", "internal", "quark")
        token_data = token_res.json()
        stoken = token_data.get("data", {}).get("stoken")
        if not stoken:
            return _result(False, "invalid", token_data.get("message") or "夸克分享失效或提取码错误", "internal", "quark")

        detail_res = await client.get(
            "https://pan.quark.cn/1/clouddrive/share/sharepage/detail",
            params={"pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0"},
            headers=headers,
        )
        if detail_res.status_code != 200:
            return _result(False, "invalid", f"夸克详情检测失败 HTTP {detail_res.status_code}", "internal", "quark")
        detail = detail_res.json()
        files = detail.get("data", {}).get("list") or []
        if files:
            return _result(True, "valid", "链接有效", "internal", "quark")
        return _result(False, "invalid", detail.get("message") or "夸克分享为空或已失效", "internal", "quark")


async def _check_aliyun(url: str) -> Dict[str, Any]:
    share_id = _extract_share_id(url)
    if not share_id:
        return _result(False, "invalid", "无法解析阿里云盘分享 ID", "internal", "aliyun")
    headers = {
        "authorization": "",
        "content-type": "application/json",
        "origin": "https://www.alipan.com",
        "referer": "https://www.alipan.com/",
        "user-agent": "Mozilla/5.0",
        "x-canary": "client=web,app=share,version=v2.3.1",
    }
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        res = await client.post(
            f"https://api.aliyundrive.com/adrive/v3/share_link/get_share_by_anonymous?share_id={share_id}",
            json={"share_id": share_id},
            headers=headers,
        )
    if res.status_code == 429:
        return _result(None, "pending", "阿里云盘匿名接口限流，暂无法确认", "internal", "aliyun")
    if res.status_code != 200:
        return _result(False, "invalid", f"阿里云盘检测失败 HTTP {res.status_code}", "internal", "aliyun")
    data = res.json()
    if data.get("code"):
        return _result(False, "invalid", data.get("message") or data.get("code") or "阿里云盘分享失效", "internal", "aliyun")
    return _result(True, "valid", "链接有效", "internal", "aliyun")


async def _check_115(url: str, pwd: str = "") -> Dict[str, Any]:
    share_code = _extract_share_id(url)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    receive_code = _clean_pwd(pwd) or (query.get("password") or [""])[0] or (query.get("pwd") or [""])[0]
    if not share_code:
        return _result(False, "invalid", "无法解析 115 分享码", "internal", "pan115")
    if not receive_code:
        return _result(False, "invalid", "115 分享缺少提取码", "internal", "pan115")

    headers = {
        "referer": f"https://115cdn.com/s/{share_code}?password={receive_code}&",
        "user-agent": "Mozilla/5.0",
        "x-requested-with": "XMLHttpRequest",
    }
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        res = await client.get(
            "https://115cdn.com/webapi/share/snap",
            params={"share_code": share_code, "offset": 0, "limit": 20, "receive_code": receive_code, "cid": ""},
            headers=headers,
        )
    if res.status_code != 200:
        return _result(False, "invalid", f"115 检测失败 HTTP {res.status_code}", "internal", "pan115")
    data = res.json()
    if data.get("state") and data.get("errno") == 0:
        share_state = (data.get("data") or {}).get("share_state") or ((data.get("data") or {}).get("shareinfo") or {}).get("share_state")
        if share_state == 1:
            return _result(True, "valid", "链接有效", "internal", "pan115")
        reason = ((data.get("data") or {}).get("shareinfo") or {}).get("forbid_reason") or f"share_state={share_state}"
        return _result(False, "invalid", reason, "internal", "pan115")
    return _result(False, "invalid", data.get("error") or "115 分享失效或提取码错误", "internal", "pan115")


async def _check_internal(url: str, platform: str, pwd: str = "") -> Dict[str, Any]:
    try:
        if platform == "quark":
            return await _check_quark(url, pwd)
        if platform == "aliyun":
            return await _check_aliyun(url)
        if platform == "pan115":
            return await _check_115(url, pwd)
        return _result(None, "unsupported", "暂不支持该类型链接检测", "internal", platform)
    except Exception as exc:
        return _result(None, "unknown", f"内置检测异常: {exc}", "internal", platform)


async def check_link_validity(url: str, drive_type: str = "", pwd: str = "", config: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    config = config or {}
    platform = infer_pancheck_platform(drive_type, url)
    if not platform:
        return _result(None, "unsupported", "暂不支持该类型链接检测", "none", platform)

    normalized_url = normalize_link_for_check(url, platform, pwd)
    pancheck_domain = (config.get("pancheck_domain") or "").strip()

    if pancheck_domain:
        try:
            result = await _check_with_pancheck(pancheck_domain, normalized_url, platform)
            if result and result.get("status") in {"valid", "invalid"}:
                result["checked_url"] = normalized_url
                return result
        except Exception as exc:
            pancheck_error = str(exc)
        else:
            pancheck_error = ""
    else:
        pancheck_error = ""

    result = await _check_internal(url, platform, pwd)
    result["checked_url"] = normalized_url
    if pancheck_error and result.get("status") == "unknown":
        result["message"] = f"PanCheck 不可用: {pancheck_error}; {result.get('message')}"
    elif pancheck_error:
        result["pancheck_error"] = pancheck_error
    return result
