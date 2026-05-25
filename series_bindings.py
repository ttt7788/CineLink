import datetime
import re

from aliyun_drive_mobile import AliyunDrive
from database import get_db, get_sys_config
from drive_api import Drive115, QuarkDrive, Drive123Open
from logger import add_log


VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".ts", ".m2ts", ".rmvb", ".webm", ".iso")


class SeriesBindingUnavailable(RuntimeError):
    """The bound cloud folder is missing or cannot be scanned."""


def normalize_drive_type(drive_type):
    value = (drive_type or "115").lower()
    if value in {"115", "cloud115"}:
        return "115"
    if value in {"aliyun", "alipan", "aliyundrive"}:
        return "aliyun"
    if value == "quark":
        return "quark"
    if value in {"123", "123pan", "pan123"}:
        return "123"
    return value


def get_default_parent_id(drive_type, config=None):
    config = config or get_sys_config()
    drive_type = normalize_drive_type(drive_type)
    if drive_type == "quark":
        return (config.get("quark_save_dir") or "0").split("-")[0].strip() or "0"
    if drive_type == "aliyun":
        return (config.get("aliyun_save_dir") or "root").split("-")[0].strip() or "root"
    if drive_type == "115":
        return (config.get("drive115_save_dir") or "0").split("-")[0].strip() or "0"
    if drive_type == "123":
        return (config.get("drive123_save_dir") or "0").split("-")[0].strip() or "0"
    return "0"


def mount_root(drive_type):
    return {
        "115": "/115",
        "aliyun": "/aliyun",
        "quark": "/quark",
        "123": "/123",
    }.get(normalize_drive_type(drive_type), f"/{drive_type}")


def normalize_title(value):
    return re.sub(r"[\W_]+", "", (value or "").lower(), flags=re.UNICODE)


def is_video_name(name):
    return (name or "").lower().endswith(VIDEO_EXTS)


def item_name(drive_type, item):
    drive_type = normalize_drive_type(drive_type)
    if drive_type == "quark":
        return item.get("file_name") or item.get("name") or ""
    if drive_type == "115":
        return item.get("n") or item.get("fn") or item.get("name") or ""
    if drive_type == "123":
        return item.get("filename") or item.get("fileName") or ""
    return item.get("name") or ""


def item_id(drive_type, item):
    drive_type = normalize_drive_type(drive_type)
    if drive_type == "quark":
        return item.get("fid") or ""
    if drive_type == "115":
        return item.get("cid") or item.get("fid") or ""
    if drive_type == "123":
        return str(item.get("fileId") or "")
    return item.get("file_id") or ""


def is_folder(drive_type, item):
    drive_type = normalize_drive_type(drive_type)
    if drive_type == "quark":
        return item.get("file_type") == 0 or item.get("dir") is True
    if drive_type == "115":
        return bool(item.get("cid")) and not item.get("fid")
    if drive_type == "123":
        return int(item.get("type") or 0) == 1
    return item.get("type") == "folder"


def item_updated_at(drive_type, item):
    drive_type = normalize_drive_type(drive_type)
    if drive_type == "quark":
        value = item.get("updated_at") or item.get("created_at") or 0
        try:
            value = int(value)
            if value > 100000000000:
                value = value / 1000
            return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S") if value else ""
        except Exception:
            return ""
    if drive_type == "115":
        value = item.get("te") or item.get("tu") or item.get("t") or ""
        try:
            return datetime.datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S") if str(value).isdigit() else str(value)
        except Exception:
            return str(value or "")
    if drive_type == "123":
        return str(item.get("updateAt") or item.get("createAt") or "")
    return str(item.get("updated_at") or item.get("created_at") or "").replace("T", " ").replace("Z", "")


def safe_folder_name(title):
    value = re.sub(r'[\\/:*?"<>|]+', " ", title or "").strip()
    return re.sub(r"\s+", " ", value)[:80] or "未命名剧集"


def is_binding_read_error(message):
    text = str(message or "").lower()
    if not text:
        return False
    auth_markers = ("cookie", "token", "access token", "unauthorized", "forbidden", "未配置", "授权", "登录")
    if any(marker.lower() in text for marker in auth_markers):
        return False
    missing_markers = (
        "code=404",
        "code=405",
        "http 404",
        "http 405",
        "not found",
        "cannot be found",
        "no such file",
        "not exist",
        "目录不存在",
        "目录读取失败",
        "文件夹不存在",
        "parent_id",
        "file_id cannot be found",
    )
    return any(marker in text for marker in missing_markers)


def unbind_series_folder(binding_id, title, reason):
    conn = get_db()
    conn.execute("DELETE FROM series_bindings WHERE id=?", (binding_id,))
    conn.commit()
    conn.close()
    add_log(
        "WARNING",
        f"【剧集追更】《{title or '未命名剧集'}》绑定目录不可用，已自动解除绑定，请重新绑定追剧目录。原因: {reason}",
        module="series",
    )


async def list_drive_files(drive_type, parent_id, config=None):
    config = config or get_sys_config()
    drive_type = normalize_drive_type(drive_type)
    if drive_type == "quark":
        api = QuarkDrive(config.get("cookie_quark", ""))
        return await api.list_files(parent_id or "0")
    if drive_type == "aliyun":
        api = AliyunDrive(config.get("token_aliyun", ""))
        return await api.list_files(parent_id or "root")
    if drive_type == "123":
        api = Drive123Open(config.get("drive123_client_id", ""), config.get("drive123_client_secret", ""))
        return await api.list_files(parent_id or "0")
    api = Drive115(config.get("cookie_115", ""))
    return await api.list_files(parent_id or "0")


async def ensure_series_target_folder(drive_type, title, parent_id=None, config=None):
    drive_type = normalize_drive_type(drive_type)
    config = config or get_sys_config()
    parent_id = parent_id or get_default_parent_id(drive_type, config)
    folder_name = safe_folder_name(title)
    items, msg = await list_drive_files(drive_type, parent_id, config)
    if msg != "success":
        return parent_id, mount_root(drive_type), False, msg or f"{drive_type} 父目录读取失败"

    for item in items:
        if is_folder(drive_type, item) and item_name(drive_type, item) == folder_name:
            return item_id(drive_type, item), f"{mount_root(drive_type)}/{folder_name}".replace("//", "/"), True, "success"

    if drive_type == "quark":
        api = QuarkDrive(config.get("cookie_quark", ""))
        ok, msg = await api.make_dir(parent_id, folder_name)
    elif drive_type == "aliyun":
        api = AliyunDrive(config.get("token_aliyun", ""))
        ok, msg = await api.make_dir(parent_id, folder_name)
    elif drive_type == "115":
        api = Drive115(config.get("cookie_115", ""))
        ok, msg = await api.make_dir(parent_id, folder_name)
    elif drive_type == "123":
        api = Drive123Open(config.get("drive123_client_id", ""), config.get("drive123_client_secret", ""))
        ok, msg = await api.make_dir(parent_id, folder_name)
    else:
        return parent_id, mount_root(drive_type), False, f"{drive_type} 暂不支持自动创建剧集绑定目录"

    if not ok:
        return parent_id, mount_root(drive_type), False, msg or "创建剧集目录失败"

    items, msg = await list_drive_files(drive_type, parent_id, config)
    if msg != "success":
        return parent_id, mount_root(drive_type), False, msg or "创建后重新读取目录失败"
    for item in items:
        if is_folder(drive_type, item) and item_name(drive_type, item) == folder_name:
            return item_id(drive_type, item), f"{mount_root(drive_type)}/{folder_name}".replace("//", "/"), True, "success"
    return parent_id, mount_root(drive_type), False, "已创建目录，但未能定位新目录 ID"


async def find_series_folder(drive_type, title, parent_id, config=None):
    drive_type = normalize_drive_type(drive_type)
    parent_id = parent_id or get_default_parent_id(drive_type, config)
    items, msg = await list_drive_files(drive_type, parent_id, config)
    if msg != "success":
        return parent_id, mount_root(drive_type), msg

    title_key = normalize_title(title)
    folders = [item for item in items if is_folder(drive_type, item)]
    matches = []
    for item in folders:
        name = item_name(drive_type, item)
        key = normalize_title(name)
        if title_key and (title_key in key or key in title_key):
            matches.append(item)

    picked = matches[0] if matches else None
    if picked:
        folder_name = item_name(drive_type, picked)
        return item_id(drive_type, picked), f"{mount_root(drive_type)}/{folder_name}".replace("//", "/"), "success"

    return "", "", "未定位到明确剧集目录，已保持未绑定"


async def scan_series_folder(drive_type, parent_id, max_depth=4, config=None):
    drive_type = normalize_drive_type(drive_type)
    parent_id = str(parent_id or "").strip()
    if not parent_id:
        raise SeriesBindingUnavailable("未绑定追剧目录")
    seen = set()
    latest_name = ""
    latest_time = ""

    async def walk(folder_id, depth):
        nonlocal latest_name, latest_time
        if not folder_id or folder_id in seen or depth > max_depth:
            return 0
        seen.add(folder_id)
        items, msg = await list_drive_files(drive_type, folder_id, config)
        if msg != "success":
            message = msg or f"{drive_type} 目录读取失败: parent_id={folder_id}"
            if is_binding_read_error(message):
                raise SeriesBindingUnavailable(message)
            raise RuntimeError(message)
        count = 0
        for item in items:
            name = item_name(drive_type, item)
            updated = item_updated_at(drive_type, item)
            if updated and updated > latest_time:
                latest_time = updated
                latest_name = name
            if is_folder(drive_type, item):
                count += await walk(item_id(drive_type, item), depth + 1)
            elif is_video_name(name):
                count += 1
        return count

    return await walk(parent_id, 0), latest_name, latest_time


async def bind_series_after_transfer(tmdb_id, media_type, title, drive_type, source_share_url="", source_share_pwd="", parent_id=None, cloud_path=None):
    if media_type != "tv":
        return False
    drive_type = normalize_drive_type(drive_type)
    config = get_sys_config()
    parent_id = parent_id or get_default_parent_id(drive_type, config)
    if cloud_path and parent_id:
        cloud_parent_id, msg = parent_id, "success"
    else:
        cloud_parent_id, cloud_path, msg = await find_series_folder(drive_type, title, parent_id, config)
    if msg != "success" or not cloud_parent_id or not cloud_path:
        add_log("WARNING", f"【剧集绑定】《{title}》绑定失败: {msg or '未定位到目录'}")
        return False
    episode_count = 0
    latest_name = ""
    latest_time = ""
    try:
        episode_count, latest_name, latest_time = await scan_series_folder(drive_type, cloud_parent_id, config=config)
    except Exception as exc:
        add_log("WARNING", f"【剧集绑定】《{title}》已记录绑定，但扫描集数失败: {exc}")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        """
        INSERT INTO series_bindings
            (tmdb_id, drive_type, title, cloud_parent_id, cloud_path, source_share_url, source_share_pwd,
             latest_episode_count, latest_item_name, latest_item_updated_at, last_checked_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id, drive_type) DO UPDATE SET
            title=excluded.title,
            cloud_parent_id=excluded.cloud_parent_id,
            cloud_path=excluded.cloud_path,
            source_share_url=excluded.source_share_url,
            source_share_pwd=excluded.source_share_pwd,
            latest_episode_count=excluded.latest_episode_count,
            latest_item_name=excluded.latest_item_name,
            latest_item_updated_at=excluded.latest_item_updated_at,
            last_checked_at=excluded.last_checked_at,
            updated_at=excluded.updated_at
        """,
        (
            tmdb_id,
            drive_type,
            title,
            cloud_parent_id,
            cloud_path,
            source_share_url,
            source_share_pwd,
            episode_count,
            latest_name,
            latest_time,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    add_log("SUCCESS", f"【剧集绑定】《{title}》已绑定 {drive_type}:{cloud_path}，当前识别 {episode_count} 个视频文件。")
    return True


async def bind_series_manual(tmdb_id, title, drive_type, cloud_parent_id, cloud_path=""):
    drive_type = normalize_drive_type(drive_type)
    cloud_parent_id = str(cloud_parent_id or "").strip()
    if not cloud_parent_id:
        return False, "请填写追剧目录 ID", None

    config = get_sys_config()
    episode_count, latest_name, latest_time = await scan_series_folder(
        drive_type,
        cloud_parent_id,
        config=config,
    )
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    display_path = (cloud_path or "").strip() or f"{mount_root(drive_type)}/{cloud_parent_id}".replace("//", "/")
    conn = get_db()
    conn.execute(
        """
        INSERT INTO series_bindings
            (tmdb_id, drive_type, title, cloud_parent_id, cloud_path,
             latest_episode_count, latest_item_name, latest_item_updated_at, last_checked_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id, drive_type) DO UPDATE SET
            title=excluded.title,
            cloud_parent_id=excluded.cloud_parent_id,
            cloud_path=excluded.cloud_path,
            latest_episode_count=excluded.latest_episode_count,
            latest_item_name=excluded.latest_item_name,
            latest_item_updated_at=excluded.latest_item_updated_at,
            last_checked_at=excluded.last_checked_at,
            updated_at=excluded.updated_at
        """,
        (
            tmdb_id,
            drive_type,
            title,
            cloud_parent_id,
            display_path,
            episode_count,
            latest_name,
            latest_time,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    add_log(
        "SUCCESS",
        f"【剧集绑定】《{title}》已手动绑定 {drive_type}:{display_path}，当前识别 {episode_count} 个视频文件。",
        module="series",
    )
    return True, "绑定成功", {
        "cloud_parent_id": cloud_parent_id,
        "cloud_path": display_path,
        "latest_episode_count": episode_count,
        "latest_item_name": latest_name,
        "latest_item_updated_at": latest_time,
        "last_checked_at": now,
    }


def is_root_binding(row):
    drive_type = normalize_drive_type(row["drive_type"])
    root_id = "root" if drive_type == "aliyun" else "0"
    return (row["cloud_parent_id"] or "") == root_id and (row["cloud_path"] or "") == mount_root(drive_type)


def purge_root_series_bindings():
    conn = get_db()
    rows = conn.execute("SELECT * FROM series_bindings").fetchall()
    bad_ids = [row["id"] for row in rows if is_root_binding(row)]
    if bad_ids:
        conn.execute(f"DELETE FROM series_bindings WHERE id IN ({','.join('?' * len(bad_ids))})", bad_ids)
        conn.commit()
    conn.close()
    if bad_ids:
        add_log("WARNING", f"【剧集绑定】已清理 {len(bad_ids)} 条误绑定到网盘根目录的记录，请重新刷新绑定。")
    return len(bad_ids)


async def refresh_series_bindings():
    purge_root_series_bindings()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT b.*, m.media_type
        FROM series_bindings b
        JOIN media_items m ON m.tmdb_id = b.tmdb_id
        ORDER BY b.updated_at DESC
        """
    ).fetchall()
    conn.close()
    if not rows:
        return

    config = get_sys_config()
    for row in rows:
        title = row["title"] or ""
        try:
            count, latest_name, latest_time = await scan_series_folder(
                row["drive_type"],
                row["cloud_parent_id"],
                config=config,
            )
            old_count = int(row["latest_episode_count"] or 0)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = get_db()
            conn.execute(
                """
                UPDATE series_bindings
                SET latest_episode_count=?, latest_item_name=?, latest_item_updated_at=?,
                    last_checked_at=?, updated_at=?
                WHERE id=?
                """,
                (count, latest_name, latest_time, now, now, row["id"]),
            )
            conn.commit()
            conn.close()
            if count > old_count:
                add_log("SUCCESS", f"【剧集追更】《{title}》发现更新：{old_count} -> {count}，最新：{latest_name or '未知'}", module="series")
            else:
                add_log("INFO", f"【剧集追更】《{title}》暂无新增，当前 {count} 个视频文件。", module="series")
        except SeriesBindingUnavailable as exc:
            unbind_series_folder(row["id"], title, str(exc) or "目录不可用")
        except Exception as exc:
            add_log("ERROR", f"【剧集追更】《{title}》扫描失败: {exc or '未知错误'}", module="series")


async def rebuild_success_series_bindings(only_missing=True):
    purge_root_series_bindings()
    missing_clause = "AND b.id IS NULL" if only_missing else ""
    conn = get_db()
    rows = conn.execute(
        f"""
        SELECT s.tmdb_id, s.drive_type, m.title, m.media_type,
               b.id AS binding_id, b.source_share_url, b.source_share_pwd
        FROM subscriptions s
        JOIN media_items m ON m.tmdb_id = s.tmdb_id
        LEFT JOIN series_bindings b ON b.tmdb_id = s.tmdb_id AND b.drive_type = s.drive_type
        WHERE s.status = 'success' AND m.media_type = 'tv'
        {missing_clause}
        ORDER BY s.id DESC
        """
    ).fetchall()
    conn.close()

    mode_label = "未绑定剧集" if only_missing else "全部成功剧集"
    add_log("INFO", f"【剧集绑定】开始刷新{mode_label}，待处理 {len(rows)} 条。")
    if not rows:
        add_log("INFO", "【剧集绑定】没有需要补绑定的剧集：可能都已绑定，或当前没有成功转存的剧集记录。")
        return {"processed": 0, "bound": 0, "failed": 0}

    processed = 0
    bound = 0
    failed = 0
    for row in rows:
        processed += 1
        try:
            add_log("INFO", f"【剧集绑定】正在处理 {processed}/{len(rows)}：《{row['title']}》 -> {row['drive_type']}")
            result = await bind_series_after_transfer(
                row["tmdb_id"],
                row["media_type"],
                row["title"],
                row["drive_type"],
                row["source_share_url"] or "",
                row["source_share_pwd"] or "",
                get_default_parent_id(row["drive_type"]),
            )
            if result:
                bound += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            add_log("ERROR", f"【剧集绑定】《{row['title']}》补全绑定失败: {exc}")
    return {"processed": processed, "bound": bound, "failed": failed}
