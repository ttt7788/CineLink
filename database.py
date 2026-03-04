import sqlite3

def init_db():
    conn = sqlite3.connect('tmdb_system.db')
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS system_configs (config_key VARCHAR(50) UNIQUE PRIMARY KEY, config_value VARCHAR(255))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS media_items (tmdb_id INTEGER PRIMARY KEY, media_type VARCHAR(20), title VARCHAR(255), overview TEXT, poster_path VARCHAR(255), add_date DATE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER UNIQUE, status VARCHAR(20) DEFAULT 'pending')''')
    try:
        cursor.execute("ALTER TABLE subscriptions ADD COLUMN drive_type VARCHAR(20) DEFAULT '115'")
    except sqlite3.OperationalError:
        pass 
    cursor.execute('''CREATE TABLE IF NOT EXISTS system_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, level VARCHAR(20), message TEXT, created_at DATETIME)''')
    
    default_configs = [
        ('api_key', '5ac680068ecbeded86de5c9cca4bdf70'), ('api_domain', 'https://api.tmdb.org'),
        ('image_domain', 'https://image.tmdb.org'), ('pansou_domain', 'http://192.168.68.200:8080'),
        ('cookie_115', ''), ('cookie_quark', ''), ('token_aliyun', ''),
        ('quark_save_dir', '0'), ('aliyun_save_dir', 'root'), 
        ('cron_expression', '0 * * * *'), ('cms_api_url', 'http://192.168.68.200:8090'),
        ('cms_api_token', 'cloud_media_sync'), ('last_sync_date', ''),
        ('auto_subscribe_new', '0'), 
        ('auto_subscribe_drive', '115')  # 【新增】默认值为115网盘
    ]
    cursor.executemany('INSERT OR IGNORE INTO system_configs (config_key, config_value) VALUES (?, ?)', default_configs)

    cursor.execute('''CREATE TABLE IF NOT EXISTS strm_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, config_name TEXT, url TEXT, username TEXT, 
                    password TEXT, rootpath TEXT, target_directory TEXT, download_enabled INTEGER DEFAULT 1,
                    update_mode TEXT DEFAULT 'incremental', download_interval_range TEXT DEFAULT '1-3')''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS strm_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, video_formats TEXT, subtitle_formats TEXT,
                    image_formats TEXT, metadata_formats TEXT, size_threshold INTEGER DEFAULT 100, download_threads INTEGER DEFAULT 4)''')
    
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

    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect('tmdb_system.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_sys_config():
    conn = get_db()
    rows = conn.execute("SELECT config_key, config_value FROM system_configs").fetchall()
    conn.close()
    return {row['config_key']: row['config_value'] for row in rows}