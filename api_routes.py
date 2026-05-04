import httpx
import datetime
import base64
import io
import json
import urllib.parse
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, HTTPException
from database import get_db, get_sys_config
from models import ConfigModel, SubscribeModel, BatchSubscribeModel, BatchDeleteModel, SaveLinkModel, DriveListReq, DriveActionReq, QrcodeStatusModel, QrcodeLoginModel, AliyunQrcodeStatusModel, AliyunQrcodeLoginModel
from logger import get_logs, add_log
from drive_api import Drive115, QuarkDrive
from aliyun_drive_mobile import AliyunDrive

router = APIRouter()

UPSERT_MEDIA_SQL = '''
    INSERT INTO media_items (tmdb_id, media_type, title, overview, poster_path, add_date)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(tmdb_id) DO UPDATE SET
        media_type=excluded.media_type,
        title=excluded.title,
        overview=excluded.overview,
        poster_path=excluded.poster_path,
        add_date=excluded.add_date
'''

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
            ('auto_subscribe_drive', config.auto_subscribe_drive)
        ]
        for key, value in fields: conn.execute("REPLACE INTO system_configs (config_key, config_value) VALUES (?, ?)", (key, value))
        conn.commit()
        return {"message": "配置保存成功"}
    finally: conn.close()

@router.get("/api/sync")
async def sync_daily_data():
    config = get_sys_config()
    api_key = config.get('api_key', '').strip()
    
    if not api_key:
        add_log("WARNING", "手动触发 TMDB 采集失败：未配置 API Key")
        return {"status": "error", "message": "未配置 TMDB API Key，请先在【TMDB与盘搜源配置】中填写！"}
        
    add_log("INFO", "已检测到 TMDB API Key，后台马上开始采集数据...")
    from scheduler import sync_tmdb_data
    import asyncio
    asyncio.create_task(sync_tmdb_data(force=True, mode="all"))
    
    return {"status": "success", "message": "数据入库操作已马上启动，请留意系统运行日志！"}

@router.get("/api/local_media")
async def get_local_media(type: str = 'hot', page: int = 1, size: int = 30):
    page = max(1, page)
    size = min(max(1, size), 100)
    conn = get_db()
    today_str = datetime.date.today().isoformat()

    if type == 'hot':
        c_q_today = "SELECT COUNT(*) FROM media_items WHERE is_trending = 1 AND trend_date = ?"
        today_count = conn.execute(c_q_today, (today_str,)).fetchone()[0]

        if today_count == 0:
            conn.close()
            config = get_sys_config()
            if config.get('api_key'):
                from scheduler import sync_tmdb_data
                add_log("INFO", "🚀 首次访问触发：今日热门数据为空，立刻极速同步 (前10页)...")
                await sync_tmdb_data(force=True, mode="trending")
            conn = get_db()

    elif type in ['movie', 'tv']:
        type_count = conn.execute("SELECT COUNT(*) FROM media_items WHERE media_type = ?", (type,)).fetchone()[0]
        if type_count < 10000:
            conn.close()
            config = get_sys_config()
            if config.get('api_key'):
                from scheduler import sync_tmdb_data
                import asyncio
                add_log("INFO", f"🚀 首次访问触发：{type} 基础库不足({type_count}条)，后台静默补全 500 页...")
                asyncio.create_task(sync_tmdb_data(force=False, mode=type))
            conn = get_db()

    offset = (page - 1) * size
    sub_dict = {row['tmdb_id']: row['status'] for row in conn.execute("SELECT tmdb_id, status FROM subscriptions").fetchall()}
    
    if type == 'hot':
        c_q = "SELECT COUNT(*) FROM media_items WHERE is_trending = 1 AND trend_date = ?"
        d_q = "SELECT * FROM media_items WHERE is_trending = 1 AND trend_date = ? ORDER BY popularity DESC, vote_average DESC, tmdb_id DESC LIMIT ? OFFSET ?"
        p_c, p_d = (today_str,), (today_str, size, offset)
    elif type == 'movie':
        c_q = "SELECT COUNT(*) FROM media_items WHERE media_type='movie'"
        d_q = "SELECT * FROM media_items WHERE media_type='movie' ORDER BY popularity DESC, vote_average DESC, tmdb_id DESC LIMIT ? OFFSET ?"
        p_c, p_d = (), (size, offset)
    else:
        c_q = "SELECT COUNT(*) FROM media_items WHERE media_type='tv'"
        d_q = "SELECT * FROM media_items WHERE media_type='tv' ORDER BY popularity DESC, vote_average DESC, tmdb_id DESC LIMIT ? OFFSET ?"
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
    conn.execute(UPSERT_MEDIA_SQL, (media.tmdb_id, media.media_type, media.title, media.overview, media.poster_path, today))
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
        conn.execute(UPSERT_MEDIA_SQL, (media.tmdb_id, media.media_type, media.title, media.overview, media.poster_path, today))
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
    drive_type = req.drive_type
    url_lower = (req.url or "").lower()
    if "pan.quark.cn" in url_lower:
        drive_type = "quark"
    elif "alipan.com" in url_lower or "aliyundrive.com" in url_lower:
        drive_type = "aliyun"

    try:
        add_log("INFO", f"【手动转存】来源类型:{req.drive_type}，识别网盘:{drive_type}，链接:{req.url}")
        if drive_type == 'quark':
            save_dir = config.get('quark_save_dir', '0').split('-')[0].strip() if config.get('quark_save_dir') else "0"
            success, msg = await push_to_quark(config.get('cookie_quark', ''), req.url, req.pwd, save_dir)
        elif drive_type == 'aliyun':
            save_dir = config.get('aliyun_save_dir', 'root').split('-')[0].strip() if config.get('aliyun_save_dir') else "root"
            success, msg = await push_to_aliyun(config.get('token_aliyun', ''), req.url, req.pwd, save_dir)
        else:
            cms_url = config.get('cms_api_url', '')
            cms_token = config.get('cms_api_token', '')
            if not cms_url: return {"code": 400, "message": "未配置 CMS API"}
            success, msg = await push_to_cms(cms_url, cms_token, req.url)
            
        if success:
            conn = get_db(); today = datetime.date.today().isoformat()
            conn.execute(UPSERT_MEDIA_SQL, (req.tmdb_id, req.media_type, req.title, "", req.poster_path, today))
            existing = conn.execute("SELECT status FROM subscriptions WHERE tmdb_id = ?", (req.tmdb_id,)).fetchone()
            if existing: conn.execute("UPDATE subscriptions SET status = 'success', drive_type = ? WHERE tmdb_id = ?", (drive_type, req.tmdb_id))
            else: conn.execute("INSERT INTO subscriptions (tmdb_id, status, drive_type) VALUES (?, 'success', ?)", (req.tmdb_id, drive_type))
            conn.commit(); conn.close()
            add_log("SUCCESS", f"【手动转存】{req.title} 转存成功，目标:{drive_type}")
            return {"code": 200, "message": "转存成功！"}
        add_log("ERROR", f"【手动转存】{req.title} 转存失败: {msg}")
        return {"code": 500, "message": f"失败: {msg}"}
    except Exception as e:
        add_log("ERROR", f"【手动转存】异常: {str(e)}")
        return {"code": 500, "message": f"异常: {str(e)}"}

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
        elif req.drive_type == '115':
            api = Drive115(config.get('cookie_115', ''))
            items, msg = await api.list_files(req.parent_id or "0")
            for i in items:
                is_folder = bool(i.get('cid')) and not i.get('fid')
                result.append({
                    "id": i.get('cid') if is_folder else i.get('fid'),
                    "name": i.get('n') or i.get('fn') or i.get('name'),
                    "is_folder": is_folder,
                    "size": i.get('s', 0),
                    "updated_at": datetime.datetime.fromtimestamp(int(i.get('te') or i.get('tu') or 0)).strftime('%Y-%m-%d %H:%M:%S') if (i.get('te') or i.get('tu')) else (i.get('t') or "")
                })
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
    if req.drive_type == 'quark':
        api = QuarkDrive(config.get('cookie_quark', ''))
    elif req.drive_type == '115':
        api = Drive115(config.get('cookie_115', ''))
    else:
        api = AliyunDrive(config.get('token_aliyun', ''))
    try:
        if req.action == 'mkdir': success, msg = await api.make_dir(req.file_id, req.new_name)
        elif req.action == 'rename': success, msg = await api.rename(req.file_id, req.new_name)
        elif req.action == 'delete': success, msg = await api.delete(req.file_id)
        return {"code": 200 if success else 500, "msg": msg}
    except Exception as e: return {"code": 500, "msg": str(e)}

# ==================== 115 扫码登录接口：带有最强抗风控头信息 ====================
HEADERS_115 = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

def build_qrcode_data_url(text: str):
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"

@router.get("/api/115/qrcode")
async def get_115_qr():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get("https://qrcodeapi.115.com/api/1.0/web/1.0/token/", headers=HEADERS_115)
            res.raise_for_status()
            data = res.json()
            qr_data = data.get("data") or {}
            qr_text = qr_data.get("qrcode")
            uid = qr_data.get("uid")
            if not qr_text and uid:
                qr_text = f"https://115.com/scan/dg-{uid}"
            if qr_text:
                qr_data["qrcode"] = qr_text
                qr_data["qrcode_image"] = build_qrcode_data_url(qr_text)
                data["data"] = qr_data
            return data
    except Exception as e:
        add_log("ERROR", f"获取 115 二维码失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"网络请求或 115 接口拦截: {str(e)}")

@router.post("/api/115/status")
async def get_115_st(p: QrcodeStatusModel):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"https://qrcodeapi.115.com/get/status/?uid={p.uid}&time={p.time}&sign={p.sign}", headers=HEADERS_115)
            return res.json()
    except httpx.TimeoutException:
        return {"state": 1, "code": 0, "message": "等待扫码中", "data": {"status": 0}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/115/login")
async def log_115(p: QrcodeLoginModel):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post("https://passportapi.115.com/app/1.0/web/1.0/login/qrcode/", data={"app": "web", "account": p.uid}, headers=HEADERS_115)
            res_json = res.json()
            if res_json.get('state'):
                ck = "; ".join(f"{k}={v}" for k, v in res_json['data']['cookie'].items())
                conn = get_db()
                conn.execute("REPLACE INTO system_configs (config_key, config_value) VALUES ('cookie_115', ?)", (ck,))
                conn.commit()
                conn.close()
                return {"message": "成功"}
            raise HTTPException(status_code=400, detail="登录失败或二维码已过期")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

ALIYUN_QRCODE_HOST = "https://passport.aliyundrive.com"
ALIYUN_QRCODE_PARAMS = {"appName": "aliyun_drive", "fromSite": "52", "_bx-v": "2.0.31"}
ALIYUN_QRCODE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.alipan.com",
    "Referer": "https://www.alipan.com/",
}

def _aliyun_qr_data(data):
    if isinstance(data, dict):
        content = data.get("content") or {}
        if isinstance(content, dict):
            inner = content.get("data") or {}
            if isinstance(inner, dict):
                return inner
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner
    return {}

def _decode_aliyun_biz_ext(biz_ext: str):
    if not biz_ext:
        return {}
    candidates = []
    raw = str(biz_ext).strip()
    candidates.append(raw)
    candidates.append(urllib.parse.unquote(raw))
    candidates.append(raw.replace(" ", "+"))

    for candidate in candidates:
        if not candidate:
            continue
        parsed = _try_parse_json(candidate)
        if parsed:
            return parsed

        padding = "=" * (-len(candidate) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded_bytes = decoder((candidate + padding).encode("utf-8"))
            except Exception:
                continue
            for encoding in ("utf-8", "utf-8-sig", "gbk"):
                try:
                    decoded = decoded_bytes.decode(encoding)
                except Exception:
                    continue
                parsed = _try_parse_json(decoded)
                if parsed:
                    return parsed
    return {}

def _try_parse_json(value):
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    if isinstance(parsed, str):
        return _try_parse_json(parsed)
    return parsed if isinstance(parsed, (dict, list)) else {}

def _find_aliyun_refresh_token(value, depth=0):
    if depth > 8:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if normalized_key == "refreshtoken" and isinstance(item, str) and len(item) > 20:
                return item
        for item in value.values():
            found = _find_aliyun_refresh_token(item, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_aliyun_refresh_token(item, depth + 1)
            if found:
                return found
    elif isinstance(value, str):
        text = value.strip()
        parsed = _try_parse_json(value)
        if parsed:
            return _find_aliyun_refresh_token(parsed, depth + 1)
        if len(text) > 20:
            decoded = _decode_aliyun_biz_ext(text)
            if decoded:
                found = _find_aliyun_refresh_token(decoded, depth + 1)
                if found:
                    return found

            query = urllib.parse.urlparse(text).query or text
            query_data = urllib.parse.parse_qs(query)
            if query_data:
                flat = {k: v[0] if len(v) == 1 else v for k, v in query_data.items()}
                found = _find_aliyun_refresh_token(flat, depth + 1)
                if found:
                    return found
    return None

def _describe_aliyun_token_shape(value):
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, dict):
                parts.append(f"{key}({','.join(map(str, item.keys()))})")
            elif isinstance(item, list):
                parts.append(f"{key}[{len(item)}]")
            elif isinstance(item, str):
                parts.append(f"{key}:str")
            else:
                parts.append(f"{key}:{type(item).__name__}")
        return "; ".join(parts)[:500]
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__

def _save_aliyun_refresh_token(refresh_token: str):
    conn = get_db()
    conn.execute("REPLACE INTO system_configs (config_key, config_value) VALUES ('token_aliyun', ?)", (refresh_token,))
    conn.commit()
    conn.close()

@router.get("/api/aliyun/qrcode")
async def get_aliyun_qr():
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"{ALIYUN_QRCODE_HOST}/newlogin/qrcode/generate.do",
                params=ALIYUN_QRCODE_PARAMS,
                headers=ALIYUN_QRCODE_HEADERS,
            )
            res.raise_for_status()
            data = res.json()
            qr_data = _aliyun_qr_data(data)
            qr_content = qr_data.get("codeContent")
            t = str(qr_data.get("t") or "")
            ck = qr_data.get("ck")
            if not qr_content or not t or not ck:
                raise HTTPException(status_code=502, detail="阿里云盘移动端二维码接口返回格式异常")
            return {
                "code": 200,
                "data": {
                    "sid": t,
                    "t": t,
                    "ck": ck,
                    "qrcode": qr_content,
                    "qrcode_image": build_qrcode_data_url(qr_content),
                    "status": "WaitLogin",
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        add_log("ERROR", f"获取阿里云盘二维码失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取阿里云盘二维码失败: {str(e)}")

@router.post("/api/aliyun/status")
async def get_aliyun_status(p: AliyunQrcodeStatusModel):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            t = str(p.t or p.sid)
            form = {
                "t": t,
                "ck": p.ck or "",
                "appName": "aliyun_drive",
                "appEntrance": "web",
                "isMobile": "false",
                "lang": "zh_CN",
                "returnUrl": "",
                "fromSite": "52",
                "bizParams": "",
                "navlanguage": "zh-CN",
                "navPlatform": "Win32",
            }
            res = await client.post(
                f"{ALIYUN_QRCODE_HOST}/newlogin/qrcode/query.do",
                params=ALIYUN_QRCODE_PARAMS,
                data=form,
                headers=ALIYUN_QRCODE_HEADERS,
            )
            res.raise_for_status()
            raw = res.json()
            qr_data = _aliyun_qr_data(raw)
            qr_status = qr_data.get("qrCodeStatus") or qr_data.get("status")
            status_map = {
                "NEW": "WaitLogin",
                "SCANED": "ScanSuccess",
                "CONFIRMED": "LoginSuccess",
                "EXPIRED": "QRCodeExpired",
                "CANCELED": "QRCodeExpired",
            }
            status = status_map.get(qr_status, qr_status or "WaitLogin")
            result = {"status": status, "rawStatus": qr_status}
            if status == "LoginSuccess":
                ext = _decode_aliyun_biz_ext(qr_data.get("bizExt") or "")
                refresh_token = _find_aliyun_refresh_token(qr_data) or _find_aliyun_refresh_token(ext)
                if not refresh_token:
                    result["status"] = "TokenMissing"
                    result["message"] = "扫码已确认，但未解析到移动端 Refresh Token"
                    add_log("WARNING", f"阿里云盘移动端扫码已确认，但未解析到 Refresh Token，字段结构: {_describe_aliyun_token_shape(ext or qr_data)}")
                else:
                    _save_aliyun_refresh_token(refresh_token)
                    add_log("SUCCESS", "阿里云盘移动端扫码成功，Refresh Token 已自动写入配置。")
                    result["saved"] = True
                    result["message"] = "阿里云盘移动端 Refresh Token 已写入配置"
            return {"code": 200, "data": result}
    except httpx.TimeoutException:
        return {"code": 200, "data": {"status": "WaitLogin", "message": "等待扫码中"}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/aliyun/login")
async def log_aliyun(p: AliyunQrcodeLoginModel):
    raise HTTPException(status_code=410, detail="已改为移动端扫码流程，确认扫码后会自动写入 Refresh Token")

@router.get("/api/logs")
def fetch_logs(): return get_logs(100)

@router.post("/api/tasks/trigger")
async def trigger_task():
    from scheduler import auto_subscription_task
    import asyncio
    asyncio.create_task(auto_subscription_task())
    return {"message": "启动成功"}
