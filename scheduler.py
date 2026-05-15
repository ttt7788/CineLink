import httpx
import asyncio
import datetime
import random
import re
from database import get_db, get_sys_config
from config_guard import require_drive_ready
from drive_api import Drive115
from logger import add_log
from series_bindings import bind_series_after_transfer, ensure_series_target_folder, refresh_series_bindings

QUALITY_MAP = {"4k": 100, "2160p": 100, "uhd": 100, "1080p": 80, "fhd": 80, "bdrip": 75, "720p": 60, "remux": 95}

VALID_VIDEO_EXTS = (
    '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.ts', '.m2ts',
    '.rmvb', '.iso', '.vob', '.webm', '.srt', '.ass', '.sub', '.nfo'
)

TMDB_TRENDING_PAGES = 10
TMDB_BASE_PAGES = 500
TMDB_BATCH_SIZE = 50
TMDB_CONCURRENCY = 20
TMDB_BASE_MIN_COUNT = 10000
_tmdb_sync_locks = {
    "trending": asyncio.Lock(),
    "movie": asyncio.Lock(),
    "tv": asyncio.Lock(),
}
_auto_subscription_running = False

def get_quality_score(text: str) -> int:
    text = text.lower()
    score = 50
    for key, weight in QUALITY_MAP.items():
        if key in text: score = max(score, weight)
    return score

def _media_tuple(item, today_str, is_trending=False):
    title = item.get('title') or item.get('name')
    poster = item.get('poster_path')
    if not title or not poster or not item.get('id'):
        return None
    return (
        item['id'],
        item.get('media_type', 'movie'),
        title,
        item.get('overview', ''),
        poster,
        today_str,
        1 if is_trending else 0,
        today_str if is_trending else None,
        float(item.get('popularity') or 0),
        float(item.get('vote_average') or 0),
    )

def _upsert_media_items(items, today_str, is_trending=False):
    rows = []
    seen_ids = set()
    for item in items:
        row = _media_tuple(item, today_str, is_trending)
        if not row or row[0] in seen_ids:
            continue
        seen_ids.add(row[0])
        rows.append(row)

    if not rows:
        return []

    conn = get_db()
    cursor = conn.cursor()
    cursor.executemany('''
        INSERT INTO media_items
            (tmdb_id, media_type, title, overview, poster_path, add_date,
             is_trending, trend_date, popularity, vote_average)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id) DO UPDATE SET
            media_type=excluded.media_type,
            title=excluded.title,
            overview=excluded.overview,
            poster_path=excluded.poster_path,
            add_date=excluded.add_date,
            is_trending=CASE
                WHEN excluded.is_trending = 1 THEN 1
                ELSE media_items.is_trending
            END,
            trend_date=CASE
                WHEN excluded.is_trending = 1 THEN excluded.trend_date
                ELSE media_items.trend_date
            END,
            popularity=excluded.popularity,
            vote_average=excluded.vote_average
    ''', rows)
    conn.commit()
    conn.close()
    return rows

def _set_config_values(values):
    conn = get_db()
    cursor = conn.cursor()
    cursor.executemany(
        "REPLACE INTO system_configs (config_key, config_value) VALUES (?, ?)",
        list(values.items())
    )
    conn.commit()
    conn.close()

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

async def push_to_115(cookie: str, share_url: str, passcode: str = "", save_dir: str = "0"):
    if not cookie:
        return False, "未配置 115 Cookie"
    if not (save_dir or "").strip():
        return False, "未配置 115 默认保存目录 ID"
    try:
        return await Drive115(cookie).save_share(share_url, passcode, save_dir)
    except Exception as e:
        return False, f"115 API 异常: {str(e)}"

# ==================== 夸克网盘模块 ====================
async def push_to_quark(cookie: str, share_url: str, passcode: str = "", save_dir: str = "0"):
    if not cookie: return False, "未配置夸克Cookie"
    if not (save_dir or "").strip(): return False, "未配置夸克默认保存目录 ID"
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
    if not (save_dir or "").strip(): return False, "未配置阿里云盘默认保存目录 ID"
    match = re.search(r'/s/([a-zA-Z0-9]+)', share_url)
    if not match: return False, "无法解析阿里云盘分享链接"
    clean_save_dir = save_dir.split('-')[0].strip() if save_dir else "root"

    try:
        from aliyun_drive_mobile import AliyunDrive
        api = AliyunDrive(refresh_token)
        return await api.save_share(share_url, passcode, clean_save_dir)
    except Exception as e:
        return False, f"阿里云盘 API 异常: {str(e)}"


async def push_to_123(client_id: str, client_secret: str, share_url: str, passcode: str = "", save_dir: str = "0"):
    return False, "123云盘 Open API 暂未提供分享链接转存能力，当前已支持文件管理、播放代理与 STRM。"

# ==================== TMDB 数据采集 ====================
# mode 支持：trending / movie / tv / base / all
async def sync_tmdb_data(force=False, mode="all"):
    config = get_sys_config()
    api_key = config.get('api_key')

    if not api_key:
        add_log("WARNING", "【库同步】跳过：未配置 TMDB API Key。")
        return

    today_str = datetime.date.today().isoformat()
    base_url = config.get('api_domain', 'https://api.tmdb.org').rstrip('/')

    conn = get_db()
    counts = {
        "movie": conn.execute("SELECT COUNT(*) FROM media_items WHERE media_type='movie'").fetchone()[0],
        "tv": conn.execute("SELECT COUNT(*) FROM media_items WHERE media_type='tv'").fetchone()[0],
    }
    conn.close()

    if not force and mode == "all" and config.get('last_sync_date') == today_str:
        if counts["movie"] >= TMDB_BASE_MIN_COUNT and counts["tv"] >= TMDB_BASE_MIN_COUNT:
            return

    async def fetch_json(client, path, params):
        try:
            res = await client.get(f"{base_url}{path}", params=params)
            if res.status_code == 200:
                return res.json().get('results', [])
            add_log("WARNING", f"【库同步】TMDB 请求失败: {path} HTTP {res.status_code}")
        except Exception as e:
            add_log("WARNING", f"【库同步】TMDB 请求异常: {path} -> {str(e)}")
        return []

    async def sync_trending(client):
        lock = _tmdb_sync_locks["trending"]
        if lock.locked():
            add_log("INFO", "【今日热门】已有采集任务运行中，本次跳过重复触发。")
            return []

        async with lock:
            fresh_config = get_sys_config()
            if not force and fresh_config.get('last_trending_sync_date') == today_str:
                add_log("INFO", "【今日热门】今日已采集完成，跳过重复请求。")
                return []

            add_log("INFO", f"【今日热门】开始采集电影/剧集日趋势，各 {TMDB_TRENDING_PAGES} 页...")
            tasks = []
            for m_type in ['movie', 'tv']:
                for page_no in range(1, TMDB_TRENDING_PAGES + 1):
                    tasks.append(fetch_json(
                        client,
                        f"/3/trending/{m_type}/day",
                        {"api_key": api_key, "language": "zh-CN", "page": page_no}
                    ))

            results = await asyncio.gather(*tasks)
            items = []
            for idx, result in enumerate(results):
                m_type = 'movie' if idx < TMDB_TRENDING_PAGES else 'tv'
                for item in result:
                    item['media_type'] = m_type
                    items.append(item)

            rows = _upsert_media_items(items, today_str, is_trending=True)
            _set_config_values({"last_trending_sync_date": today_str})
            add_log("SUCCESS", f"【今日热门】采集完成，新增/更新 {len(rows)} 条热门影视。")
            return rows

    async def sync_media_type(client, m_type):
        lock = _tmdb_sync_locks[m_type]
        if lock.locked():
            add_log("INFO", f"【{m_type}库】已有补全任务运行中，本次跳过重复触发。")
            return 0

        async with lock:
            fresh_config = get_sys_config()
            if not force and fresh_config.get(f'last_{m_type}_sync_date') == today_str and counts[m_type] >= TMDB_BASE_MIN_COUNT:
                add_log("INFO", f"【{m_type}库】今日已补全且数量充足，跳过重复采集。")
                return 0

            add_log("INFO", f"【{m_type}库】开始补全热门基础库，共 {TMDB_BASE_PAGES} 页，并发 {TMDB_CONCURRENCY}。")
            total_rows = 0
            sem = asyncio.Semaphore(TMDB_CONCURRENCY)

            async def fetch_page(page_no):
                async with sem:
                    result = await fetch_json(
                        client,
                        f"/3/{m_type}/popular",
                        {"api_key": api_key, "language": "zh-CN", "page": page_no}
                    )
                    for item in result:
                        item['media_type'] = m_type
                    return result

            for start in range(1, TMDB_BASE_PAGES + 1, TMDB_BATCH_SIZE):
                end = min(start + TMDB_BATCH_SIZE - 1, TMDB_BASE_PAGES)
                batch_results = await asyncio.gather(*[fetch_page(page_no) for page_no in range(start, end + 1)])
                batch_items = [item for result in batch_results for item in result]
                rows = _upsert_media_items(batch_items, today_str, is_trending=False)
                total_rows += len(rows)
                add_log("INFO", f"【{m_type}库】已处理 {end}/{TMDB_BASE_PAGES} 页，累计写入 {total_rows} 条。")

            _set_config_values({f"last_{m_type}_sync_date": today_str})
            add_log("SUCCESS", f"【{m_type}库】补全完成，累计新增/更新 {total_rows} 条。")
            return total_rows

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            trending_rows = []
            if mode in ["all", "trending"]:
                trending_rows = await sync_trending(client)

            target_types = []
            if mode in ["all", "base"]:
                if force or counts["movie"] < TMDB_BASE_MIN_COUNT:
                    target_types.append("movie")
                if force or counts["tv"] < TMDB_BASE_MIN_COUNT:
                    target_types.append("tv")
            elif mode in ["movie", "tv"]:
                target_types.append(mode)

            for target_type in target_types:
                await sync_media_type(client, target_type)

            values = {}
            if mode == "all":
                values["last_sync_date"] = today_str
            if target_types:
                values["last_base_sync_date"] = today_str
            if values:
                _set_config_values(values)

            if trending_rows and config.get('auto_subscribe_new') == '1':
                target_drive = config.get('auto_subscribe_drive', '115')
                conn = get_db()
                cursor = conn.cursor()
                sub_data = [(row[0], target_drive) for row in trending_rows]
                cursor.executemany(
                    "INSERT OR IGNORE INTO subscriptions (tmdb_id, status, drive_type) VALUES (?, 'pending', ?)",
                    sub_data
                )
                conn.commit()
                conn.close()
                add_log("INFO", f"【自动订阅】已将 {len(sub_data)} 部今日热门加入待搜刮队列，目标：{target_drive}。")

            add_log("INFO", f"【库同步】执行完毕 (模式: {mode})。")
    except Exception as e:
        add_log("ERROR", f"【库同步】严重异常: {str(e)}")

# ==================== 调度主循环 ====================
async def auto_subscription_task():
    global _auto_subscription_running
    if _auto_subscription_running:
        add_log("WARNING", "【定时任务】已有搜刮任务正在运行，本次触发已跳过，避免重复转存。")
        return
    _auto_subscription_running = True
    try:
        await _auto_subscription_task_impl()
    finally:
        _auto_subscription_running = False


async def _auto_subscription_task_impl():
    config = get_sys_config()
    api_key = config.get('api_key', '').strip()
    auto_subscribe = str(config.get('auto_subscribe_new', '0'))
    
    if auto_subscribe == '1' and not api_key:
        add_log("WARNING", "⏰ 定时任务警告：已开启自动订阅开关，但未配置 TMDB API Key，TMDB采集将被跳过。")
        
    await sync_tmdb_data(force=False, mode="all")
    
    add_log("INFO", "【定时任务】开始处理待搜刮的订阅任务...")
    pansou_domain = config.get('pansou_domain', "http://192.168.68.200:8080")
    cookie_115 = config.get('cookie_115')
    cookie_quark = config.get('cookie_quark')
    token_aliyun = config.get('token_aliyun')
    drive123_client_id = config.get('drive123_client_id')
    drive123_client_secret = config.get('drive123_client_secret')
    
    drive115_save_dir = config.get('drive115_save_dir', '0')
    quark_save_dir = config.get('quark_save_dir', '0')
    aliyun_save_dir = config.get('aliyun_save_dir', 'root')
    drive123_save_dir = config.get('drive123_save_dir', '0')

    conn = get_db()
    subs = conn.execute(
        """
        SELECT s.tmdb_id, s.drive_type, m.title, m.media_type
        FROM subscriptions s
        JOIN media_items m ON s.tmdb_id = m.tmdb_id
        WHERE s.status = 'pending'
        """
    ).fetchall()
    conn.close()
    if not subs:
        await refresh_series_bindings()
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        for sub in subs:
            tmdb_id, title, media_type, drive_type = sub['tmdb_id'], sub['title'], sub['media_type'], sub['drive_type']
            add_log("INFO", f"【搜刮】执行中: 《{title}》 目标网盘: {drive_type}")
            try:
                ready, ready_msg = require_drive_ready(drive_type, config)
                if not ready:
                    add_log("WARNING", f"【搜刮】《{title}》跳过：{ready_msg}")
                    await asyncio.sleep(1)
                    continue
                ps_res = await client.post(f"{pansou_domain.rstrip('/')}/api/search", json={"kw": title})
                data = ps_res.json().get("data", {}).get("merged_by_type", {})
                
                if drive_type == 'quark': priorities = ["quark"]
                elif drive_type == 'aliyun': priorities = ["aliyun"]
                elif drive_type == '115': priorities = ["115"]
                elif drive_type == '123': priorities = ["123"]
                else: priorities = [drive_type]
                    
                candidates = []
                for p_type in priorities:
                    for item in data.get(p_type) or []:
                        link = item.get("url")
                        if not link:
                            continue
                        candidates.append({
                            "url": link,
                            "hit_type": p_type,
                            "note": item.get("note", ""),
                            "pwd": item.get("password", "") or item.get("pwd", ""),
                        })
                
                if candidates:
                    add_log("INFO", f"【搜刮】《{title}》找到 {len(candidates)} 条 {drive_type} 候选资源，开始逐条尝试。")
                    success, msg = False, ""
                    best_link, hit_type, best_pwd = "", "", ""
                    target_save_dir = ""
                    target_cloud_path = ""
                    if media_type == "tv" and drive_type in {"quark", "aliyun", "115", "123"}:
                        if drive_type == "quark":
                            base_dir = quark_save_dir
                        elif drive_type == "aliyun":
                            base_dir = aliyun_save_dir
                        elif drive_type == "123":
                            base_dir = drive123_save_dir
                        else:
                            base_dir = drive115_save_dir
                        target_save_dir, target_cloud_path, folder_ok, folder_msg = await ensure_series_target_folder(drive_type, title, base_dir.split("-")[0].strip() if base_dir else None)
                        if folder_ok:
                            add_log("INFO", f"【剧集绑定】《{title}》使用独立剧集目录: {target_cloud_path}")
                        else:
                            add_log("WARNING", f"【剧集绑定】《{title}》无法创建独立剧集目录，将只转存不绑定: {folder_msg}")
                            target_save_dir = ""
                            target_cloud_path = ""

                    for index, candidate in enumerate(candidates, start=1):
                        link = candidate["url"]
                        hit_type = candidate["hit_type"]
                        best_pwd = candidate["pwd"]
                        note = candidate["note"]
                        add_log("INFO", f"【推送】《{title}》尝试第 {index}/{len(candidates)} 条 {hit_type} 资源(密码:{best_pwd or '无'})...")

                        if drive_type == 'quark':
                            save_target = target_save_dir or quark_save_dir
                            add_log("INFO", f"【推送】转存至夸克目录[{save_target.split('-')[0].strip()}]...")
                            success, msg = await push_to_quark(cookie_quark, link, best_pwd, save_target)
                        elif drive_type == 'aliyun':
                            save_target = target_save_dir or aliyun_save_dir
                            add_log("INFO", f"【推送】转存至阿里云盘目录[{save_target.split('-')[0].strip()}]...")
                            success, msg = await push_to_aliyun(token_aliyun, link, best_pwd, save_target)
                        elif drive_type == '115':
                            ex_file, ex_score = await check_115_existing_quality(cookie_115, title)
                            new_score = get_quality_score(note or title)
                            if ex_file and ex_score >= new_score:
                                add_log("INFO", f"【跳过】网盘已有极佳版本: {ex_file}")
                                conn = get_db(); conn.execute("UPDATE subscriptions SET status='success' WHERE tmdb_id=?", (tmdb_id,)); conn.commit(); conn.close()
                                success = True
                                msg = "网盘已有极佳版本"
                                best_link = link
                                break
                            save_target = target_save_dir or drive115_save_dir
                            add_log("INFO", f"【推送】转存至 115 目录[{save_target.split('-')[0].strip()}]...")
                            success, msg = await push_to_115(cookie_115, link, best_pwd, save_target)
                        elif drive_type == '123':
                            save_target = target_save_dir or drive123_save_dir
                            add_log("INFO", f"【推送】转存至 123云盘目录[{save_target.split('-')[0].strip()}]...")
                            success, msg = await push_to_123(drive123_client_id, drive123_client_secret, link, best_pwd, save_target)

                        if success:
                            best_link = link
                            add_log("SUCCESS", f"【成功】《{title}》第 {index}/{len(candidates)} 条资源已入库 ({hit_type})")
                            break
                        add_log("WARNING", f"【重试】《{title}》第 {index}/{len(candidates)} 条资源失败: {msg}")

                    if success:
                        conn = get_db(); conn.execute("UPDATE subscriptions SET status='success' WHERE tmdb_id=?", (tmdb_id,)); conn.commit(); conn.close()
                        parent_id = "0"
                        if drive_type == "quark":
                            parent_id = target_save_dir or (quark_save_dir.split("-")[0].strip() if quark_save_dir else "0")
                        elif drive_type == "aliyun":
                            parent_id = target_save_dir or (aliyun_save_dir.split("-")[0].strip() if aliyun_save_dir else "root")
                        elif drive_type == "115":
                            parent_id = target_save_dir or (drive115_save_dir.split("-")[0].strip() if drive115_save_dir else "0")
                        elif drive_type == "123":
                            parent_id = target_save_dir or (drive123_save_dir.split("-")[0].strip() if drive123_save_dir else "0")
                        await bind_series_after_transfer(
                            tmdb_id,
                            media_type,
                            title,
                            drive_type,
                            best_link,
                            best_pwd,
                            parent_id,
                            target_cloud_path or None,
                        )
                    else:
                        add_log("ERROR", f"【失败】《{title}》{len(candidates)} 条候选资源全部尝试失败，最后错误: {msg}")
                else:
                    add_log("WARN", f"【搜刮】全网未找到符合 {drive_type} 的《{title}》资源。")
            except Exception as e: 
                add_log("ERROR", f"【异常】: {str(e)}")
            await asyncio.sleep(2)
    await refresh_series_bindings()
