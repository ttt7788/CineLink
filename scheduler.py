import httpx
import asyncio
import datetime
import random
import re
from database import get_db, get_sys_config
from logger import add_log

QUALITY_MAP = {"4k": 100, "2160p": 100, "uhd": 100, "1080p": 80, "fhd": 80, "bdrip": 75, "720p": 60, "remux": 95}

VALID_VIDEO_EXTS = (
    '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.ts', '.m2ts', 
    '.rmvb', '.iso', '.vob', '.webm', '.srt', '.ass', '.sub', '.nfo'
)

def get_quality_score(text: str) -> int:
    text = text.lower()
    score = 50 
    for key, weight in QUALITY_MAP.items():
        if key in text: score = max(score, weight)
    return score

# ==================== 115网盘模块 ====================
async def check_115_existing_quality(cookie: str, title: str):
    if not cookie: return None, 0
    await asyncio.sleep(random.uniform(0.2, 0.5))
    search_url = f"https://webapi.115.com/files/search?search_value={title}"
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.get(search_url, headers=headers)
            res_data = res.json()
            if res_data.get("state") and res_data.get("data"):
                file_list = res_data["data"]
                if not file_list: return None, 0
                best_match, max_score = None, 0
                for f in file_list:
                    name = f.get("n", "")
                    score = get_quality_score(name)
                    if score > max_score: max_score = score; best_match = name
                return best_match, max_score
        except Exception: pass
    return None, 0

async def push_to_cms(cms_url: str, cms_token: str, link: str):
    api_endpoint = f"{cms_url.rstrip('/')}/api/cloud/add_share_down_by_token"
    payload = {"url": link, "token": cms_token}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            res = await client.post(api_endpoint, json=payload)
            res_json = res.json()
            if res_json.get("code") == 200: return True, res_json.get("msg")
            return False, res_json.get("msg", "未知错误")
        except Exception as e: return False, f"连接 CMS 失败: {str(e)}"

# ==================== 夸克网盘模块 ====================
async def push_to_quark(cookie: str, share_url: str, passcode: str = "", save_dir: str = "0"):
    if not cookie: return False, "未配置夸克Cookie"
    match = re.search(r'/s/([a-zA-Z0-9]+)', share_url)
    if not match: return False, "无法解析夸克分享链接"
    pwd_id = match.group(1)
    
    clean_save_dir = save_dir.split('-')[0].strip() if save_dir else "0"
    
    headers = {
        "cookie": cookie, 
        "content-type": "application/json",
        "referer": f"https://pan.quark.cn/s/{pwd_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            token_url = "https://pan.quark.cn/1/clouddrive/share/sharepage/token"
            token_payload = {"pwd_id": pwd_id, "passcode": passcode}
            info_res = await client.post(token_url, json=token_payload, headers=headers)
            info_data = info_res.json()
            if info_data.get("code") != 0: return False, f"夸克解析失败: {info_data.get('message', '未知错误')}"
            stoken = info_data.get("data", {}).get("stoken")
            if not stoken: return False, "未能提取 stoken"

            detail_url = "https://pan.quark.cn/1/clouddrive/share/sharepage/detail"
            detail_params = {"pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0"}
            detail_res = await client.get(detail_url, params=detail_params, headers=headers)
            detail_data = detail_res.json()
            if detail_data.get("code") != 0: return False, f"获取文件列表失败: {detail_data.get('message', '未知错误')}"
            file_list = detail_data.get("data", {}).get("list", [])
            if not file_list: return False, "分享内无文件或为空目录"
            
            filtered_list = []
            for f in file_list:
                fname = f.get("file_name", "").lower()
                is_folder = f.get("file_type") == 0 
                if is_folder or fname.endswith(VALID_VIDEO_EXTS):
                    filtered_list.append(f)
            
            if not filtered_list: return False, "分享链接内未找到视频格式文件 (可能为压缩包或无关引流文件)"
            
            fid_list = [f["fid"] for f in filtered_list]
            fid_token_list = [f["share_fid_token"] for f in filtered_list]
            
            save_url = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/save"
            params = {"pr": "ucpro", "fr": "pc", "uc_param_str": "", "app": "clouddrive", "__dt": int(random.uniform(1, 5) * 60 * 1000), "__t": int(datetime.datetime.now().timestamp() * 1000)}
            payload = {"fid_list": fid_list, "fid_token_list": fid_token_list, "to_pdir_fid": clean_save_dir, "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "scene": "link"}
            res = await client.post(save_url, params=params, json=payload, headers=headers)
            res_json = res.json()
            if res_json.get("code") == 0: return True, "夸克文件转存成功"
            else: return False, res_json.get("message", "转存被拒绝")
        except Exception as e: return False, f"夸克 API 异常: {str(e)}"

# ==================== 阿里云盘模块 ====================
async def push_to_aliyun(refresh_token: str, share_url: str, passcode: str = "", save_dir: str = "root"):
    if not refresh_token: return False, "未配置阿里云盘 Refresh Token"
    match = re.search(r'/s/([a-zA-Z0-9]+)', share_url)
    if not match: return False, "无法解析阿里云盘分享链接"
    share_id = match.group(1)
    clean_save_dir = save_dir.split('-')[0].strip() if save_dir else "root"

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            refresh_res = await client.post("https://api.aliyundrive.com/token/refresh", json={"refresh_token": refresh_token})
            refresh_data = refresh_res.json()
            if "access_token" not in refresh_data: return False, "Token 刷新失败"
            access_token = refresh_data["access_token"]
            drive_id = refresh_data.get("default_drive_id")
            auth_header = {"Authorization": f"Bearer {access_token}"}
            
            st_res = await client.post("https://api.aliyundrive.com/v2/share_link/get_share_token", json={"share_id": share_id, "share_pwd": passcode})
            share_token = st_res.json().get("share_token")
            if not share_token: return False, "获取 Share Token 失败"

            info_res = await client.post(f"https://api.aliyundrive.com/adrive/v3/share_link/get_share_by_anonymous?share_id={share_id}", json={"share_id": share_id}, headers=auth_header)
            file_infos = info_res.json().get("file_infos", [])
            if not file_infos: return False, "分享链接内无文件"
            
            requests_list = []
            idx = 0
            for f in file_infos:
                fname = f.get("name", "").lower()
                is_folder = f.get("type") == 'folder'
                if is_folder or fname.endswith(VALID_VIDEO_EXTS):
                    requests_list.append({
                        "body": {"file_id": f["file_id"], "share_id": share_id, "auto_rename": True, "to_parent_file_id": clean_save_dir, "to_drive_id": drive_id},
                        "headers": {"Content-Type": "application/json"}, "id": str(idx), "method": "POST", "url": "/file/copy"
                    })
                    idx += 1
            
            if not requests_list: return False, "分享链接内未找到视频格式文件 (可能为压缩包或无关引流文件)"
                
            batch_res = await client.post("https://api.aliyundrive.com/adrive/v2/batch", json={"requests": requests_list, "resource": "file"}, headers={"Authorization": f"Bearer {access_token}", "x-share-token": share_token})
            if batch_res.status_code in [200, 202]: return True, "阿里云盘文件极速转存成功"
            else: return False, "阿里云盘转存被拒绝"
        except Exception as e: return False, f"阿里云盘 API 异常: {str(e)}"

# ==================== TMDB 数据采集 ====================
# 增加 mode 参数，精确区分“只采前10页热门”和“首次补全500页基础库”
async def sync_tmdb_data(force=False, mode="all"):
    config = get_sys_config()
    api_key = config.get('api_key')
    
    if not api_key: 
        add_log("WARNING", "【库同步】跳过：未配置 TMDB API Key。")
        return

    today_str = datetime.date.today().isoformat()
    # 如果不是强制且今天已同步，并且是全量日常定时任务，则跳过
    if not force and config.get('last_sync_date') == today_str and mode == "all": 
        return 

    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
    conn.close()

    base_url = config.get('api_domain', 'https://api.tmdb.org').rstrip('/')
    items = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # 1. 【今日热门】模式：只采集前 10 页
            if mode in ["all", "trending"]:
                add_log("INFO", f"【库同步】开始极速抓取今日新增热门趋势 (前 10 页)...")
                
                async def fetch_trend(m_type, window, page):
                    try:
                        url = f"{base_url}/3/trending/{m_type}/{window}"
                        r = await client.get(url, params={"api_key": api_key, "language": "zh-CN", "page": page})
                        if r.status_code == 200:
                            res_data = r.json().get('results', [])
                            for m in res_data: m['media_type'] = m_type
                            return res_data
                    except Exception: pass
                    return []

                trend_tasks = []
                for w in ['day']:
                    for t in ['movie', 'tv']:
                        for p in range(1, 11): 
                            trend_tasks.append(fetch_trend(t, w, p))
                
                trend_results = await asyncio.gather(*trend_tasks)
                for res_arr in trend_results:
                    items.extend(res_arr)

            # 2. 【基础库】模式：首次部署库数据不足时，采集500页。一旦饱满永远不再执行。
            if count < 15000 and mode in ["all", "base"]:
                add_log("INFO", f"【库同步】历史库数据不足({count}条)，启动并发大补全(电影/剧集各500页)...")
                sem = asyncio.Semaphore(15) 
                async def fetch_page(m_type, page):
                    async with sem:
                        try:
                            res = await client.get(f"{base_url}/3/{m_type}/popular", params={"api_key": api_key, "language": "zh-CN", "page": page})
                            if res.status_code == 200:
                                res_items = res.json().get('results', [])
                                for r in res_items: r['media_type'] = m_type
                                return res_items
                        except Exception: pass
                        return []

                tasks_m = [fetch_page('movie', p) for p in range(1, 501)]
                for i in range(0, 500, 100):
                    res_list = await asyncio.gather(*tasks_m[i:i+100])
                    for r in res_list: items.extend(r)
                    add_log("INFO", f"【库同步】电影库已处理 {min(i+100, 500)} 页...")

                tasks_t = [fetch_page('tv', p) for p in range(1, 501)]
                for i in range(0, 500, 100):
                    res_list = await asyncio.gather(*tasks_t[i:i+100])
                    for r in res_list: items.extend(r)
                    add_log("INFO", f"【库同步】剧集库已处理 {min(i+100, 500)} 页...")

            unique_items = {item['id']: item for item in items if item.get('id')}.values()
            if not unique_items: return

            insert_data = []
            for item in unique_items:
                title = item.get('title') or item.get('name')
                poster = item.get('poster_path')
                if not title or not poster: continue
                insert_data.append((item['id'], item.get('media_type', 'movie'), title, item.get('overview', ''), poster, today_str))

            conn = get_db()
            cursor = conn.cursor()
            cursor.executemany('''INSERT OR REPLACE INTO media_items (tmdb_id, media_type, title, overview, poster_path, add_date) VALUES (?, ?, ?, ?, ?, ?)''', insert_data)
            
            # 只有全量同步时才刷新今日的同步状态标识
            if mode == "all":
                cursor.execute("REPLACE INTO system_configs (config_key, config_value) VALUES ('last_sync_date', ?)", (today_str,))
            
            # 【防止自动订阅爆炸】只有日常定时更新（库已满）且抓取了每日热点时，才将当天的数据丢入自动订阅。
            if count >= 15000 and config.get('auto_subscribe_new') == '1' and mode in ["all", "trending"]:
                target_drive = config.get('auto_subscribe_drive', '115')
                sub_data = [(item[0], target_drive) for item in insert_data]
                cursor.executemany("INSERT OR IGNORE INTO subscriptions (tmdb_id, status, drive_type) VALUES (?, 'pending', ?)", sub_data)
                add_log("INFO", f"【自动订阅】功能开启！已成功将 {len(sub_data)} 部趋势影视加入待搜刮队列，目标：{target_drive}。")

            conn.commit()
            conn.close()
            add_log("INFO", f"【库同步】执行完毕 (模式: {mode})，系统运转流畅！")
        except Exception as e:
            add_log("ERROR", f"【库同步】严重异常: {str(e)}")

# ==================== 调度主循环 ====================
async def auto_subscription_task():
    config = get_sys_config()
    api_key = config.get('api_key', '').strip()
    auto_subscribe = str(config.get('auto_subscribe_new', '0'))
    
    if auto_subscribe == '1' and not api_key:
        add_log("WARNING", "⏰ 定时任务警告：已开启自动订阅开关，但未配置 TMDB API Key，TMDB采集将被跳过。")
        
    await sync_tmdb_data(force=False, mode="all")
    
    add_log("INFO", "【定时任务】开始处理待搜刮的订阅任务...")
    pansou_domain = config.get('pansou_domain', "http://192.168.68.200:8080")
    cms_url = config.get('cms_api_url')
    cms_token = config.get('cms_api_token')
    
    cookie_115 = config.get('cookie_115')
    cookie_quark = config.get('cookie_quark')
    token_aliyun = config.get('token_aliyun')
    
    quark_save_dir = config.get('quark_save_dir', '0')
    aliyun_save_dir = config.get('aliyun_save_dir', 'root')

    conn = get_db()
    subs = conn.execute("SELECT s.tmdb_id, s.drive_type, m.title FROM subscriptions s JOIN media_items m ON s.tmdb_id = m.tmdb_id WHERE s.status = 'pending'").fetchall()
    conn.close()
    if not subs: return

    async with httpx.AsyncClient(timeout=30.0) as client:
        for sub in subs:
            tmdb_id, title, drive_type = sub['tmdb_id'], sub['title'], sub['drive_type']
            add_log("INFO", f"【搜刮】执行中: 《{title}》 目标网盘: {drive_type}")
            try:
                ps_res = await client.post(f"{pansou_domain.rstrip('/')}/api/search", json={"kw": title})
                data = ps_res.json().get("data", {}).get("merged_by_type", {})
                
                if drive_type == 'quark': priorities = ["quark"]
                elif drive_type == 'aliyun': priorities = ["aliyun"]
                else: priorities = ["115", "aliyun", "ed2k", "magnet"]
                    
                best_link, hit_type, new_note, best_pwd = None, None, "", ""
                for p_type in priorities:
                    if data.get(p_type) and len(data[p_type]) > 0:
                        item = data[p_type][0]
                        best_link = item["url"]
                        hit_type = p_type
                        new_note = item.get("note", "")
                        best_pwd = item.get("password", "") or item.get("pwd", "")
                        break
                
                if best_link:
                    success, msg = False, ""
                    
                    if drive_type == 'quark':
                        add_log("INFO", f"【推送】命中夸克资源(密码:{best_pwd or '无'})，转存至目录[{quark_save_dir.split('-')[0].strip()}]...")
                        success, msg = await push_to_quark(cookie_quark, best_link, best_pwd, quark_save_dir)
                    elif drive_type == 'aliyun':
                        add_log("INFO", f"【推送】命中阿里云盘资源(密码:{best_pwd or '无'})，转存至目录[{aliyun_save_dir.split('-')[0].strip()}]...")
                        success, msg = await push_to_aliyun(token_aliyun, best_link, best_pwd, aliyun_save_dir)
                    else:
                        if not cms_url or not cms_token:
                            add_log("WARN", "未配置 CMS，跳过 115 节点")
                            continue
                        ex_file, ex_score = await check_115_existing_quality(cookie_115, title)
                        new_score = get_quality_score(new_note or title)
                        if ex_file and ex_score >= new_score:
                            add_log("INFO", f"【跳过】网盘已有极佳版本: {ex_file}")
                            conn = get_db(); conn.execute("UPDATE subscriptions SET status='success' WHERE tmdb_id=?", (tmdb_id,)); conn.commit(); conn.close()
                            continue
                        success, msg = await push_to_cms(cms_url, cms_token, best_link)

                    if success:
                        add_log("SUCCESS", f"【成功】《{title}》已入库 ({hit_type})")
                        conn = get_db(); conn.execute("UPDATE subscriptions SET status='success' WHERE tmdb_id=?", (tmdb_id,)); conn.commit(); conn.close()
                    else:
                        add_log("ERROR", f"【失败】{msg}")
                else:
                    add_log("WARN", f"【搜刮】全网未找到符合 {drive_type} 的《{title}》资源。")
            except Exception as e: 
                add_log("ERROR", f"【异常】: {str(e)}")
            await asyncio.sleep(2)