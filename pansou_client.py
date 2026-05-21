import json
import os
import re
from typing import Any

import httpx


DEFAULT_PANSOU_DOMAIN = os.environ.get("CINELINK_PANSOU_URL", "")
PANSOU_TIMEOUT = httpx.Timeout(90.0, connect=15.0, read=90.0, write=30.0, pool=15.0)


def resolve_pansou_domain(config: dict[str, Any] | None = None) -> str:
    config = config or {}
    domain = (
        config.get("pansou_domain")
        or os.environ.get("CINELINK_PANSOU_URL")
        or DEFAULT_PANSOU_DOMAIN
        or ""
    ).strip()
    if not domain:
        return ""
    if not domain.startswith(("http://", "https://")):
        domain = f"http://{domain}"
    return domain.rstrip("/")


def normalize_pansou_response(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"total": 0, "merged_by_type": {}, "raw": raw}

    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    merged = (
        data.get("merged_by_type")
        or data.get("mergedByType")
        or raw.get("merged_by_type")
        or raw.get("mergedByType")
        or {}
    )

    if not isinstance(merged, dict):
        merged = {}

    normalized: dict[str, list[dict[str, Any]]] = {}
    for drive_type, items in merged.items():
        if isinstance(items, list):
            cleaned = [item for item in items if isinstance(item, dict) and item.get("url")]
            if cleaned:
                normalized[str(drive_type)] = cleaned

    if not normalized:
        rows = (
            data.get("items")
            or data.get("list")
            or data.get("results")
            or raw.get("items")
            or raw.get("list")
            or []
        )
        if isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                drive_type = str(
                    item.get("type")
                    or item.get("drive_type")
                    or item.get("pan")
                    or item.get("source_type")
                    or "other"
                )
                normalized.setdefault(drive_type, []).append(item)

    total = data.get("total")
    if not isinstance(total, int):
        total = sum(len(items) for items in normalized.values())

    return {
        "total": total,
        "merged_by_type": normalized,
        "raw_code": raw.get("code"),
        "raw_message": raw.get("message") or raw.get("msg") or "",
    }


def _format_request_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return f"{exc.__class__.__name__}: 请求超时"
    if isinstance(exc, httpx.ConnectError):
        return f"{exc.__class__.__name__}: 无法连接"
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "").strip().replace("\n", " ")[:160]
        return f"HTTP {exc.response.status_code}" + (f" - {body}" if body else "")
    if isinstance(exc, json.JSONDecodeError):
        return "JSONDecodeError: 接口返回不是 JSON"
    return f"{exc.__class__.__name__}: {exc or repr(exc)}"


def _compact_text(text: str) -> str:
    return re.sub(r"[\s·:：\-—_《》【】\[\]（）()，,。.!！?？/\\]+", "", text or "").lower()


def _filter_normalized_by_keyword(normalized: dict[str, Any], keyword: str) -> dict[str, Any]:
    compact_keyword = _compact_text(keyword)
    parts = [
        _compact_text(part)
        for part in re.split(r"[\s·:：\-—_《》【】\[\]（）()，,。.!！?？/\\]+", keyword or "")
        if len(_compact_text(part)) >= 2
    ]
    terms = [term for term in [compact_keyword, *parts] if term]
    if not terms:
        return normalized

    filtered: dict[str, list[dict[str, Any]]] = {}
    for drive_type, items in (normalized.get("merged_by_type") or {}).items():
        for item in items:
            haystack = _compact_text(
                " ".join(
                    str(item.get(key, ""))
                    for key in ("note", "title", "content", "work_title", "source", "url")
                )
            )
            if any(term in haystack for term in terms):
                filtered.setdefault(drive_type, []).append(item)

    return {
        **normalized,
        "total": sum(len(items) for items in filtered.values()),
        "merged_by_type": filtered,
    }


async def _check_health(client: httpx.AsyncClient, domain: str) -> str:
    try:
        res = await client.get(f"{domain}/api/health", timeout=httpx.Timeout(8.0, connect=3.0))
        return f"健康检查 HTTP {res.status_code}"
    except Exception as exc:
        return f"健康检查失败: {_format_request_error(exc)}"


async def search_pansou(
    keyword: str,
    config: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    kw = (keyword or "").strip()
    domain = resolve_pansou_domain(config)
    if not kw:
        return {"ok": False, "message": "搜索关键词为空", "source": domain, "total": 0, "merged_by_type": {}}
    if not domain:
        return {"ok": False, "message": "未配置盘搜 API 接口地址", "source": "", "total": 0, "merged_by_type": {}}

    close_client = client is None
    active_client = client or httpx.AsyncClient(timeout=PANSOU_TIMEOUT)
    attempts = [
        ("POST", "/api/search", {"json": {"kw": kw, "res": "merge", "src": "all"}}),
        ("GET", "/api/search", {"params": {"kw": kw, "res": "merge", "src": "all"}}),
    ]
    best_result: dict[str, Any] | None = None
    errors: list[str] = []
    try:
        for method, path, kwargs in attempts:
            try:
                if method == "POST":
                    res = await active_client.post(f"{domain}{path}", timeout=PANSOU_TIMEOUT, **kwargs)
                else:
                    res = await active_client.get(f"{domain}{path}", timeout=PANSOU_TIMEOUT, **kwargs)
                res.raise_for_status()
                raw = res.json()
                normalized = normalize_pansou_response(raw)
                message = normalized.get("raw_message") or "success"
                current = {
                    "ok": True,
                    "message": message,
                    "source": domain,
                    "total": normalized["total"],
                    "merged_by_type": normalized["merged_by_type"],
                    "raw": raw,
                    "request_mode": f"{method} {path} kw",
                }
                if normalized["total"] > 0 or normalized["merged_by_type"]:
                    return current
                if best_result is None:
                    best_result = current
            except Exception as exc:
                errors.append(f"{method} {path}: {_format_request_error(exc)}")

        if best_result is not None:
            fallback_payload = {"keyword": kw, "res": "merge", "src": "all"}
            try:
                res = await active_client.post(f"{domain}/api/search", timeout=PANSOU_TIMEOUT, json=fallback_payload)
                res.raise_for_status()
                raw = res.json()
                normalized = _filter_normalized_by_keyword(normalize_pansou_response(raw), kw)
                if normalized["total"] > 0 or normalized["merged_by_type"]:
                    return {
                        "ok": True,
                        "message": "success，已使用兼容参数并按关键词过滤",
                        "source": domain,
                        "total": normalized["total"],
                        "merged_by_type": normalized["merged_by_type"],
                        "raw": raw,
                        "request_mode": "POST /api/search keyword-filtered",
                    }
            except Exception as exc:
                errors.append(f"POST /api/search keyword-filtered: {_format_request_error(exc)}")
            return best_result

        health_msg = await _check_health(active_client, domain)
        raise RuntimeError("; ".join(errors) if errors else "all request attempts failed")
    except Exception as exc:
        extra = f"；{health_msg}" if "health_msg" in locals() else ""
        return {
            "ok": False,
            "message": f"无法连接盘搜接口: {exc}{extra}",
            "source": domain,
            "total": 0,
            "merged_by_type": {},
        }
    finally:
        if close_client:
            await active_client.aclose()
