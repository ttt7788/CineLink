import sqlite3
import os

# 【核心修改1】将数据库存放于独立的 data 目录下，完美适配 Docker 目录挂载
DB_DIR = os.environ.get("CINELINK_DATA_DIR", "data")
DB_PATH = os.path.join(DB_DIR, "tmdb_system.db")

def init_db():
    # 1. 自动创建数据库存放目录
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
        
    # 2. 防呆检测：如果之前被 Docker 错误挂载成了文件夹，给出明显提示
    if os.path.isdir(DB_PATH):
        raise Exception(f"致命错误：{DB_PATH} 被错误地创建为了文件夹！请删除宿主机上的同名文件夹并重新启动。")

    # 3. 连接数据库（如果文件不存在，SQLite 会自动创建空 db 文件）
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS system_configs (config_key VARCHAR(50) UNIQUE PRIMARY KEY, config_value VARCHAR(255))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS media_items (
                    tmdb_id INTEGER PRIMARY KEY, media_type VARCHAR(20), title VARCHAR(255),
                    overview TEXT, poster_path VARCHAR(255), add_date DATE,
                    is_trending INTEGER DEFAULT 0, trend_date DATE,
                    popularity REAL DEFAULT 0, vote_average REAL DEFAULT 0)''')
    for sql in [
        "ALTER TABLE media_items ADD COLUMN is_trending INTEGER DEFAULT 0",
        "ALTER TABLE media_items ADD COLUMN trend_date DATE",
        "ALTER TABLE media_items ADD COLUMN popularity REAL DEFAULT 0",
        "ALTER TABLE media_items ADD COLUMN vote_average REAL DEFAULT 0",
    ]:
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError:
            pass
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_hot ON media_items(is_trending, trend_date, popularity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_type_popularity ON media_items(media_type, popularity)")
    cursor.execute('''CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER UNIQUE, status VARCHAR(20) DEFAULT 'pending')''')
    try:
        cursor.execute("ALTER TABLE subscriptions ADD COLUMN drive_type VARCHAR(20) DEFAULT '115'")
    except sqlite3.OperationalError:
        pass 
    cursor.execute('''CREATE TABLE IF NOT EXISTS series_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tmdb_id INTEGER NOT NULL,
                    drive_type VARCHAR(20) NOT NULL,
                    title VARCHAR(255),
                    cloud_parent_id TEXT,
                    cloud_path TEXT,
                    source_share_url TEXT,
                    source_share_pwd TEXT,
                    latest_episode_count INTEGER DEFAULT 0,
                    latest_item_name TEXT,
                    latest_item_updated_at TEXT,
                    last_checked_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tmdb_id, drive_type))''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_series_bindings_drive ON series_bindings(drive_type, tmdb_id)")
    cursor.execute('''CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level VARCHAR(20),
                    module VARCHAR(50) DEFAULT 'system',
                    message TEXT,
                    created_at DATETIME)''')
    try:
        cursor.execute("ALTER TABLE system_logs ADD COLUMN module VARCHAR(50) DEFAULT 'system'")
    except sqlite3.OperationalError:
        pass
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_module ON system_logs(module, id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level, id)")
    
    # 【核心修改2】将私人的虚拟模板数据全部置空，仅保留公共服务域名和系统开关状态
    default_configs = [
        ('api_key', ''), # 置空
        ('api_domain', 'https://api.tmdb.org'), # 保留官方公共域名
        ('image_domain', 'https://image.tmdb.org'), # 保留官方公共域名
        ('pansou_domain', ''), # 置空
        ('pancheck_domain', os.environ.get('CINELINK_PANCHECK_URL', '')),
        ('pancheck_enabled', '1'),
        ('cookie_115', ''),
        ('alist_115_cookie_source', ''),
        ('alist_115_qrcode_token', ''),
        ('alist_115_qrcode_source', ''),
        ('cookie_quark', ''),
        ('token_aliyun', ''),
        ('drive123_client_id', ''),
        ('drive123_client_secret', ''),
        ('drive115_save_dir', '0'),
        ('quark_save_dir', '0'),
        ('aliyun_save_dir', 'root'), 
        ('drive123_save_dir', '0'),
        ('cron_expression', '0 10,22 * * *'),
        ('cron_start_after', ''),
        ('last_sync_date', ''),
        ('last_trending_sync_date', ''),
        ('last_trending_sync_version', ''),
        ('last_movie_sync_date', ''),
        ('last_tv_sync_date', ''),
        ('last_base_sync_date', ''),
        ('auto_subscribe_new', '0'),
        ('auto_subscribe_drive', '115'),
        ('magnet_download_drive', '115'),
        ('ed2k_download_drive', '115'),
        ('pipeline_auto_organize', '0'),
        ('pipeline_organize_max_items', '30'),
        ('plugin_recycle_enabled', '0'),
        ('plugin_recycle_drives', '115,aliyun,quark'),
        ('plugin_recycle_interval_hours', '24'),
        ('plugin_recycle_115_password', ''),
        ('plugin_recycle_last_run', '')
    ]
    cursor.executemany('INSERT OR IGNORE INTO system_configs (config_key, config_value) VALUES (?, ?)', default_configs)

    cursor.execute('''CREATE TABLE IF NOT EXISTS strm_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT DEFAULT 'webdav',
                    config_name TEXT, url TEXT, username TEXT,
                    password TEXT, rootpath TEXT, root_id TEXT DEFAULT '', target_directory TEXT, download_enabled INTEGER DEFAULT 1,
                    update_mode TEXT DEFAULT 'incremental', download_interval_range TEXT DEFAULT '1-3')''')
    try:
        cursor.execute("ALTER TABLE strm_configs ADD COLUMN source_type TEXT DEFAULT 'webdav'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE strm_configs ADD COLUMN root_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    for source_type, config_key, fallback in (
        ("115_internal", "drive115_save_dir", "0"),
        ("aliyun_internal", "aliyun_save_dir", "root"),
        ("quark_internal", "quark_save_dir", "0"),
        ("123_internal", "drive123_save_dir", "0"),
    ):
        row = cursor.execute("SELECT config_value FROM system_configs WHERE config_key=?", (config_key,)).fetchone()
        root_id = ((row[0] if row else fallback) or fallback).split("-")[0].strip() or fallback
        cursor.execute(
            "UPDATE strm_configs SET root_id=? WHERE source_type=? AND COALESCE(root_id, '')=''",
            (root_id, source_type),
        )
    cursor.execute(
        "UPDATE strm_configs SET url='internal://alist' "
        "WHERE source_type IN ('115_internal','aliyun_internal','quark_internal')"
    )
    cursor.execute("UPDATE strm_configs SET config_name=REPLACE(config_name, '内置WebDAV', '内置挂载') WHERE config_name LIKE '%内置WebDAV%'")
    cursor.execute("UPDATE strm_configs SET config_name=REPLACE(config_name, '内置 WebDAV', '内置挂载') WHERE config_name LIKE '%内置 WebDAV%'")

    cursor.execute('''CREATE TABLE IF NOT EXISTS strm_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, video_formats TEXT, subtitle_formats TEXT,
                    image_formats TEXT, metadata_formats TEXT, size_threshold INTEGER DEFAULT 100, download_threads INTEGER DEFAULT 4)''')
    
    # STRM 的扩展名属于系统必备通用配置，保留以确保开箱即用
    cursor.execute("SELECT COUNT(*) FROM strm_settings")
    if cursor.fetchone()[0] == 0:
        cursor.execute('''INSERT INTO strm_settings (video_formats, subtitle_formats, image_formats, metadata_formats, size_threshold, download_threads) 
            VALUES (?, ?, ?, ?, ?, ?)''', ('mp4,mkv,avi,mov,flv,wmv,ts,m2ts', 'srt,ass,sub', 'jpg,png,bmp', 'nfo', 100, 4))
            
    cursor.execute('''CREATE TABLE IF NOT EXISTS strm_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, config_id INTEGER, file_name TEXT, local_path TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_strm_local_path ON strm_records(config_id, local_path)')

    cursor.execute('''CREATE TABLE IF NOT EXISTS strm_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_name TEXT, 
                    config_id INTEGER, cron_expression TEXT, is_enabled INTEGER DEFAULT 1)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS transfer_download_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    source_url TEXT NOT NULL,
                    pwd TEXT DEFAULT '',
                    link_type TEXT DEFAULT 'share',
                    drive_type TEXT DEFAULT '115',
                    save_dir TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    message TEXT DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transfer_download_tasks_status ON transfer_download_tasks(status, created_at)")

    conn.commit()
    conn.close()

def get_db():
    # 【核心修改3】这里必须使用 DB_PATH 变量！(之前旧代码这里写死的是 'tmdb_system.db'，导致了您的报错)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_sys_config():
    conn = get_db()
    rows = conn.execute("SELECT config_key, config_value FROM system_configs").fetchall()
    conn.close()
    return {row['config_key']: row['config_value'] for row in rows}
