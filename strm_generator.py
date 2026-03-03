import sys
import os
import time
import random
from urllib.parse import urlparse, unquote
import threading
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import easywebdav

from database import get_db
from logger import add_log

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

def get_webdav_config(config_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM strm_configs WHERE id=?", (config_id,)).fetchone()
    conn.close()
    if not row: return None
    
    parsed_url = urlparse(row['url'])
    protocol = parsed_url.scheme
    host = parsed_url.hostname
    port = parsed_url.port if parsed_url.port else (80 if protocol == 'http' else 443)
    
    try:
        min_int, max_int = map(float, str(row['download_interval_range']).split('-'))
    except:
        min_int, max_int = 1.0, 3.0
    
    return {
        'id': row['id'], 'config_name': row['config_name'], 'host': host, 'port': int(port), 
        'username': row['username'], 'password': row['password'], 
        'rootpath': row['rootpath'], 'protocol': protocol,
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

def connect_webdav(config):
    return easywebdav.connect(
        host=config['host'], port=config['port'], username=config['username'],
        password=config['password'], protocol=config['protocol']
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
        min_sec, max_sec = config['interval']
        time.sleep(random.uniform(min_sec, max_sec))
        
        client = get_webdav_client(config)
        safe_dir = directory if directory.endswith('/') else directory + '/'
        return directory, client.ls(safe_dir)
    except Exception as e:
        add_log("ERROR", f"❌ 读取 WebDAV 目录失败 [{directory}] -> 错误原因: {str(e)}")
        return directory, e

def scan_directories_concurrently(config, script_config, existing_records):
    global video_file_counter, existing_strm_file_counter, strm_tasks, metadata_tasks, dir_scan_counter
    
    root_dir = config['rootpath']
    if not root_dir.startswith('/dav'):
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
            done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                current_dir, result = future.result()
                
                with counter_lock:
                    dir_scan_counter += 1
                    if dir_scan_counter % 20 == 0:
                        add_log("INFO", f"🔍 扫描进度: 已深入遍历 {dir_scan_counter} 个云端子目录...")

                if isinstance(result, Exception):
                    continue
                    
                decoded_directory = unquote(current_dir)
                local_relative_path = decoded_directory.replace(config['rootpath'], '').lstrip('/')
                local_directory = os.path.join(config['target_directory'], local_relative_path)
                os.makedirs(local_directory, exist_ok=True)

                for f in result:
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
                            
                            decoded_file_name = unquote(f.name).replace('/dav/', '')
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
                            decoded_file_name = unquote(f.name).replace('/dav/', '')
                            local_file_name = os.path.basename(decoded_file_name)
                            local_file_path = os.path.join(local_directory, local_file_name)
                            relative_path = os.path.relpath(local_file_path, config['target_directory'])
                            
                            # 增量模式下，如果数据库有记录 或 本地磁盘已存在该文件，则跳过
                            if config['update_mode'] == 'incremental' and (relative_path in existing_records or os.path.exists(local_file_path)):
                                pass
                            else:
                                with counter_lock:
                                    metadata_tasks.append((f.name, local_directory, relative_path, local_file_name))

def create_strm_file(file_name, file_size, config, local_directory, relative_path, strm_file_name, size_threshold):
    global strm_file_counter
    if file_size < size_threshold * (1024 * 1024): return

    min_sec, max_sec = config['interval']
    time.sleep(random.uniform(min_sec, max_sec))

    clean_file_name = file_name.replace('/dav', '')
    http_link = f"{config['protocol']}://{config['host']}:{config['port']}/d{clean_file_name}"
    strm_file_path = os.path.join(local_directory, strm_file_name)

    try:
        with open(strm_file_path, 'w', encoding='utf-8') as strm_file:
            strm_file.write(http_link)
        os.chmod(strm_file_path, 0o777)
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
    local_file_path = os.path.join(local_directory, local_file_name)
    
    # 二次防错：如果本地正好存在，跳过不下载
    if os.path.exists(local_file_path) and os.path.getsize(local_file_path) > 0:
        record_success(config['id'], local_file_name, relative_path)
        return

    min_sec, max_sec = config['interval']
    time.sleep(random.uniform(min_sec, max_sec))

    try:
        client = get_webdav_client(config)
        client.download(remote_file_name, local_file_path)
        os.chmod(local_file_path, 0o777)
        record_success(config['id'], local_file_name, relative_path)
        
        with counter_lock: 
            metadata_file_counter += 1
            if metadata_file_counter % 20 == 0:
                add_log("INFO", f"📥 元数据下载进度: 已成功拉取 {metadata_file_counter} 个封面/字幕文件。")
    except Exception as e:
        add_log("ERROR", f"❌ 下载元数据文件失败: [{local_file_name}] -> 原因: {str(e)}")

def main(config_id):
    global strm_file_counter, metadata_file_counter, video_file_counter, existing_strm_file_counter, strm_tasks, metadata_tasks, dir_scan_counter
    config = get_webdav_config(config_id)
    if not config:
        add_log("ERROR", f"❌ 找不到节点配置 (ID: {config_id})，生成任务已终止。")
        return
    
    script_config = get_script_config()
    
    add_log("INFO", f"🎥 STRM 引擎: 启动节点 [{config['config_name']}] 的全自动生成作业...")
    
    existing_records = get_existing_records(config['id']) 
    add_log("INFO", f"📚 数据库比对缓存加载完毕，该节点共命中 {len(existing_records)} 条历史记录。")
    
    scan_directories_concurrently(config, script_config, existing_records)
    
    if len(strm_tasks) == 0 and len(metadata_tasks) == 0:
        add_log("INFO", f"✅ STRM 引擎结束: 累计深入 {dir_scan_counter} 个目录。本次未发现新视频与未下载的元数据文件。")
        return

    add_log("INFO", f"🚀 STRM 引擎: 捕获到 {len(strm_tasks)} 个全新视频，及 {len(metadata_tasks)} 个待下载附属元数据！开启 {script_config['download_threads']} 线程处理中...")
    
    with ThreadPoolExecutor(max_workers=script_config['download_threads']) as executor:
        futures = []
        # 1. 提交 STRM 写入任务
        for t in strm_tasks:
            futures.append(executor.submit(create_strm_file, t[0], t[1], config, t[2], t[3], t[4], script_config['size_threshold']))
        # 2. 提交 元数据 下载任务
        for m in metadata_tasks:
            futures.append(executor.submit(download_metadata_file, m[0], config, m[1], m[2], m[3]))
            
        for future in as_completed(futures):
            pass

    add_log("SUCCESS", f"🎉 STRM 作业圆满完成！本次新增映射 {strm_file_counter} 个视频，真实下载了 {metadata_file_counter} 个字幕/元数据，并已安全更新至缓存。")

if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)