import asyncio
import json
import os
import re
from string import Formatter

import httpx

from aliyun_drive_mobile import AliyunDrive
from config_guard import require_drive_ready
from database import get_db, get_sys_config
from drive_api import Drive115, Drive123Open, QuarkDrive, _safe_json
from logger import add_log


CONFIG_KEY = "drive_organizer_config"
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".ts", ".m2ts", ".rmvb", ".iso", ".vob", ".webm"}

DEFAULT_CATEGORY_STRATEGY = """# 二级分类策略
# 目录字段说明：
# cid115      -> 115 网盘目录 ID
# cid_quark  -> 夸克网盘目录 ID
# cid_aliyun -> 阿里云盘目录 ID
# cid123     -> 123 云盘目录 ID
# target_id  -> 通用自定义目录 ID，未配置单盘字段时使用
# 旧字段 cid / cid123 仍然兼容。你可以直接新增自定义分类块。
movie:
  动画电影:
    cid115: 3372304203423464322
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    genre_ids: "16"
  华语电影:
    cid115: 3372304442104528071
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    origin_country: "CN,TW,HK"
  外语电影:
    cid115: 3372304575416286707
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
tv:
  儿童:
    cid115: 3373000227542793309
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    genre_ids: "10762"
  国产:
    cid115: 3373000056029314653
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    genre_ids: "16"
    origin_country: "CN,TW,HK"
  日本:
    cid115: 337299994217786040
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    genre_ids: "16"
    origin_country: "JP"
  纪录片:
    cid115: 3372805994525490303
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    genre_ids: "99"
  综艺:
    cid115: 3372999522932276366
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    genre_ids: "10764,10767"
  欧美剧:
    cid115: 3372805835631913238
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    origin_country: "US,FR,GB,DE,ES,IT,NL,PT,RU,UK"
  日韩剧:
    cid115: 3372805730371541444
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456
    origin_country: "JP,KP,KR,TH,IN,SG"
  其它:
    cid115: 1000000000000000012
    cid_quark: 0
    cid_aliyun: root
    cid123: 123456"""

DEFAULT_WASH_STRATEGY = """电影同质优先策略:
  mode: replace
  media_type: movie
  priority_level:
    - resource_pix: "2160p,4k"
      resource_type: "BluRay"
    - resource_pix: "1080p"
      resource_type: "BluRay"
    - resource_pix: "2160p,4k"
      resource_type: "WEB-DL"
    - resource_pix: "1080p"

剧集同质优先策略:
  mode: replace
  media_type: tv
  priority_level:
    - resource_pix: "2160p,4k"
      resource_type: "BluRay"
    - resource_pix: "1080p"
      resource_type: "BluRay"
    - resource_pix: "2160p,4k"
      resource_type: "WEB-DL"
    - resource_pix: "1080p"

全局兜底策略:
  mode: max_size"""

DEFAULT_CONFIG = {
    "drive_type": "quark",
    "source_dir": "0",
    "movie_dir": "0",
    "tv_dir": "0",
    "max_items": 30,
    "max_depth": 2,
    "recursive": True,
    "dry_run": True,
    "movie_folder_rule": "{first_letter}-{title}-{year}",
    "movie_file_rule": "{title}.{year}.{resource_pix}.{resource_source}.{video_encode}{ext}",
    "tv_folder_rule": "{first_letter}-{title}-{year}",
    "season_folder_rule": "Season {season_num:02d}",
    "episode_file_rule": "{title}.{year}.{season_episode}.{resource_pix}.{resource_source}.{video_encode}{ext}",
    "category_strategy": DEFAULT_CATEGORY_STRATEGY,
    "wash_strategy": DEFAULT_WASH_STRATEGY,
}

DEFAULT_ROOTS = {
    "quark": "0",
    "aliyun": "root",
    "115": "0",
    "123": "0",
}

CATEGORY_TARGET_KEYS = {
    "cid",
    "cid115",
    "cid_115",
    "id_115",
    "folder_id_115",
    "target_115",
    "cid_quark",
    "quark_cid",
    "fid_quark",
    "folder_id_quark",
    "target_quark",
    "quark",
    "cid_aliyun",
    "aliyun_cid",
    "file_id_aliyun",
    "folder_id_aliyun",
    "target_aliyun",
    "aliyun",
    "parent_file_id",
    "cid123",
    "cid_123",
    "file_id_123",
    "folder_id_123",
    "target_123",
    "drive123",
    "target_id",
    "folder_id",
    "dir_id",
    "cid_custom",
    "custom_cid",
}
CATEGORY_META_KEYS = CATEGORY_TARGET_KEYS | {"desc", "description", "remark", "remarks", "note", "label", "name"}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_id(value, default="0"):
    value = str(value or "").split("-")[0].strip()
    return value or default


def _clean_target_id(value, default=""):
    value = str(value or "").strip()
    if not value:
        return default
    if "-" in value:
        prefix = value.split("-", 1)[0].strip()
        if prefix == "root" or prefix.isdigit() or re.fullmatch(r"[a-fA-F0-9]{16,}", prefix):
            return prefix
    return value


def _normalize_drive_type(drive_type):
    value = str(drive_type or "quark").replace("_internal", "").strip().lower()
    aliases = {
        "115pan": "115",
        "drive115": "115",
        "123pan": "123",
        "drive123": "123",
        "aliyundrive": "aliyun",
        "alipan": "aliyun",
        "quarkdrive": "quark",
    }
    return aliases.get(value, value)


def _strip_quotes(value):
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return value[1:-1]
    return value


def _parse_key_value(line):
    if ":" not in line:
        return "", ""
    key, value = line.split(":", 1)
    return key.strip(), _strip_quotes(value.split("#", 1)[0].strip())


def _parse_category_strategy(text):
    result = {}
    current_media = ""
    current_category = ""
    for raw in str(text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith(":"):
            current_media = line[:-1].strip()
            result.setdefault(current_media, {})
            current_category = ""
        elif indent == 2 and line.endswith(":") and current_media:
            current_category = line[:-1].strip()
            result[current_media].setdefault(current_category, {})
        elif indent >= 4 and current_media and current_category:
            key, value = _parse_key_value(line)
            if key:
                result[current_media][current_category][key] = value
    return result


def _parse_wash_strategy(text):
    result = {"sections": [], "global_mode": "max_size"}
    current = None
    in_priority = False
    current_priority = None
    for raw in str(text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith(":"):
            current = {"name": line[:-1].strip(), "priority_level": []}
            result["sections"].append(current)
            in_priority = False
            current_priority = None
            continue
        if not current:
            continue
        if indent == 2:
            key, value = _parse_key_value(line)
            if key == "priority_level":
                in_priority = True
            elif key:
                current[key] = value
                if key == "mode" and any(token in current.get("name", "") for token in ("兜底", "全局", "鍏滃簳")):
                    result["global_mode"] = value or result["global_mode"]
        elif in_priority and indent >= 4:
            if line.startswith("- "):
                key, value = _parse_key_value(line[2:].strip())
                current_priority = {}
                if key:
                    current_priority[key] = value
                current["priority_level"].append(current_priority)
            elif current_priority is not None:
                key, value = _parse_key_value(line)
                if key:
                    current_priority[key] = value
    return result


def _split_csv(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _norm_token(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _is_default_root(drive_type, value):
    drive_type = _normalize_drive_type(drive_type)
    default_root = DEFAULT_ROOTS.get(drive_type, "0")
    return _clean_id(value, default_root) == default_root


def _category_target_id(rule, drive_type):
    drive_type = _normalize_drive_type(drive_type)
    safe_drive_key = re.sub(r"[^a-z0-9]+", "_", drive_type).strip("_")
    dynamic_keys = tuple(
        key for key in (
            f"cid_{safe_drive_key}",
            f"{safe_drive_key}_cid",
            f"folder_id_{safe_drive_key}",
            f"target_{safe_drive_key}",
            f"{safe_drive_key}_folder_id",
        )
        if key
    )
    drive_keys = {
        "115": ("cid115", "cid_115", "id_115", "folder_id_115", "target_115"),
        "quark": ("cid_quark", "quark_cid", "fid_quark", "folder_id_quark", "target_quark", "quark"),
        "aliyun": ("cid_aliyun", "aliyun_cid", "file_id_aliyun", "folder_id_aliyun", "target_aliyun", "aliyun", "parent_file_id"),
        "123": ("cid123", "cid_123", "file_id_123", "folder_id_123", "target_123", "drive123"),
    }.get(drive_type, dynamic_keys)
    legacy_keys = ("cid123", "cid_123", "cid") if drive_type == "123" else ("cid",)
    common_keys = ("target_id", "folder_id", "dir_id", "cid_custom", "custom_cid")
    for key in (*drive_keys, *legacy_keys, *common_keys):
        target_id = _clean_target_id((rule or {}).get(key), "")
        if target_id:
            return target_id
    return ""


def _is_category_meta_key(key):
    key = str(key or "").strip()
    return (
        key in CATEGORY_META_KEYS
        or key.startswith("cid_")
        or key.endswith("_cid")
        or key.startswith("folder_id_")
        or key.startswith("target_")
        or key.endswith("_folder_id")
    )


def _looks_like_legacy_mojibake(text):
    value = str(text or "")
    return any(token in value for token in ("浜岀骇", "鐢靛奖", "鍚岃川", "鍏滃簳", "绛栫暐"))


def get_organizer_config():
    conn = get_db()
    row = conn.execute("SELECT config_value FROM system_configs WHERE config_key=?", (CONFIG_KEY,)).fetchone()
    conn.close()
    data = {}
    if row and row["config_value"]:
        try:
            data = json.loads(row["config_value"])
        except Exception:
            data = {}
    config = {**DEFAULT_CONFIG, **data}
    config["max_items"] = min(max(_int(config.get("max_items"), 30), 1), 200)
    config["max_depth"] = min(max(_int(config.get("max_depth"), 2), 0), 8)
    config["recursive"] = _bool(config.get("recursive"))
    config["dry_run"] = _bool(config.get("dry_run"))
    if _looks_like_legacy_mojibake(config.get("category_strategy")):
        config["category_strategy"] = DEFAULT_CATEGORY_STRATEGY
    if _looks_like_legacy_mojibake(config.get("wash_strategy")):
        config["wash_strategy"] = DEFAULT_WASH_STRATEGY
    return config


def save_organizer_config(config):
    clean = {**DEFAULT_CONFIG, **(config or {})}
    clean["max_items"] = min(max(_int(clean.get("max_items"), 30), 1), 200)
    clean["max_depth"] = min(max(_int(clean.get("max_depth"), 2), 0), 8)
    clean["recursive"] = _bool(clean.get("recursive"))
    clean["dry_run"] = _bool(clean.get("dry_run"))
    if _looks_like_legacy_mojibake(clean.get("category_strategy")):
        clean["category_strategy"] = DEFAULT_CATEGORY_STRATEGY
    if _looks_like_legacy_mojibake(clean.get("wash_strategy")):
        clean["wash_strategy"] = DEFAULT_WASH_STRATEGY
    conn = get_db()
    conn.execute(
        "REPLACE INTO system_configs (config_key, config_value) VALUES (?, ?)",
        (CONFIG_KEY, json.dumps(clean, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return clean


def _client_for(drive_type, sys_config):
    drive_type = _normalize_drive_type(drive_type)
    ready, msg = require_drive_ready(drive_type, sys_config)
    if not ready:
        raise RuntimeError(msg)
    if drive_type == "quark":
        return QuarkDrive(sys_config.get("cookie_quark", "")), "0"
    if drive_type == "aliyun":
        return AliyunDrive(sys_config.get("token_aliyun", "")), "root"
    if drive_type == "123":
        return Drive123Open(sys_config.get("drive123_client_id", ""), sys_config.get("drive123_client_secret", "")), "0"
    return Drive115(sys_config.get("cookie_115", "")), "0"


def _normalize_item(drive_type, item):
    if drive_type == "quark":
        return {
            "id": str(item.get("fid") or ""),
            "name": item.get("file_name") or "",
            "is_folder": item.get("file_type") == 0,
            "size": int(item.get("size") or 0),
        }
    if drive_type == "aliyun":
        return {
            "id": str(item.get("file_id") or ""),
            "name": item.get("name") or "",
            "is_folder": item.get("type") == "folder",
            "size": int(item.get("size") or 0),
        }
    if drive_type == "123":
        return {
            "id": str(item.get("fileId") or ""),
            "name": item.get("filename") or item.get("fileName") or "",
            "is_folder": int(item.get("type") or 0) == 1,
            "size": int(item.get("size") or 0),
        }
    is_folder = bool(item.get("cid")) and not item.get("fid")
    return {
        "id": str(item.get("cid") if is_folder else item.get("fid") or ""),
        "name": item.get("n") or item.get("fn") or item.get("name") or "",
        "is_folder": is_folder,
        "size": int(item.get("s") or 0),
    }


async def _list_normalized(client, drive_type, parent_id):
    items, msg = await client.list_files(parent_id)
    if msg != "success" and not items:
        raise RuntimeError(msg)
    return [_normalize_item(drive_type, item) for item in items if _normalize_item(drive_type, item)["id"]]


async def _scan_files(client, drive_type, parent_id, max_items, recursive, max_depth):
    pending = [(_clean_id(parent_id, "root" if drive_type == "aliyun" else "0"), "", 0)]
    result = []
    visited = set()
    while pending and len(result) < max_items:
        folder_id, folder_path, depth = pending.pop(0)
        if folder_id in visited:
            continue
        visited.add(folder_id)
        for item in await _list_normalized(client, drive_type, folder_id):
            item["parent_id"] = folder_id
            item["path"] = f"{folder_path}/{item['name']}".strip("/")
            if item["is_folder"]:
                if recursive and depth < max_depth:
                    pending.append((item["id"], item["path"], depth + 1))
                continue
            if os.path.splitext(item["name"])[1].lower() in VIDEO_EXTS:
                result.append(item)
                if len(result) >= max_items:
                    break
    return result


def parse_media_name(name):
    ext = os.path.splitext(name)[1]
    stem = os.path.splitext(name)[0]
    compact = re.sub(r"[\[\]【】()（）]", " ", stem)
    year_match = re.search(r"(19|20)\d{2}", compact)
    season_ep = re.search(r"[Ss](\d{1,2})[ ._-]*[Ee](\d{1,3})", compact)
    season_cn = re.search(r"第\s*(\d{1,2})\s*[季部]", compact)
    episode_cn = re.search(r"第\s*(\d{1,3})\s*[集话話]", compact)
    quality = _first_match(compact, [r"2160p|4k", r"1080p", r"720p", r"480p"])
    source = _first_match(compact, [r"blu-?ray|bdrip|remux", r"web-?dl", r"webrip", r"hdtv", r"hdrip"])
    effect = _first_match(compact, [r"dv\.?hdr|dolby[ ._-]?vision", r"hdr10\+?", r"hdr", r"sdr"])
    version = _first_match(compact, [r"imax", r"hq", r"3d", r"\bcc\b", r"\bdc\b"])
    fps = _first_match(compact, [r"60fps", r"50fps", r"30fps", r"24fps"])
    codec = _first_match(compact, [r"h\.?265|hevc|x265", r"h\.?264|x264", r"av1"])
    title = compact
    for pattern in [
        r"(19|20)\d{2}",
        r"[Ss]\d{1,2}[ ._-]*[Ee]\d{1,3}",
        r"第\s*\d{1,2}\s*[季部]",
        r"第\s*\d{1,3}\s*[集话話]",
        r"2160p|4k|1080p|720p|480p|blu-?ray|bdrip|remux|web-?dl|webrip|hdtv|hdrip|h\.?265|hevc|x265|h\.?264|x264|av1",
    ]:
        title = re.sub(pattern, " ", title, flags=re.IGNORECASE)
    title = re.sub(r"[._\-]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip() or stem[:80]
    season_num = int(season_ep.group(1)) if season_ep else int(season_cn.group(1)) if season_cn else 1
    episode_num = int(season_ep.group(2)) if season_ep else int(episode_cn.group(1)) if episode_cn else 0
    media_type = "tv" if season_ep or season_cn or episode_cn else "movie"
    first_letter = (title[:1] or "#").upper()
    season_episode = f"S{season_num:02d}E{episode_num:02d}" if episode_num else f"S{season_num:02d}"
    return {
        "original_name": name,
        "ext": ext,
        "title": title,
        "year": year_match.group(0) if year_match else "",
        "tmdb_id": "",
        "en_title": "",
        "first_letter": first_letter,
        "media_type": media_type,
        "resource_pix": quality,
        "resource_source": source,
        "video_encode": codec,
        "resource_version": version,
        "resource_type": source,
        "resource_effect": effect,
        "audio_encode": "",
        "resource_team": "",
        "fps": fps,
        "genre_ids": "",
        "origin_country": "",
        "disc_num": "",
        "season_name": "",
        "season_year": "",
        "episode_name": "",
        "custom_regex_match": "",
        "season_episode": season_episode,
        "season_num": season_num,
        "episode_num": episode_num,
    }


def _first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).replace("-", "").upper()
    return ""


def _condition_matches(variables, key, expected):
    expected_values = [_norm_token(v) for v in _split_csv(expected)]
    if not expected_values:
        return True
    actual_values = [_norm_token(v) for v in _split_csv(variables.get(key, ""))]
    if not actual_values:
        return False
    return bool(set(expected_values) & set(actual_values))


def _category_for_variables(variables, config, drive_type):
    strategy = _parse_category_strategy(config.get("category_strategy"))
    media_rules = strategy.get(variables.get("media_type")) or {}
    fallback = None
    for name, rule in media_rules.items():
        target_id = _category_target_id(rule, drive_type)
        conditions = {k: v for k, v in rule.items() if not _is_category_meta_key(k)}
        if not conditions:
            fallback = (name, target_id)
            continue
        if all(_condition_matches(variables, key, value) for key, value in conditions.items()):
            return name, target_id
    return fallback or ("", "")


def _wash_sections_for_media(config, media_type):
    parsed = _parse_wash_strategy(config.get("wash_strategy"))
    sections = []
    for section in parsed.get("sections", []):
        if section.get("media_type") == media_type and section.get("priority_level"):
            sections.append(section)
    return sections


def _criteria_matches(variables, criteria):
    for key, expected in (criteria or {}).items():
        if not _condition_matches(variables, key, expected):
            return False
    return True


def _quality_rank(variables, config):
    sections = _wash_sections_for_media(config, variables.get("media_type"))
    for section in sections:
        for index, criteria in enumerate(section.get("priority_level") or []):
            if _criteria_matches(variables, criteria):
                return index
    return 999


def _wash_group_key(variables):
    title = _norm_token(variables.get("title"))
    year = _norm_token(variables.get("year"))
    if variables.get("media_type") == "tv":
        return ("tv", title, _norm_token(variables.get("season_episode")))
    return ("movie", title, year)


async def _enrich_variables_from_tmdb(variables, sys_config, http_client):
    api_key = (sys_config.get("api_key") or "").strip()
    api_domain = (sys_config.get("api_domain") or "https://api.tmdb.org").rstrip("/")
    title = (variables.get("title") or "").strip()
    if not api_key or not title:
        return variables

    media_type = variables.get("media_type") or "movie"
    endpoint = "tv" if media_type == "tv" else "movie"
    params = {"api_key": api_key, "query": title, "language": "zh-CN"}
    if variables.get("year"):
        params["first_air_date_year" if endpoint == "tv" else "year"] = variables["year"]
    try:
        res = await http_client.get(f"{api_domain}/3/search/{endpoint}", params=params)
        data = res.json()
        results = data.get("results") or []
        if not results:
            return variables
        item = results[0]
        variables["tmdb_id"] = str(item.get("id") or variables.get("tmdb_id") or "")
        variables["genre_ids"] = ",".join(str(v) for v in (item.get("genre_ids") or []))
        if endpoint == "tv":
            variables["origin_country"] = ",".join(item.get("origin_country") or [])
            variables["en_title"] = item.get("original_name") or variables.get("en_title") or ""
        else:
            variables["origin_country"] = (item.get("original_language") or "").upper()
            variables["en_title"] = item.get("original_title") or variables.get("en_title") or ""
            if variables["tmdb_id"]:
                detail_res = await http_client.get(
                    f"{api_domain}/3/movie/{variables['tmdb_id']}",
                    params={"api_key": api_key, "language": "zh-CN"},
                )
                detail = detail_res.json()
                countries = [
                    country.get("iso_3166_1")
                    for country in (detail.get("production_countries") or [])
                    if country.get("iso_3166_1")
                ]
                if countries:
                    variables["origin_country"] = ",".join(countries)
        variables["title"] = item.get("title") or item.get("name") or variables.get("title")
        variables["year"] = variables.get("year") or (item.get("release_date") or item.get("first_air_date") or "")[:4]
    except Exception as exc:
        add_log("WARNING", f"銆愮綉鐩樻暣鐞嗐€慣MDB 鍏冩暟鎹ˉ鍏ㄨ烦杩? {title} -> {exc}", module="drive")
    return variables


def _apply_wash_strategy(plans, config):
    groups = {}
    for item in plans:
        groups.setdefault(item["_wash_group_key"], []).append(item)

    for group_items in groups.values():
        if len(group_items) < 2:
            continue
        winner = sorted(
            group_items,
            key=lambda item: (item["_quality_rank"], -int(item.get("size") or 0), item["old_name"]),
        )[0]
        winner["_wash_winner"] = True
        winner["wash_group_size"] = len(group_items)
        winner["message"] = f"洗版保留：同组 {len(group_items)} 个版本中优先级最高"
        for item in group_items:
            if item is winner:
                continue
            item["_wash_rejected_by"] = winner["file_id"]
            item["status"] = "wash_rejected"
            item["message"] = f"洗版淘汰：保留 {winner['old_name']}"
    return plans


def render_rule(template, variables):
    formatter = Formatter()
    pieces = []
    data = _SafeDict(variables)
    for literal, field, spec, conv in formatter.parse(template or ""):
        pieces.append(literal)
        if field is None:
            continue
        value = data[field]
        if conv == "r":
            value = repr(value)
        elif conv == "s":
            value = str(value)
        pieces.append(format(value, spec) if spec else str(value))
    return sanitize_name("".join(pieces))


def sanitize_name(name):
    value = re.sub(r'[\\/:*?"<>|]+', " ", str(name or ""))
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"(\. )+", ".", value)
    value = re.sub(r"\.+$", "", value)
    return value or "未命名"


async def _ensure_folder(client, drive_type, parent_id, folder_name, dry_run):
    clean_name = sanitize_name(folder_name)
    children = await _list_normalized(client, drive_type, parent_id)
    for item in children:
        if item["is_folder"] and item["name"] == clean_name:
            return item["id"], "exists"
    if dry_run:
        return f"dryrun:{parent_id}:{clean_name}", "dry_run"
    success, msg = await client.make_dir(parent_id, clean_name)
    if not success:
        raise RuntimeError(msg)
    children = await _list_normalized(client, drive_type, parent_id)
    for item in children:
        if item["is_folder"] and item["name"] == clean_name:
            return item["id"], "created"
    raise RuntimeError(f"鐩綍宸插垱寤轰絾鏈兘鍥炶 ID: {clean_name}")


async def _rename_file(client, file_id, new_name, dry_run):
    if dry_run:
        return True, "dry_run"
    success, msg = await client.rename(file_id, new_name)
    return success, msg


async def _move_file(client, drive_type, file_id, target_parent_id, dry_run):
    if dry_run:
        return True, "dry_run"
    if drive_type == "quark":
        payload = {"action_type": 1, "filelist": [file_id], "to_pdir_fid": target_parent_id or "0"}
        async with httpx.AsyncClient(timeout=client.timeout) as http:
            res = await http.post(f"{client.api_url}/file/move", params=client._get_base_params(), json=payload, headers=client.headers)
            data = _safe_json(res)
            return data.get("code") == 0, data.get("message") or "绉诲姩瀹屾垚"
    if drive_type == "aliyun":
        success, msg = await client._refresh_access_token()
        if not success:
            return False, msg
        payload = {
            "drive_id": client.default_drive_id,
            "file_id": file_id,
            "to_parent_file_id": target_parent_id,
            "check_name_mode": "auto_rename",
        }
        async with httpx.AsyncClient(timeout=client.timeout) as http:
            res = await http.post(f"{client.api_url}/v3/file/move", json=payload, headers=client._auth_headers())
            data = _safe_json(res)
            return res.status_code in (200, 201, 202) and not data.get("code"), client._format_error(data, "绉诲姩瀹屾垚") if data.get("code") else "绉诲姩瀹屾垚"
    if drive_type == "115":
        return await client.move(file_id, target_parent_id)
    return False, "当前网盘暂未接入移动接口"


async def _delete_file(client, file_id, dry_run):
    if dry_run:
        return True, "dry_run"
    if not hasattr(client, "delete"):
        return False, "当前网盘暂未接入回收站接口"
    return await client.delete(file_id)


def _is_better_plan(left, right):
    left_rank = int(left.get("_quality_rank", 999))
    right_rank = int(right.get("_quality_rank", 999))
    if left_rank != right_rank:
        return left_rank < right_rank
    return int(left.get("size") or 0) > int(right.get("size") or 0)


async def _apply_existing_target_wash(client, drive_type, target_parent, item, config):
    if item.get("_quality_rank", 999) == 999 and not (config.get("wash_strategy") or "").strip():
        return "continue", ""
    try:
        existing_items = await _list_normalized(client, drive_type, target_parent)
    except Exception as exc:
        return "continue", f"鐩爣鐩綍娲楃増妫€鏌ヨ烦杩? {exc}"

    current_key = item.get("_wash_group_key")
    for existing in existing_items:
        if existing.get("is_folder") or existing.get("id") == item.get("file_id"):
            continue
        if os.path.splitext(existing.get("name") or "")[1].lower() not in VIDEO_EXTS:
            continue
        variables = parse_media_name(existing.get("name") or "")
        if _wash_group_key(variables) != current_key:
            continue
        existing_plan = {
            "file_id": existing["id"],
            "old_name": existing["name"],
            "size": int(existing.get("size") or 0),
            "_quality_rank": _quality_rank(variables, config),
        }
        if _is_better_plan(existing_plan, item):
            return "skip", f"娲楃増璺宠繃锛氱洰鏍囩洰褰曞凡鏈夋洿浼樼増鏈?{existing_plan['old_name']}"
        success, msg = await _delete_file(client, existing_plan["file_id"], _bool(config.get("dry_run")))
        if not success:
            return "skip", f"娲楃増鏇挎崲鍙楅樆锛氱洰鏍囩洰褰曞凡鏈変綆浼樺厛绾х増鏈紝浣嗘棤娉曞洖鏀? {msg}"
        return "continue", f"娲楃増鏇挎崲锛氬凡鍥炴敹鏃х増鏈?{existing_plan['old_name']}"
    return "continue", ""


def _plan_for_file(item, config, variables=None):
    variables = variables or parse_media_name(item["name"])
    drive_type = _normalize_drive_type(config.get("drive_type"))
    category_name, category_id = _category_for_variables(variables, config, drive_type)
    if variables["media_type"] == "tv":
        folder = render_rule(config.get("tv_folder_rule"), variables)
        season = render_rule(config.get("season_folder_rule"), variables)
        new_name = render_rule(config.get("episode_file_rule"), variables)
        root_id = config.get("tv_dir") or config.get("movie_dir") or config.get("source_dir")
        target_parts = [folder, season]
    else:
        folder = render_rule(config.get("movie_folder_rule"), variables)
        new_name = render_rule(config.get("movie_file_rule"), variables)
        root_id = config.get("movie_dir") or config.get("source_dir")
        target_parts = [folder]
    if category_id:
        root_id = category_id
    if not new_name.lower().endswith(variables["ext"].lower()):
        new_name = sanitize_name(new_name) + variables["ext"]
    return {
        "file_id": item["id"],
        "parent_id": item["parent_id"],
        "size": int(item.get("size") or 0),
        "source_path": item["path"],
        "old_name": item["name"],
        "new_name": new_name,
        "media_type": variables["media_type"],
        "title": variables["title"],
        "category": category_name,
        "root_id": root_id,
        "target_parts": target_parts,
        "target_path": "/".join(target_parts + [new_name]),
        "status": "pending",
        "message": "绛夊緟鎵ц",
        "_variables": variables,
        "_quality_rank": _quality_rank(variables, config),
        "_wash_group_key": _wash_group_key(variables),
    }


async def preview_organize(config_override=None):
    config = {**get_organizer_config(), **(config_override or {})}
    sys_config = get_sys_config()
    drive_type = _normalize_drive_type(config.get("drive_type"))
    client, default_root = _client_for(drive_type, sys_config)
    source_dir = _clean_id(config.get("source_dir"), default_root)
    files = await _scan_files(
        client,
        drive_type,
        source_dir,
        min(max(_int(config.get("max_items"), 30), 1), 200),
        _bool(config.get("recursive")),
        min(max(_int(config.get("max_depth"), 2), 0), 8),
    )
    plans = []
    async with httpx.AsyncClient(timeout=8.0) as http_client:
        for item in files:
            variables = parse_media_name(item["name"])
            variables = await _enrich_variables_from_tmdb(variables, sys_config, http_client)
            plans.append(_plan_for_file(item, config, variables))
    plans = _apply_wash_strategy(plans, config)
    return {"drive_type": drive_type, "count": len(plans), "items": plans}


async def run_organize(config_override=None):
    config = {**get_organizer_config(), **(config_override or {})}
    config["dry_run"] = _bool(config.get("dry_run"))
    sys_config = get_sys_config()
    drive_type = _normalize_drive_type(config.get("drive_type"))
    client, _ = _client_for(drive_type, sys_config)
    preview = await preview_organize(config)
    items = preview["items"]
    add_log("INFO", f"【网盘整理】开始整理 {drive_type}，模式: {'预览' if config['dry_run'] else '执行'}，候选: {len(items)}", module="drive")
    for item in items:
        try:
            if item.get("_wash_rejected_by"):
                if config["dry_run"]:
                    item["status"] = "dry_run"
                    continue
                winner = next((candidate for candidate in items if candidate["file_id"] == item["_wash_rejected_by"]), None)
                if not winner or winner.get("status") != "success":
                    item["status"] = "skipped"
                    item["message"] = "洗版跳过：保留版本尚未整理成功，未处理该低优先级版本"
                    continue
                success, msg = await _delete_file(client, item["file_id"], config["dry_run"])
                if not success:
                    item["status"] = "skipped"
                    item["message"] = f"洗版淘汰但未回收: {msg}"
                    continue
                item["status"] = "success"
                item["message"] = "洗版淘汰：已放入回收站"
                continue
            if config["dry_run"]:
                item["status"] = "dry_run"
                if not item.get("_wash_winner"):
                    item["message"] = "预览通过"
                continue
            target_parent = _clean_id(item["root_id"], "root" if drive_type == "aliyun" else "0")
            for part in item["target_parts"]:
                target_parent, _ = await _ensure_folder(client, drive_type, target_parent, part, config["dry_run"])
            wash_action, wash_msg = await _apply_existing_target_wash(client, drive_type, target_parent, item, config)
            if wash_action == "skip":
                item["status"] = "skipped"
                item["message"] = wash_msg
                continue
            if item["old_name"] != item["new_name"]:
                success, msg = await _rename_file(client, item["file_id"], item["new_name"], config["dry_run"])
                if not success:
                    raise RuntimeError(msg)
            success, msg = await _move_file(client, drive_type, item["file_id"], target_parent, config["dry_run"])
            if not success:
                raise RuntimeError(msg)
            item["status"] = "success"
            item["message"] = wash_msg or "整理完成"
        except Exception as exc:
            item["status"] = "failed"
            item["message"] = str(exc)
    ok = sum(1 for item in items if item["status"] in {"success", "dry_run", "skipped"})
    failed = sum(1 for item in items if item["status"] == "failed")
    level = "SUCCESS" if failed == 0 else "WARNING"
    add_log(level, f"【网盘整理】完成 {drive_type}：成功 {ok}，失败 {failed}", module="drive")
    return {"drive_type": drive_type, "count": len(items), "success": ok, "failed": failed, "items": items}


async def maybe_run_post_transfer_organize(drive_type, source_dir, title="", media_type="", sys_config=None):
    sys_config = sys_config or get_sys_config()
    if str(sys_config.get("pipeline_auto_organize", "0")) != "1":
        return {"skipped": True, "message": "pipeline_auto_organize disabled"}

    drive_type = _normalize_drive_type(drive_type)
    if drive_type not in {"115", "quark", "aliyun"}:
        add_log("WARNING", f"【流程整理】{drive_type or 'unknown'} 暂不支持自动整理，已跳过。", module="drive")
        return {"skipped": True, "message": "drive not supported"}

    default_root = DEFAULT_ROOTS.get(drive_type, "0")
    source_id = _clean_id(source_dir, default_root)
    max_items = min(max(_int(sys_config.get("pipeline_organize_max_items"), 30), 1), 200)

    config = get_organizer_config()
    config.update({
        "drive_type": drive_type,
        "source_dir": source_id,
        "max_items": max_items,
        "dry_run": False,
        "recursive": True,
    })

    media_type = (media_type or "").lower()
    if media_type == "tv":
        if _is_default_root(drive_type, config.get("tv_dir")):
            config["tv_dir"] = source_id
    elif media_type == "movie":
        if _is_default_root(drive_type, config.get("movie_dir")):
            config["movie_dir"] = source_id
    else:
        if _is_default_root(drive_type, config.get("movie_dir")):
            config["movie_dir"] = source_id
        if _is_default_root(drive_type, config.get("tv_dir")):
            config["tv_dir"] = source_id

    display_title = title or "未命名任务"
    add_log("INFO", f"【流程整理】《{display_title}》转存成功，开始整理 {drive_type}:{source_id}。", module="drive")
    try:
        return await run_organize(config)
    except Exception as exc:
        add_log("ERROR", f"【流程整理】《{display_title}》自动整理失败: {exc}", module="drive")
        return {"skipped": False, "failed": 1, "message": str(exc)}


def smoke_test_parser():
    sample = "The Boys.S04E01.2024.1080p.WEB-DL.H265.mkv"
    parsed = parse_media_name(sample)
    assert parsed["media_type"] == "tv"
    assert parsed["season_episode"] == "S04E01"
    assert render_rule("{title}.{year}.{season_episode}{ext}", parsed).endswith(".mkv")
    return True


