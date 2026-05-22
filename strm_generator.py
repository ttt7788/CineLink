import sys
import os
import time
import random
import re
import posixpath
from urllib.parse import quote, urlparse, unquote
import threading
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import easywebdav
import requests

from database import get_db
from logger import add_log
from alist_integration import ALIST_BASE_URL, get_alist_admin_token
from internal_drives import INTERNAL_DRIVE_PROVIDERS
from strm_control import StrmControlStopped, check_strm_control, finish_strm_job, start_strm_job

INTERNAL_SOURCE_TYPES = {'115_internal', 'aliyun_internal', 'quark_internal', '123_internal'}
INTERNAL_PLAY_PUBLIC_URL = os.environ.get("CINELINK_PLAY_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
INTERNAL_STRM_BACKEND = os.environ.get("CINELINK_STRM_BACKEND", "play").lower()
ALIYUN_STRM_MODE = os.environ.get("CINELINK_ALIYUN_STRM_MODE", "preview").lower()
QUARK_STRM_MODE = os.environ.get("CINELINK_QUARK_STRM_MODE", "preview").lower()
INTERNAL_ALIST_PUBLIC_URL = os.environ.get("CINELINK_ALIST_PUBLIC_URL", ALIST_BASE_URL).rstrip("/")
DEFAULT_STRM_OUTPUT_DIR = os.environ.get("CINELINK_STRM_OUTPUT_DIR", "/data/media")
INTERNAL_SOURCE_DRIVE = {"115_internal": "115", "aliyun_internal": "aliyun", "quark_internal": "quark", "123_internal": "123"}

strm_file_counter = 0  
metadata_file_counter = 0  # 【新增】元数据下载计数器
video_file_counter = 0  
existing_strm_file_counter = 0  
dir_scan_counter = 0  
strm_tasks = [] 
metadata_tasks = []        # 【新增】元数据下载队列
counter_lock = threading.Lock()
db_lock = threading.Lock()
thread_local = threading.local()
alist_dir_cache = {}
alist_cache_lock = threading.Lock()
internal_id_path_cache = {}
internal_id_path_lock = threading.Lock()

def get_webdav_config(config_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM strm_configs WHERE id=?", (config_id,)).fetchone()
    conn.close()
    if not row: return None
    
    source_type = row['source_type'] if 'source_type' in row.keys() and row['source_type'] else 'webdav'
    if source_type in INTERNAL_SOURCE_TYPES:
        protocol = "internal"
        host = "alist"
        port = 0
    else:
        parsed_url = urlparse(row['url'])
        protocol = parsed_url.scheme
        host = parsed_url.hostname
        port = parsed_url.port if parsed_url.port else (80 if protocol == 'http' else 443)
    
    try:
        min_int, max_int = map(float, str(row['download_interval_range']).split('-'))
    except:
        min_int, max_int = 1.0, 3.0

    return {
        'id': row['id'], 'source_type': source_type,
        'config_name': row['config_name'], 'host': host, 'port': int(port),
        'username': row['username'], 'password': row['password'],
        'rootpath': row['rootpath'], 'protocol': protocol,
        'root_id': row['root_id'] if 'root_id' in row.keys() else '',
        'public_url': "",
        'target_directory': row['target_directory'],
        'update_mode': row['update_mode'],
        'interval': (min_int, max_int),
        'download_enabled': row['download_enabled'] # 【修复】读取是否开启元数据下载
    }

def get_script_config():
    conn = get_db()
    row = conn.execute("SELECT * FROM strm_settings LIMIT 1").fetchone()
    conn.close()
    
    def parse_exts(ext_str):
        return [x.strip().lower() for x in str(ext_str).split(',') if x.strip()]
        
    return {
        'video_formats': parse_exts(row['video_formats']),
        'subtitle_formats': parse_exts(row['subtitle_formats']),
        'image_formats': parse_exts(row['image_formats']),
        'metadata_formats': parse_exts(row['metadata_formats']),
        'size_threshold': row['size_threshold'],
        'download_threads': row['download_threads']
    }

def safe_path_name(value):
    cleaned = re.sub(r"[^\w.-]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("_")
    return cleaned or "default"

def join_output_path(base, *parts):
    if str(base).startswith("/"):
        normalized_parts = [str(part).replace("\\", "/").strip("/") for part in parts if str(part)]
        return posixpath.join(str(base).rstrip("/"), *normalized_parts)
    return os.path.join(base, *parts)

def local_fs_path(path):
    if os.name != "nt":
        return path
    normalized = os.path.abspath(os.path.normpath(path))
    return normalized if normalized.startswith("\\\\?\\") else "\\\\?\\" + normalized

def path_is_writable(path):
    try:
        os.makedirs(local_fs_path(path), exist_ok=True)
        probe = os.path.join(path, ".cinelink_write_test")
        with open(local_fs_path(probe), "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(local_fs_path(probe))
        return True, ""
    except Exception as e:
        return False, str(e)

def normalize_target_directory(config):
    target = str(config.get('target_directory') or "").strip()
    source_name = str(config.get('source_type') or "webdav").replace("_internal", "")
    fallback = join_output_path(DEFAULT_STRM_OUTPUT_DIR, safe_path_name(source_name))

    if not target:
        add_log("WARNING", f"⚠️ STRM 节点 [{config['config_name']}] 未配置本地输出目录，自动使用: {fallback}")
        return fallback

    if os.name != "nt" and (re.match(r"^[A-Za-z]:[\\/]", target) or "\\" in target):
        add_log("WARNING", f"⚠️ STRM 节点 [{config['config_name']}] 使用了非容器路径 [{target}]，自动改用: {fallback}")
        return fallback

    if target.startswith("/"):
        normalized = posixpath.normpath(target)
        ok, reason = path_is_writable(normalized)
        if ok:
            return normalized
        fallback_ok, fallback_reason = path_is_writable(fallback)
        if fallback_ok:
            add_log("WARNING", f"⚠️ STRM 节点 [{config['config_name']}] 输出目录不可写 [{normalized}]，自动改用: {fallback}，原因: {reason}")
            return fallback
        add_log("ERROR", f"❌ STRM 节点 [{config['config_name']}] 输出目录不可写 [{normalized}]，默认目录也不可写 [{fallback}] -> {fallback_reason}")
        return normalized

    if not os.path.isabs(target):
        fixed = join_output_path(DEFAULT_STRM_OUTPUT_DIR, target)
        add_log("WARNING", f"⚠️ STRM 节点 [{config['config_name']}] 输出目录不是绝对路径，自动改用: {fixed}")
        return fixed

    return target

def get_alist_sign(path):
    parent_path = posixpath.dirname(path.rstrip("/")) or "/"
    file_name = posixpath.basename(path)
    with alist_cache_lock:
        cached = alist_dir_cache.get(parent_path)
    if cached is None:
        token = get_alist_admin_token()
        if not token:
            return ""
        try:
            res = requests.post(
                f"{ALIST_BASE_URL}/api/fs/list",
                headers={"Authorization": token},
                json={"path": parent_path, "page": 1, "per_page": 500, "refresh": False},
                timeout=30,
            )
            data = res.json()
            if data.get("code") != 200:
                add_log("WARNING", f"【AList STRM】读取目录失败: {parent_path} -> {data.get('message')}")
                return ""
            cached = data.get("data", {}).get("content") or []
            with alist_cache_lock:
                alist_dir_cache[parent_path] = cached
        except Exception as e:
            add_log("WARNING", f"【AList STRM】读取目录异常: {parent_path} -> {e}")
            return ""
    item = next((x for x in cached if x.get("name") == file_name), None)
    return item.get("sign", "") if item else ""

def alist_api_list(path):
    token = get_alist_admin_token()
    if not token:
        add_log("ERROR", "【AList STRM】未取得 Admin Token，无法扫描内置网盘。")
        return []

    all_items = []
    page = 1
    per_page = 500
    while True:
        try:
            res = requests.post(
                f"{ALIST_BASE_URL}/api/fs/list",
                headers={"Authorization": token},
                json={"path": path, "page": page, "per_page": per_page, "refresh": False},
                timeout=60,
            )
            data = res.json()
        except Exception as e:
            add_log("ERROR", f"【AList STRM】读取目录异常: {path} -> {e}")
            return all_items
        if data.get("code") != 200:
            add_log("ERROR", f"【AList STRM】读取目录失败: {path} -> {data.get('message') or data}")
            return all_items
        content = data.get("data", {}).get("content") or []
        all_items.extend(content)
        total = int(data.get("data", {}).get("total") or len(all_items))
        if not content or len(all_items) >= total or len(content) < per_page:
            break
        page += 1

    with alist_cache_lock:
        alist_dir_cache[path] = all_items
    return all_items

def get_internal_alist_root(config):
    drive = INTERNAL_SOURCE_DRIVE.get(config.get("source_type"), "")
    root = unquote(str(config.get("rootpath") or "/")).replace("\\", "/").strip()
    root = root.replace("/dav", "", 1) if root.startswith("/dav") else root
    parts = [part for part in root.strip("/").split("/") if part]
    if not parts:
        return f"/{drive}"
    if parts[0] == drive:
        return "/" + "/".join(parts)
    return "/" + "/".join([drive] + parts)

def validate_downloaded_metadata(local_file_path):
    try:
        if not os.path.exists(local_fs_path(local_file_path)):
            return False, "文件未落盘"
        size = os.path.getsize(local_fs_path(local_file_path))
        if size <= 0:
            return False, "下载结果为空文件"
        with open(local_fs_path(local_file_path), "rb") as fh:
            head = fh.read(256).lstrip()
        if head.startswith(b'{"code":') or head.startswith(b'{"message":') or head.startswith(b'{"error":'):
            return False, "下载到的是 AList/API 错误响应，不是媒体附属文件"
        if head.startswith(b"<html") or head.startswith(b"<!doctype html"):
            return False, "下载到的是 HTML 错误页面，不是媒体附属文件"
        return True, ""
    except Exception as e:
        return False, str(e)

def alist_rel_dir(current_dir, root_dir):
    current_dir = "/" + current_dir.strip("/")
    root_dir = "/" + root_dir.strip("/")
    if current_dir == root_dir:
        return ""
    if current_dir.startswith(root_dir.rstrip("/") + "/"):
        return current_dir[len(root_dir):].lstrip("/")
    return current_dir.strip("/")

def scan_alist_directories_concurrently(config, script_config, existing_records):
    global video_file_counter, existing_strm_file_counter, strm_tasks, metadata_tasks, dir_scan_counter

    root_dir = get_internal_alist_root(config)
    config["rootpath"] = root_dir
    meta_formats = script_config['subtitle_formats'] + script_config['image_formats'] + script_config['metadata_formats']
    add_log("INFO", f"📂 开始使用 AList API 扫描内置网盘目录: {root_dir}")

    max_workers = script_config.get('download_threads', 4) * 2
    futures = set()
    visited = {root_dir}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures.add(executor.submit(alist_api_list, root_dir))
        future_dirs = {}
        for future in list(futures):
            future_dirs[future] = root_dir

        while futures:
            check_strm_control(config['id'], "AList 目录扫描")
            done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                check_strm_control(config['id'], "AList 目录扫描")
                current_dir = future_dirs.pop(future, root_dir)
                result = future.result()
                with counter_lock:
                    dir_scan_counter += 1
                    if dir_scan_counter % 20 == 0:
                        add_log("INFO", f"🔎 AList 扫描进度: 已深入 {dir_scan_counter} 个子目录...")

                local_relative_path = alist_rel_dir(current_dir, root_dir)
                local_directory = join_output_path(config['target_directory'], local_relative_path)
                try:
                    os.makedirs(local_fs_path(local_directory), exist_ok=True)
                except Exception as e:
                    add_log("ERROR", f"❌ 创建 STRM 本地目录失败: [{local_directory}] -> {e}")
                    continue

                for item in result:
                    check_strm_control(config['id'], "AList 文件比对")
                    name = item.get("name") or ""
                    if not name:
                        continue
                    remote_path = posixpath.join(current_dir.rstrip("/"), name)
                    if item.get("is_dir"):
                        if remote_path not in visited:
                            visited.add(remote_path)
                            child_future = executor.submit(alist_api_list, remote_path)
                            future_dirs[child_future] = remote_path
                            futures.add(child_future)
                        continue

                    file_extension = os.path.splitext(name)[1].lower().lstrip('.')
                    if file_extension in script_config['video_formats']:
                        with counter_lock:
                            video_file_counter += 1
                        strm_file_name = os.path.splitext(os.path.basename(name))[0] + ".strm"
                        strm_file_path = os.path.join(local_directory, strm_file_name)
                        relative_path = os.path.relpath(strm_file_path, config['target_directory'])
                        if config['update_mode'] == 'incremental' and relative_path in existing_records:
                            with counter_lock:
                                existing_strm_file_counter += 1
                        else:
                            with counter_lock:
                                strm_tasks.append((remote_path, int(item.get("size") or 0), local_directory, relative_path, strm_file_name))
                    elif config['download_enabled'] == 1 and file_extension in meta_formats:
                        local_file_name = os.path.basename(name)
                        local_file_path = os.path.join(local_directory, local_file_name)
                        relative_path = os.path.relpath(local_file_path, config['target_directory'])
                        if config['update_mode'] == 'incremental' and (relative_path in existing_records or os.path.exists(local_fs_path(local_file_path))):
                            pass
                        else:
                            with counter_lock:
                                    metadata_tasks.append((remote_path, local_directory, relative_path, local_file_name))


def internal_root_is_default(drive, root_id):
    root_id = str(root_id or "").strip()
    if not root_id:
        return True
    return root_id == ("root" if drive == "aliyun" else "0")


def internal_default_root_id(drive):
    return "root" if drive == "aliyun" else "0"


def internal_item_name(provider, item):
    return provider.get_name(item, "") or item.get("name") or item.get("file_name") or item.get("filename") or ""


def internal_item_is_folder(drive, item):
    if drive == "quark":
        return item.get("file_type") == 0
    if drive == "aliyun":
        return item.get("type") == "folder"
    if drive == "115":
        return bool(item.get("cid")) and not item.get("fid")
    if drive == "123":
        return int(item.get("type") or 0) == 1
    return False


def internal_item_folder_id(drive, item):
    if drive == "quark":
        return item.get("fid")
    if drive == "aliyun":
        return item.get("file_id")
    if drive == "115":
        return item.get("cid")
    if drive == "123":
        return item.get("fileId")
    return ""


def find_internal_folder_path(provider, drive, target_id, max_dirs=2000):
    target_id = str(target_id or "").strip()
    if internal_root_is_default(drive, target_id):
        return f"/{drive}"
    cache_key = (drive, target_id)
    with internal_id_path_lock:
        if cache_key in internal_id_path_cache:
            return internal_id_path_cache[cache_key]

    root_id = internal_default_root_id(drive)
    queue = [(root_id, f"/{drive}")]
    visited = {root_id}
    scanned = 0
    while queue and scanned < max_dirs:
        current_id, current_path = queue.pop(0)
        scanned += 1
        try:
            items = provider.list_children(current_id) or []
        except Exception as exc:
            add_log("WARNING", f"STRM ID path lookup failed at {drive}:{current_id} -> {exc}", module="strm")
            continue

        for item in items:
            if not internal_item_is_folder(drive, item):
                continue
            folder_id = str(internal_item_folder_id(drive, item) or "")
            if not folder_id or folder_id in visited:
                continue
            folder_name = internal_item_name(provider, item)
            folder_path = posixpath.join(current_path.rstrip("/"), folder_name)
            if folder_id == target_id:
                with internal_id_path_lock:
                    internal_id_path_cache[cache_key] = folder_path
                return folder_path
            visited.add(folder_id)
            queue.append((folder_id, folder_path))

    add_log("WARNING", f"STRM could not map {drive} directory ID to AList path: {target_id}", module="strm")
    return ""


def internal_item_play_id(drive, item):
    if drive == "quark":
        return item.get("fid")
    if drive == "aliyun":
        return item.get("file_id")
    if drive == "115":
        return item.get("pc") or item.get("pick_code") or item.get("pickcode")
    if drive == "123":
        return item.get("fileId")
    return ""


def make_internal_id_ref(drive, play_id, name):
    return f"internal-id://{drive}/{quote(str(play_id), safe='')}?name={quote(str(name or ''), safe='')}"


def parse_internal_id_ref(ref):
    parsed = urlparse(ref)
    return parsed.netloc, unquote(parsed.path.strip("/")), unquote(parsed.query.replace("name=", "", 1)) if parsed.query.startswith("name=") else ""


def scan_internal_directories_by_id(config, script_config, existing_records):
    global video_file_counter, existing_strm_file_counter, strm_tasks, metadata_tasks, dir_scan_counter

    drive = INTERNAL_SOURCE_DRIVE.get(config.get("source_type"), "")
    provider = INTERNAL_DRIVE_PROVIDERS.get(drive)
    root_id = str(config.get("root_id") or "").strip()
    if not provider or internal_root_is_default(drive, root_id):
        scan_alist_directories_concurrently(config, script_config, existing_records)
        return

    config['target_directory'] = normalize_target_directory(config)
    try:
        os.makedirs(local_fs_path(config['target_directory']), exist_ok=True)
    except Exception as e:
        add_log("ERROR", f"❌ STRM 输出根目录不可用: [{config['target_directory']}] -> {e}")
        return

    meta_formats = script_config['subtitle_formats'] + script_config['image_formats'] + script_config['metadata_formats']
    add_log("INFO", f"🎯 STRM 内置节点 [{config['config_name']}] 使用指定目录 ID 扫描: {drive}:{root_id}")

    alist_root_path = ""
    if INTERNAL_STRM_BACKEND == "alist":
        alist_root_path = find_internal_folder_path(provider, drive, root_id)
        if alist_root_path:
            add_log("INFO", f"STRM will scan directory ID {drive}:{root_id} and write AList paths under {alist_root_path}", module="strm")
        else:
            add_log("WARNING", f"STRM directory ID {drive}:{root_id} is active, but AList path was not found. Playback will fallback to ID proxy for this node.", module="strm")

    max_workers = script_config.get('download_threads', 4) * 2
    futures = set()
    visited = {root_id}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures.add(executor.submit(provider.list_children, root_id))
        future_dirs = {}
        future_dirs[next(iter(futures))] = ("", root_id)

        while futures:
            check_strm_control(config['id'], "指定目录扫描")
            done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                check_strm_control(config['id'], "指定目录扫描")
                rel_dir, current_id = future_dirs.pop(future, ("", root_id))
                try:
                    result = future.result() or []
                except Exception as exc:
                    add_log("ERROR", f"❌ STRM 指定目录扫描失败: {drive}:{current_id} -> {exc}")
                    continue

                with counter_lock:
                    dir_scan_counter += 1
                    if dir_scan_counter % 20 == 0:
                        add_log("INFO", f"📁 STRM 指定目录扫描进度: 已深入 {dir_scan_counter} 个子目录...")

                local_directory = join_output_path(config['target_directory'], rel_dir)
                try:
                    os.makedirs(local_fs_path(local_directory), exist_ok=True)
                except Exception as e:
                    add_log("ERROR", f"❌ 创建 STRM 本地目录失败: [{local_directory}] -> {e}")
                    continue

                for item in result:
                    check_strm_control(config['id'], "指定目录文件比对")
                    name = internal_item_name(provider, item)
                    if not name:
                        continue
                    if internal_item_is_folder(drive, item):
                        child_id = str(internal_item_folder_id(drive, item) or "")
                        if child_id and child_id not in visited:
                            visited.add(child_id)
                            child_rel = posixpath.join(rel_dir, name) if rel_dir else name
                            child_future = executor.submit(provider.list_children, child_id)
                            futures.add(child_future)
                            future_dirs[child_future] = (child_rel, child_id)
                        continue

                    file_extension = os.path.splitext(name)[1].lower().lstrip('.')
                    play_id = internal_item_play_id(drive, item)
                    if not play_id:
                        continue
                    if INTERNAL_STRM_BACKEND == "alist" and alist_root_path:
                        remote_ref = posixpath.join(alist_root_path.rstrip("/"), rel_dir, name)
                    else:
                        remote_ref = make_internal_id_ref(drive, play_id, name)
                    if file_extension in script_config['video_formats']:
                        with counter_lock:
                            video_file_counter += 1
                        strm_file_name = os.path.splitext(name)[0] + ".strm"
                        strm_file_path = os.path.join(local_directory, strm_file_name)
                        relative_path = os.path.relpath(strm_file_path, config['target_directory'])
                        if config['update_mode'] == 'incremental' and relative_path in existing_records:
                            with counter_lock:
                                existing_strm_file_counter += 1
                        else:
                            with counter_lock:
                                strm_tasks.append((remote_ref, provider.get_size(item), local_directory, relative_path, strm_file_name))
                    elif config['download_enabled'] == 1 and file_extension in meta_formats:
                        local_file_path = os.path.join(local_directory, name)
                        relative_path = os.path.relpath(local_file_path, config['target_directory'])
                        if config['update_mode'] == 'incremental' and (relative_path in existing_records or os.path.exists(local_fs_path(local_file_path))):
                            pass
                        else:
                            with counter_lock:
                                metadata_tasks.append((remote_ref, local_directory, relative_path, name))

def connect_webdav(config):
    username = config['username'] or None
    password = config['password'] or None
    return easywebdav.connect(
        host=config['host'], port=config['port'], username=username,
        password=password, protocol=config['protocol']
    )

def get_webdav_client(config):
    if not hasattr(thread_local, 'client'):
        add_log("INFO", f"🔌 正在分配线程并建立 WebDAV 连接 -> {config['host']}:{config['port']}")
        thread_local.client = connect_webdav(config)
    return thread_local.client

def get_existing_records(config_id):
    conn = get_db()
    rows = conn.execute("SELECT local_path FROM strm_records WHERE config_id=?", (config_id,)).fetchall()
    conn.close()
    return set(row['local_path'] for row in rows)

def record_success(config_id, file_name, local_path):
    with db_lock:
        try:
            conn = get_db()
            conn.execute("INSERT OR IGNORE INTO strm_records (config_id, file_name, local_path) VALUES (?, ?, ?)", 
                         (config_id, file_name, local_path))
            conn.commit()
            conn.close()
        except:
            pass

def fetch_dir_task(directory, config):
    try:
        check_strm_control(config['id'], "WebDAV 目录读取")
        min_sec, max_sec = config['interval']
        time.sleep(random.uniform(min_sec, max_sec))
        check_strm_control(config['id'], "WebDAV 目录读取")
        
        client = get_webdav_client(config)
        safe_dir = directory if directory.endswith('/') else directory + '/'
        return directory, client.ls(safe_dir)
    except Exception as e:
        add_log("ERROR", f"❌ 读取 WebDAV 目录失败 [{directory}] -> 错误原因: {str(e)}")
        return directory, e

def scan_directories_concurrently(config, script_config, existing_records):
    global video_file_counter, existing_strm_file_counter, strm_tasks, metadata_tasks, dir_scan_counter
    
    config['target_directory'] = normalize_target_directory(config)
    try:
        os.makedirs(local_fs_path(config['target_directory']), exist_ok=True)
    except Exception as e:
        add_log("ERROR", f"❌ STRM 输出根目录不可用: [{config['target_directory']}] -> {e}")
        return

    root_dir = config['rootpath']
    if config.get('source_type') not in INTERNAL_SOURCE_TYPES and not root_dir.startswith('/dav'):
        root_dir = '/dav' + (root_dir if root_dir.startswith('/') else '/' + root_dir)
    if not root_dir.endswith('/'):
        root_dir += '/'
    config['rootpath'] = root_dir

    # 合并所有被允许下载的附属元数据扩展名
    meta_formats = script_config['subtitle_formats'] + script_config['image_formats'] + script_config['metadata_formats']

    add_log("INFO", f"📂 开始请求并扫描云端主目录: {root_dir}")

    max_workers = script_config.get('download_threads', 4) * 2 
    futures = set()
    visited = set()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        visited.add(root_dir)
        futures.add(executor.submit(fetch_dir_task, root_dir, config))
        
        while futures:
            check_strm_control(config['id'], "WebDAV 目录扫描")
            done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                check_strm_control(config['id'], "WebDAV 目录扫描")
                current_dir, result = future.result()
                
                with counter_lock:
                    dir_scan_counter += 1
                    if dir_scan_counter % 20 == 0:
                        add_log("INFO", f"🔍 扫描进度: 已深入遍历 {dir_scan_counter} 个云端子目录...")

                if isinstance(result, Exception):
                    continue
                    
                decoded_directory = unquote(current_dir)
                local_relative_path = decoded_directory.replace(config['rootpath'], '', 1).lstrip('/')
                local_directory = join_output_path(config['target_directory'], local_relative_path)
                try:
                    os.makedirs(local_fs_path(local_directory), exist_ok=True)
                except Exception as e:
                    add_log("ERROR", f"❌ 创建 STRM 本地目录失败: [{local_directory}] -> {e}")
                    continue

                for f in result:
                    check_strm_control(config['id'], "WebDAV 文件比对")
                    is_directory = f.name.endswith('/')
                    if is_directory:
                        if f.name != current_dir and f.name not in visited:
                            visited.add(f.name)
                            futures.add(executor.submit(fetch_dir_task, f.name, config))
                    else:
                        file_extension = os.path.splitext(f.name)[1].lower().lstrip('.')
                        
                        # 情况一：如果是视频文件，创建 STRM 映射任务
                        if file_extension in script_config['video_formats']:
                            with counter_lock: video_file_counter += 1
                            
                            decoded_file_name = unquote(f.name)
                            strm_file_name = os.path.splitext(os.path.basename(decoded_file_name))[0] + ".strm"
                            strm_file_path = os.path.join(local_directory, strm_file_name)
                            relative_path = os.path.relpath(strm_file_path, config['target_directory'])
                            
                            if config['update_mode'] == 'incremental' and relative_path in existing_records:
                                with counter_lock: existing_strm_file_counter += 1
                            else:
                                with counter_lock:
                                    strm_tasks.append((f.name, f.size, local_directory, relative_path, strm_file_name))
                        
                        # 情况二：如果是字幕/图片/NFO且开启了下载，创建真实文件下载任务
                        elif config['download_enabled'] == 1 and file_extension in meta_formats:
                            decoded_file_name = unquote(f.name)
                            local_file_name = os.path.basename(decoded_file_name)
                            local_file_path = os.path.join(local_directory, local_file_name)
                            relative_path = os.path.relpath(local_file_path, config['target_directory'])
                            
                            # 增量模式下，如果数据库有记录 或 本地磁盘已存在该文件，则跳过
                            if config['update_mode'] == 'incremental' and (relative_path in existing_records or os.path.exists(local_fs_path(local_file_path))):
                                pass
                            else:
                                with counter_lock:
                                    metadata_tasks.append((f.name, local_directory, relative_path, local_file_name))

def create_strm_file(file_name, file_size, config, local_directory, relative_path, strm_file_name, size_threshold):
    global strm_file_counter
    check_strm_control(config['id'], "STRM 文件写入")
    if file_size < size_threshold * (1024 * 1024): return

    min_sec, max_sec = config['interval']
    time.sleep(random.uniform(min_sec, max_sec))
    check_strm_control(config['id'], "STRM 文件写入")

    if str(file_name).startswith("internal-id://"):
        drive_name, play_id, display_name = parse_internal_id_ref(file_name)
        http_link = f"{INTERNAL_PLAY_PUBLIC_URL}/play_id/{drive_name}/{quote(play_id, safe='')}"
        if display_name:
            http_link += f"?name={quote(display_name, safe='')}"
    elif config.get('source_type') in INTERNAL_SOURCE_TYPES:
        clean_parts = [quote(part) for part in unquote(file_name).split('/') if part]
        drive_name = clean_parts[0] if clean_parts and clean_parts[0] in {"115", "aliyun", "quark", "123"} else INTERNAL_SOURCE_DRIVE.get(config.get('source_type'), "")
        rel_parts = clean_parts[1:] if clean_parts and clean_parts[0] == drive_name else clean_parts
        if INTERNAL_STRM_BACKEND == "alist":
            decoded_path = "/" + "/".join([unquote(part) for part in ([drive_name] + rel_parts) if part])
            sign = get_alist_sign(decoded_path)
            encoded_path = "/".join([quote(part) for part in decoded_path.strip("/").split("/") if part])
            http_link = f"{INTERNAL_ALIST_PUBLIC_URL}/d/{encoded_path}"
            if sign:
                http_link += f"?sign={quote(sign)}"
        else:
            if drive_name == "aliyun" and ALIYUN_STRM_MODE == "preview":
                http_link = f"{INTERNAL_PLAY_PUBLIC_URL}/play/aliyun_preview/{'/'.join(rel_parts)}"
            elif drive_name == "quark" and QUARK_STRM_MODE == "preview":
                http_link = f"{INTERNAL_PLAY_PUBLIC_URL}/play/quark_preview/{'/'.join(rel_parts)}"
            else:
                http_link = f"{INTERNAL_PLAY_PUBLIC_URL}/play/{drive_name}/{'/'.join(rel_parts)}"
    else:
        clean_file_name = file_name.replace('/dav', '')
        http_link = f"{config['protocol']}://{config['host']}:{config['port']}/d{clean_file_name}"
    strm_file_path = os.path.join(local_directory, strm_file_name)

    try:
        os.makedirs(local_fs_path(local_directory), exist_ok=True)
        with open(local_fs_path(strm_file_path), 'w', encoding='utf-8') as strm_file:
            strm_file.write(http_link)
        os.chmod(local_fs_path(strm_file_path), 0o777)
        record_success(config['id'], strm_file_name, relative_path)
        
        with counter_lock: 
            strm_file_counter += 1
            if strm_file_counter % 50 == 0:
                add_log("INFO", f"⏳ STRM写入进度: 已成功映射 {strm_file_counter} 个视频文件。")
    except Exception as e:
        add_log("ERROR", f"❌ 写入本地 STRM 文件失败: [{strm_file_path}] -> 原因: {str(e)}")

# 【新增】真实下载元数据文件的核心函数
def download_metadata_file(remote_file_name, config, local_directory, relative_path, local_file_name):
    global metadata_file_counter
    check_strm_control(config['id'], "元数据下载")
    local_file_path = os.path.join(local_directory, local_file_name)
    
    # 二次防错：如果本地正好存在，跳过不下载
    if os.path.exists(local_fs_path(local_file_path)) and os.path.getsize(local_fs_path(local_file_path)) > 0:
        record_success(config['id'], local_file_name, relative_path)
        return

    min_sec, max_sec = config['interval']
    time.sleep(random.uniform(min_sec, max_sec))
    check_strm_control(config['id'], "元数据下载")

    try:
        if str(remote_file_name).startswith("internal-id://"):
            drive_name, play_id, display_name = parse_internal_id_ref(remote_file_name)
            http_link = f"{INTERNAL_PLAY_PUBLIC_URL}/play_id/{drive_name}/{quote(play_id, safe='')}"
            if display_name:
                http_link += f"?name={quote(display_name, safe='')}"
            with requests.get(http_link, stream=True, timeout=120) as res:
                res.raise_for_status()
                with open(local_fs_path(local_file_path), "wb") as fh:
                    for chunk in res.iter_content(chunk_size=1024 * 1024):
                        check_strm_control(config['id'], "元数据下载")
                        if chunk:
                            fh.write(chunk)
        elif config.get('source_type') in INTERNAL_SOURCE_TYPES:
            decoded_path = "/" + "/".join([part for part in unquote(remote_file_name).split("/") if part])
            sign = get_alist_sign(decoded_path)
            if not sign:
                raise Exception(f"AList 未返回文件签名，无法下载: {decoded_path}")
            encoded_path = "/".join([quote(part) for part in decoded_path.strip("/").split("/") if part])
            http_link = f"{INTERNAL_ALIST_PUBLIC_URL}/d/{encoded_path}"
            http_link += f"?sign={quote(sign)}"
            with requests.get(http_link, stream=True, timeout=120) as res:
                res.raise_for_status()
                with open(local_fs_path(local_file_path), "wb") as fh:
                    for chunk in res.iter_content(chunk_size=1024 * 1024):
                        check_strm_control(config['id'], "元数据下载")
                        if chunk:
                            fh.write(chunk)
        else:
            client = get_webdav_client(config)
            client.download(remote_file_name, local_file_path)
        is_valid, reason = validate_downloaded_metadata(local_file_path)
        if not is_valid:
            try:
                os.remove(local_fs_path(local_file_path))
            except Exception:
                pass
            raise Exception(reason)
        os.chmod(local_fs_path(local_file_path), 0o777)
        record_success(config['id'], local_file_name, relative_path)
        
        with counter_lock: 
            metadata_file_counter += 1
            if metadata_file_counter % 20 == 0:
                add_log("INFO", f"📥 元数据下载进度: 已成功拉取 {metadata_file_counter} 个封面/字幕文件。")
    except Exception as e:
        add_log("ERROR", f"❌ 下载元数据文件失败: [{local_file_name}] -> 原因: {str(e)}")

def run_generation_body(config_id):
    global strm_file_counter, metadata_file_counter, video_file_counter, existing_strm_file_counter, strm_tasks, metadata_tasks, dir_scan_counter
    config = get_webdav_config(config_id)
    if not config:
        add_log("ERROR", f"❌ 找不到节点配置 (ID: {config_id})，生成任务已终止。")
        return
    
    script_config = get_script_config()
    
    add_log("INFO", f"🎥 STRM 引擎: 启动节点 [{config['config_name']}] 的全自动生成作业...")
    
    existing_records = get_existing_records(config['id']) 
    add_log("INFO", f"📚 数据库比对缓存加载完毕，该节点共命中 {len(existing_records)} 条历史记录。")
    
    config['target_directory'] = normalize_target_directory(config)
    try:
        os.makedirs(local_fs_path(config['target_directory']), exist_ok=True)
    except Exception as e:
        add_log("ERROR", f"❌ STRM 输出根目录不可用: [{config['target_directory']}] -> {e}")
        return

    if config.get('source_type') in INTERNAL_SOURCE_TYPES:
        scan_internal_directories_by_id(config, script_config, existing_records)
    else:
        scan_directories_concurrently(config, script_config, existing_records)
    
    if len(strm_tasks) == 0 and len(metadata_tasks) == 0:
        add_log("INFO", f"✅ STRM 引擎结束: 累计深入 {dir_scan_counter} 个目录。本次未发现新视频与未下载的元数据文件。")
        return

    add_log("INFO", f"🚀 STRM 引擎: 捕获到 {len(strm_tasks)} 个全新视频，及 {len(metadata_tasks)} 个待下载附属元数据！开启 {script_config['download_threads']} 线程处理中...")
    
    with ThreadPoolExecutor(max_workers=script_config['download_threads']) as executor:
        futures = []
        # 1. 提交 STRM 写入任务
        for t in strm_tasks:
            check_strm_control(config['id'], "提交 STRM 写入任务")
            futures.append(executor.submit(create_strm_file, t[0], t[1], config, t[2], t[3], t[4], script_config['size_threshold']))
        # 2. 提交 元数据 下载任务
        for m in metadata_tasks:
            check_strm_control(config['id'], "提交元数据下载任务")
            futures.append(executor.submit(download_metadata_file, m[0], config, m[1], m[2], m[3]))
            
        for future in as_completed(futures):
            check_strm_control(config['id'], "等待 STRM 写入完成")
            future.result()

    add_log("SUCCESS", f"🎉 STRM 作业圆满完成！本次新增映射 {strm_file_counter} 个视频，真实下载了 {metadata_file_counter} 个字幕/元数据，并已安全更新至缓存。")

def main(config_id):
    start_strm_job(config_id, os.getpid())
    try:
        run_generation_body(config_id)
        finish_strm_job(config_id, "completed", "STRM 生成任务已完成")
    except StrmControlStopped as exc:
        add_log("WARNING", f"STRM 生成任务已结束，节点 ID: {config_id}，原因: {exc}", module="strm")
        finish_strm_job(config_id, "stopped", str(exc))
    except Exception as exc:
        add_log("ERROR", f"STRM 生成任务异常退出，节点 ID: {config_id}，原因: {exc}", module="strm")
        finish_strm_job(config_id, "failed", str(exc))
        raise


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
