import os
from typing import Any

import httpx


DEFAULT_PANSOU_DOMAIN = os.environ.get("CINELINK_PANSOU_URL", "http://192.168.68.200:8080")


def resolve_pansou_domain(config: dict[str, Any] | None = None) -> str:
    config = config or {}
    domain = (config.get("pansou_domain") or os.environ.get("CINELINK_PANSOU_URL") or DEFAULT_PANSOU_DOMAIN or "").strip()
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

    normalized = {}
    for drive_type, items in merged.items():
        if isinstance(items, list):
            cleaned = [item for item in items if isinstance(item, dict) and item.get("url")]
            if cleaned:
                normalized[str(drive_type)] = cleaned

    if not normalized:
        rows = data.get("items") or data.get("list") or data.get("results") or raw.get("items") or raw.get("list") or []
        if isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                drive_type = str(item.get("type") or item.get("drive_type") or item.get("pan") or item.get("source_type") or "other")
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


async def search_pansou(keyword: str, config: dict[str, Any] | None = None, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    kw = (keyword or "").strip()
    domain = resolve_pansou_domain(config)
    if not kw:
        return {"ok": False, "message": "搜索关键词为空", "source": domain, "total": 0, "merged_by_type": {}}
    if not domain:
        return {"ok": False, "message": "未配置盘搜 API 接口地址", "source": "", "total": 0, "merged_by_type": {}}

    payload = {"kw": kw, "keyword": kw, "q": kw, "wd": kw}
    close_client = client is None
    active_client = client or httpx.AsyncClient(timeout=30.0)
    try:
        res = await active_client.post(f"{domain}/api/search", json=payload)
        res.raise_for_status()
        raw = res.json()
        normalized = normalize_pansou_response(raw)
        message = normalized.get("raw_message") or "success"
        return {
            "ok": True,
            "message": message,
            "source": domain,
            "total": normalized["total"],
            "merged_by_type": normalized["merged_by_type"],
            "raw": raw,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"无法连接盘搜接口: {exc}",
            "source": domain,
            "total": 0,
            "merged_by_type": {},
        }
    finally:
        if close_client:
            await active_client.aclose()
