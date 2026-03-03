import httpx
import datetime
from fastapi import APIRouter, HTTPException
from database import get_db, get_sys_config
from models import ConfigModel, SubscribeModel, BatchSubscribeModel, BatchDeleteModel, SaveLinkModel, DriveListReq, DriveActionReq, QrcodeStatusModel, QrcodeLoginModel
from logger import get_logs
from drive_api import QuarkDrive, AliyunDrive

router = APIRouter()

@router.get("/api/config")
def get_config(): return get_sys_config()

@router.post("/api/config")
def update_config(config: ConfigModel):
    conn = get_db()
    try:
        fields = [
            ('api_domain', config.api_domain), ('image_domain', config.image_domain), 
            ('api_key', config.api_key), ('pansou_domain', config.pansou_domain), 
            ('cron_expression', config.cron_expression), ('cms_api_url', config.cms_api_url), 
            ('cms_api_token', config.cms_api_token), ('cookie_quark', config.cookie_quark), 
            ('token_aliyun', config.token_aliyun), ('quark_save_dir', config.quark_save_dir), 
            ('aliyun_save_dir', config.aliyun_save_dir), ('auto_subscribe_new', config.auto_subscribe_new),
            ('auto_subscribe_drive', config.auto_subscribe_drive) # 【新增】保存默认订阅网盘
        ]
        for key, value in fields: conn.execute("REPLACE INTO system_configs (config_key, config_value) VALUES (?, ?)", (key, value))
        conn.commit()
        return {"message": "配置保存成功"}
    finally: conn.close()

@router.get("/api/sync")
async def sync_daily_data():
    from scheduler import sync_tmdb_data
    import asyncio
    asyncio.create_task(sync_tmdb_data(force=True))
    return {"status": "success", "message": "全量数据入库操作已启动"}

@router.get("/api/local_media")
def get_local_media(type: str = 'hot', page: int = 1, size: int = 30):
    conn = get_db()
    today_str = datetime.date.today().isoformat()
    offset = (page - 1) * size
    sub_dict = {row['tmdb_id']: row['status'] for row in conn.execute("SELECT tmdb_id, status FROM subscriptions").fetchall()}
    if type == 'hot':
        c_q, d_q = "SELECT COUNT(*) FROM media_items WHERE add_date = ?", "SELECT * FROM media_items WHERE add_date = ? ORDER BY add_date DESC, tmdb_id DESC LIMIT ? OFFSET ?"
        p_c, p_d = (today_str,), (today_str, size, offset)
    elif type == 'movie':
        c_q, d_q = "SELECT COUNT(*) FROM media_items WHERE media_type='movie'", "SELECT * FROM media_items WHERE media_type='movie' ORDER BY add_date DESC, tmdb_id DESC LIMIT ? OFFSET ?"
        p_c, p_d = (), (size, offset)
    else:
        c_q, d_q = "SELECT COUNT(*) FROM media_items WHERE media_type='tv'", "SELECT * FROM media_items WHERE media_type='tv' ORDER BY add_date DESC, tmdb_id DESC LIMIT ? OFFSET ?"
        p_c, p_d = (), (size, offset)
    total = conn.execute(c_q, p_c).fetchone()[0]
    rows = conn.execute(d_q, p_d).fetchall()
    conn.close()
    return {"total": total, "items": [{**dict(row), 'sub_status': sub_dict.get(row['tmdb_id'])} for row in rows]}

@router.get("/api/search")
async def search_tmdb(query: str):
    config = get_sys_config()
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{config['api_domain']}/3/search/multi", params={"api_key": config['api_key'], "query": query, "language": "zh-CN"})
        data = res.json()
        conn = get_db()
        sub_dict = {row['tmdb_id']: row['status'] for row in conn.execute("SELECT tmdb_id, status FROM subscriptions").fetchall()}
        conn.close()
        for i in data.get('results', []): i['sub_status'] = sub_dict.get(i.get('id'))
        return data

@router.post("/api/subscribe")
def subscribe(media: SubscribeModel):
    conn = get_db()
    existing = conn.execute("SELECT status FROM subscriptions WHERE tmdb_id = ?", (media.tmdb_id,)).fetchone()
    if existing and not media.force:
        conn.close()
        return {"code": 409, "status": existing['status'], "message": "已存在"}
    today = datetime.date.today().isoformat()
    conn.execute("INSERT OR REPLACE INTO media_items (tmdb_id, media_type, title, overview, poster_path, add_date) VALUES (?,?,?,?,?,?)", (media.tmdb_id, media.media_type, media.title, media.overview, media.poster_path, today))
    if existing: conn.execute("UPDATE subscriptions SET status = 'pending', drive_type = ? WHERE tmdb_id = ?", (media.drive_type, media.tmdb_id))
    else: conn.execute("INSERT INTO subscriptions (tmdb_id, status, drive_type) VALUES (?, 'pending', ?)", (media.tmdb_id, media.drive_type))
    conn.commit(); conn.close()
    return {"code": 200, "message": "成功"}

@router.post("/api/subscribe/batch")
def batch_subscribe(data: BatchSubscribeModel):
    conn = get_db(); today = datetime.date.today().isoformat(); count = 0
    for media in data.items:
        existing = conn.execute("SELECT status FROM subscriptions WHERE tmdb_id = ?", (media.tmdb_id,)).fetchone()
        if existing and not media.force: continue
        conn.execute("INSERT OR REPLACE INTO media_items (tmdb_id, media_type, title, overview, poster_path, add_date) VALUES (?,?,?,?,?,?)", (media.tmdb_id, media.media_type, media.title, media.overview, media.poster_path, today))
        if existing: conn.execute("UPDATE subscriptions SET status = 'pending', drive_type = ? WHERE tmdb_id = ?", (media.drive_type, media.tmdb_id))
        else: conn.execute("INSERT INTO subscriptions (tmdb_id, status, drive_type) VALUES (?, 'pending', ?)", (media.tmdb_id, media.drive_type))
        count += 1
    conn.commit(); conn.close()
    return {"code": 200, "message": f"批量加入 {count} 个"}

@router.get("/api/subscriptions")
def get_subscriptions(status: str = 'pending'):
    conn = get_db()
    rows = conn.execute("SELECT s.status, s.drive_type, m.* FROM subscriptions s JOIN media_items m ON s.tmdb_id = m.tmdb_id WHERE s.status = ? ORDER BY s.id DESC", (status,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

@router.delete("/api/subscriptions/{tmdb_id}")
def unsubscribe(tmdb_id: int):
    conn = get_db(); conn.execute("DELETE FROM subscriptions WHERE tmdb_id = ?", (tmdb_id,)); conn.commit(); conn.close()
    return {"message": "取消"}

@router.post("/api/subscriptions/batch_delete")
def batch_delete_subscriptions(data: BatchDeleteModel):
    if not data.tmdb_ids: return {"message": "无"}
    conn = get_db()
    conn.execute(f"DELETE FROM subscriptions WHERE tmdb_id IN ({','.join('?' * len(data.tmdb_ids))})", data.tmdb_ids)
    conn.commit(); conn.close()
    return {"message": "删除成功"}

@router.get("/api/pansou_search")
async def search_ps(kw: str):
    c = get_sys_config()
    domain = c.get('pansou_domain', 'http://192.168.68.200:8080').rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(f"{domain}/api/search", json={"kw": kw})
            d = res.json()
            return d.get("data") if d.get("code") == 0 else d
    except Exception as e: return {"error": f"无法连接: {str(e)}", "merged_by_type": {}}

@router.post("/api/save_link")
async def api_save_link(req: SaveLinkModel):
    from scheduler import push_to_quark, push_to_aliyun, push_to_cms
    config = get_sys_config()
    success, msg = False, ""
    try:
        if req.drive_type == 'quark':
            save_dir = config.get('quark_save_dir', '0').split('-')[0].strip() if config.get('quark_save_dir') else "0"
            success, msg = await push_to_quark(config.get('cookie_quark', ''), req.url, req.pwd, save_dir)
        elif req.drive_type == 'aliyun':
            save_dir = config.get('aliyun_save_dir', 'root').split('-')[0].strip() if config.get('aliyun_save_dir') else "root"
            success, msg = await push_to_aliyun(config.get('token_aliyun', ''), req.url, req.pwd, save_dir)
        else:
            cms_url = config.get('cms_api_url', '')
            cms_token = config.get('cms_api_token', '')
            if not cms_url: return {"code": 400, "message": "未配置 CMS API"}
            success, msg = await push_to_cms(cms_url, cms_token, req.url)
            
        if success:
            conn = get_db(); today = datetime.date.today().isoformat()
            conn.execute("INSERT OR REPLACE INTO media_items (tmdb_id, media_type, title, overview, poster_path, add_date) VALUES (?,?,?,?,?,?)", (req.tmdb_id, req.media_type, req.title, "", req.poster_path, today))
            existing = conn.execute("SELECT status FROM subscriptions WHERE tmdb_id = ?", (req.tmdb_id,)).fetchone()
            if existing: conn.execute("UPDATE subscriptions SET status = 'success', drive_type = ? WHERE tmdb_id = ?", (req.drive_type, req.tmdb_id))
            else: conn.execute("INSERT INTO subscriptions (tmdb_id, status, drive_type) VALUES (?, 'success', ?)", (req.tmdb_id, req.drive_type))
            conn.commit(); conn.close()
            return {"code": 200, "message": "转存成功！"}
        return {"code": 500, "message": f"失败: {msg}"}
    except Exception as e: return {"code": 500, "message": f"异常: {str(e)}"}

@router.post("/api/drive/list")
async def api_drive_list(req: DriveListReq):
    config = get_sys_config()
    result = []
    try:
        if req.drive_type == 'quark':
            api = QuarkDrive(config.get('cookie_quark', ''))
            items, msg = await api.list_files(req.parent_id or "0")
            for i in items:
                result.append({"id": i.get('fid'), "name": i.get('file_name'), "is_folder": i.get('file_type') == 0, "size": i.get('size', 0), "updated_at": datetime.datetime.fromtimestamp(i.get('updated_at', 0)/1000).strftime('%Y-%m-%d %H:%M:%S') if i.get('updated_at') else ""})
        else:
            api = AliyunDrive(config.get('token_aliyun', ''))
            items, msg = await api.list_files(req.parent_id or "root")
            for i in items:
                result.append({"id": i.get('file_id'), "name": i.get('name'), "is_folder": i.get('type') == 'folder', "size": i.get('size', 0), "updated_at": i.get('updated_at', '').replace('T', ' ').replace('Z', '')})
        result.sort(key=lambda x: (not x['is_folder'], x['updated_at']), reverse=True)
        return {"code": 200, "data": result, "msg": msg}
    except Exception as e: return {"code": 500, "msg": str(e)}

@router.post("/api/drive/action")
async def api_drive_action(req: DriveActionReq):
    config = get_sys_config()
    api = QuarkDrive(config.get('cookie_quark', '')) if req.drive_type == 'quark' else AliyunDrive(config.get('token_aliyun', ''))
    try:
        if req.action == 'mkdir': success, msg = await api.make_dir(req.file_id, req.new_name)
        elif req.action == 'rename': success, msg = await api.rename(req.file_id, req.new_name)
        elif req.action == 'delete': success, msg = await api.delete(req.file_id)
        return {"code": 200 if success else 500, "msg": msg}
    except Exception as e: return {"code": 500, "msg": str(e)}

@router.get("/api/115/qrcode")
async def get_115_qr():
    async with httpx.AsyncClient() as client: return (await client.get("https://qrcodeapi.115.com/api/1.0/web/1.0/token/")).json()

@router.post("/api/115/status")
async def get_115_st(p: QrcodeStatusModel):
    async with httpx.AsyncClient() as client: return (await client.get(f"https://qrcodeapi.115.com/get/status/?uid={p.uid}&time={p.time}&sign={p.sign}")).json()

@router.post("/api/115/login")
async def log_115(p: QrcodeLoginModel):
    async with httpx.AsyncClient() as client:
        res = (await client.post("https://passportapi.115.com/app/1.0/web/1.0/login/qrcode/", data={"app": "web", "account": p.uid})).json()
        if res.get('state'):
            ck = "; ".join(f"{k}={v}" for k, v in res['data']['cookie'].items())
            conn = get_db(); conn.execute("REPLACE INTO system_configs (config_key, config_value) VALUES ('cookie_115', ?)", (ck,)); conn.commit(); conn.close()
            return {"message": "成功"}
    raise HTTPException(400, "失败")

@router.get("/api/logs")
def fetch_logs(): return get_logs(100)

@router.post("/api/tasks/trigger")
async def trigger_task():
    from scheduler import auto_subscription_task
    import asyncio
    asyncio.create_task(auto_subscription_task())
    return {"message": "启动成功"}