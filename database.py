import sqlite3

def init_db():
    conn = sqlite3.connect('tmdb_system.db')
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS system_configs 
                      (config_key VARCHAR(50) UNIQUE PRIMARY KEY, config_value VARCHAR(255))''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS media_items 
                      (tmdb_id INTEGER PRIMARY KEY, media_type VARCHAR(20), title VARCHAR(255), 
                       overview TEXT, poster_path VARCHAR(255), add_date DATE)''')
        
    cursor.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER UNIQUE, status VARCHAR(20) DEFAULT 'pending')''')
                      
    try:
        cursor.execute("ALTER TABLE subscriptions ADD COLUMN drive_type VARCHAR(20) DEFAULT '115'")
    except sqlite3.OperationalError:
        pass 
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS system_logs 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, level VARCHAR(20), message TEXT, created_at DATETIME)''')
    
    default_configs = [
        ('api_key', '5ac680068ecbeded86de5c9cca4bdf70'),
        ('api_domain', 'https://api.tmdb.org'),
        ('image_domain', 'https://image.tmdb.org'),
        ('pansou_domain', 'http://192.168.68.200:8080'),
        ('cookie_115', ''),
        ('cookie_quark', ''),
        ('token_aliyun', ''),
        ('quark_save_dir', '0'),     # 新增：夸克转存目录ID，默认 0(根目录)
        ('aliyun_save_dir', 'root'), # 新增：阿里云转存目录ID，默认 root
        ('cron_expression', '0 * * * *'),
        ('cms_api_url', 'http://192.168.68.200:8090'),
        ('cms_api_token', 'cloud_media_sync'),
        ('last_sync_date', '') 
    ]
    cursor.executemany('INSERT OR IGNORE INTO system_configs (config_key, config_value) VALUES (?, ?)', default_configs)
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