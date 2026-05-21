import httpx
import datetime
import random
import re
import time
from urllib.parse import parse_qs, urlparse

from p115_runtime import ensure_p115_runtime_home

VALID_VIDEO_EXTS = (
    '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.ts', '.m2ts',
    '.rmvb', '.iso', '.vob', '.webm', '.srt', '.ass', '.sub', '.nfo'
)

def _safe_json(res):
    try: return res.json()
    except: return {"code": -999, "message": f"HTTP {res.status_code}"}

# ==========================================
# 夸克网盘 API 核心引擎 (纯享转存版)
# ==========================================
class QuarkDrive:
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.headers = {"cookie": self.cookie, "content-type": "application/json", "user-agent": "Mozilla/5.0"}
        self.timeout = 20.0
        self.api_url = "https://drive.quark.cn/1/clouddrive"

    def _set_cookie_value(self, name: str, value: str):
        if not name or not value:
            return
        parts = []
        replaced = False
        for part in (self.cookie or "").split(";"):
            part = part.strip()
            if not part:
                continue
            if part.startswith(f"{name}="):
                parts.append(f"{name}={value}")
                replaced = True
            else:
                parts.append(part)
        if not replaced:
            parts.append(f"{name}={value}")
        self.cookie = "; ".join(parts)
        self.headers["cookie"] = self.cookie

    def _sync_response_cookies(self, res):
        changed = False
        for name in ("__puus", "__pus"):
            if name in res.cookies:
                self._set_cookie_value(name, res.cookies.get(name))
                changed = True
        if changed:
            try:
                from database import get_db

                conn = get_db()
                conn.execute(
                    "REPLACE INTO system_configs (config_key, config_value) VALUES ('cookie_quark', ?)",
                    (self.cookie,),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

    def _extract_pwd_id(self, share_url: str):
        match = re.search(r'/s/([a-zA-Z0-9]+)', share_url)
        return match.group(1) if match else None

    def _get_base_params(self):
        return {"pr": "ucpro", "fr": "pc", "uc_param_str": "", "app": "clouddrive", "__dt": int(random.uniform(1, 5) * 60 * 1000), "__t": int(datetime.datetime.now().timestamp() * 1000)}

    async def get_share_token(self, pwd_id: str, passcode: str = ""):
        req_headers = self.headers.copy()
        req_headers["referer"] = f"https://pan.quark.cn/s/{pwd_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post("https://pan.quark.cn/1/clouddrive/share/sharepage/token", json={"pwd_id": pwd_id, "passcode": passcode}, headers=req_headers)
            data = _safe_json(res)
            if data.get("code") != 0: return None, data.get("message", "解析失败")
            return data.get("data", {}).get("stoken"), "success"

    async def get_share_file_list(self, pwd_id: str, stoken: str, pdir_fid: str = "0"):
        req_headers = self.headers.copy()
        req_headers["referer"] = f"https://pan.quark.cn/s/{pwd_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"https://pan.quark.cn/1/clouddrive/share/sharepage/detail?pwd_id={pwd_id}&stoken={stoken}&pdir_fid={pdir_fid}", headers=req_headers)
            data = _safe_json(res)
            if data.get("code") != 0: return None, data.get("message", "获取失败")
            return data.get("data", {}).get("list", []), "success"

    async def save_share(self, share_url: str, passcode: str = "", save_dir: str = "0"):
        if not self.cookie: return False, "未配置夸克Cookie"
        pwd_id = self._extract_pwd_id(share_url)
        if not pwd_id: return False, "无法解析"
        stoken, msg = await self.get_share_token(pwd_id, passcode)
        if not stoken: return False, msg
        file_list, msg = await self.get_share_file_list(pwd_id, stoken, "0")
        if not file_list: return False, msg
        
        fid_list = [f["fid"] for f in file_list]
        fid_token_list = [f["share_fid_token"] for f in file_list]
        req_headers = self.headers.copy()
        req_headers["referer"] = f"https://pan.quark.cn/s/{pwd_id}"
        payload = {
            "fid_list": fid_list, "fid_token_list": fid_token_list, 
            "to_pdir_fid": save_dir.split('-')[0].strip(), 
            "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": "0", "scene": "link"
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                res = await client.post("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/save", params=self._get_base_params(), json=payload, headers=req_headers)
                if _safe_json(res).get("code") == 0: return True, "转存成功"
                return False, _safe_json(res).get("message", "转存被拒绝")
            except Exception as e: return False, str(e)

    async def list_files(self, dir_fid: str = "0"):
        items = []
        page = 1
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while True:
                params = self._get_base_params()
                params.update({
                    "pdir_fid": dir_fid,
                    "_page": str(page),
                    "_size": "100",
                    "_fetch_total": "1",
                    "_fetch_sub_dirs": "1",
                    "_sort": "file_type:asc,updated_at:desc",
                })
                res = await client.get(f"{self.api_url}/file/sort", params=params, headers=self.headers)
                self._sync_response_cookies(res)
                data = _safe_json(res)
                if data.get("code") != 0:
                    return [], data.get("message", "获取失败")
                page_items = data.get("data", {}).get("list", [])
                items.extend(page_items)
                metadata = data.get("metadata") or {}
                try:
                    total = int(metadata.get("_total") or len(items))
                    count = int(metadata.get("_count") or len(page_items))
                    size = int(metadata.get("_size") or 100)
                except (TypeError, ValueError):
                    total, count, size = len(items), len(page_items), 100
                if not page_items or len(items) >= total or count < size:
                    break
                page += 1
        return items, "success"

    async def get_download_url(self, file_fid: str):
        payload = {"fids": [file_fid]}
        params = {"pr": "ucpro", "fr": "pc", "sys": "win32", "ve": "2.5.56", "ut": "", "guid": ""}
        headers = self.headers.copy()
        headers.update({"origin": "https://pan.quark.cn", "referer": "https://pan.quark.cn/"})
        user_agents = [
            headers.get("user-agent", "Mozilla/5.0"),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 "
            "Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch",
        ]
        last_msg = "获取夸克下载地址失败"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for user_agent in user_agents:
                headers["user-agent"] = user_agent
                res = await client.post(
                    f"{self.api_url}/file/download",
                    params=params,
                    json=payload,
                    headers=headers,
                )
                self._sync_response_cookies(res)
                data = _safe_json(res)
                if data.get("code") in (23018, "23018"):
                    last_msg = data.get("message", last_msg)
                    continue
                if data.get("code") not in (0, None) or data.get("status") not in (200, None):
                    last_msg = data.get("message", last_msg)
                    continue
                items = data.get("data") or []
                if not items:
                    last_msg = "夸克未返回下载地址"
                    continue
                url = items[0].get("download_url") or items[0].get("url")
                if not url:
                    last_msg = "夸克下载地址为空"
                    continue
                return url, "success"
        return None, last_msg

    async def get_preview_url(self, file_fid: str):
        payload = {
            "fid": file_fid,
            "resolutions": "low,normal,high,super,2k,4k",
            "supports": "fmp4_av,m3u8,dolby_vision",
        }
        params = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        headers = self.headers.copy()
        headers.update({
            "origin": "https://pan.quark.cn",
            "referer": "https://pan.quark.cn/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 "
                "Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch"
            ),
        })
        last_msg = "夸克转码播放地址获取失败"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                f"{self.api_url}/file/v2/play/project",
                params=params,
                json=payload,
                headers=headers,
            )
            self._sync_response_cookies(res)
            data = _safe_json(res)
            if data.get("code") not in (0, None) or data.get("status") not in (200, None):
                return None, data.get("message") or last_msg
            video_list = data.get("data", {}).get("video_list") or []
            preference = ["4k", "2k", "super", "high", "normal", "low"]
            candidates = []
            for item in video_list:
                info = item.get("video_info") or {}
                url = info.get("url")
                if not url:
                    continue
                resolution = (info.get("resolution") or item.get("resolution") or "").lower()
                try:
                    rank = preference.index(resolution)
                except ValueError:
                    rank = len(preference)
                candidates.append((rank, url))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1], "success"
        return None, last_msg

    async def make_dir(self, parent_fid: str, dir_name: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/file", json={"dir_init_lock": False, "dir_path": "", "file_name": dir_name, "pdir_fid": parent_fid}, headers=self.headers)
            return _safe_json(res).get("code") == 0, "执行完成"

    async def rename(self, file_fid: str, new_name: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/file/rename", json={"fid": file_fid, "file_name": new_name}, headers=self.headers)
            return _safe_json(res).get("code") == 0, "执行完成"

    async def delete(self, file_fid: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/file/delete", json={"action_type": 1, "exclude_fids": [], "filelist": [file_fid]}, headers=self.headers)
            return _safe_json(res).get("code") == 0, "执行完成"


# ==========================================
# 阿里云盘 API 核心引擎 (纯享转存版，抛弃臃肿的鉴权)
# ==========================================
class Drive115:
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.timeout = 20.0
        self.headers = {
            "Cookie": self.cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

    async def list_files(self, cid: str = "0"):
        if not self.cookie:
            return [], "未配置 115 Cookie"

        items = []
        offset = 0
        limit = 1200
        try:
            client = self._client()
            while True:
                data = client.fs_files_aps({
                    "cid": cid,
                    "limit": limit,
                    "offset": offset,
                    "show_dir": 1,
                })
                if data.get("state") is False:
                    return [], data.get("error") or data.get("msg") or "115 目录读取失败"
                page_items = data.get("data") or []
                items.extend(page_items)
                if len(page_items) < limit:
                    break
                offset += limit
        except Exception as e:
            return [], str(e)
        return items, "success"

    async def get_download_url(self, pickcode: str):
        if not self.cookie:
            return None, "未配置 115 Cookie"
        if not pickcode:
            return None, "115 文件缺少 pickcode"

        try:
            ensure_p115_runtime_home()
            from p115client import P115Client
            url = P115Client(self.cookie).download_url(pickcode, app="chrome")
            if url:
                return str(url), "success"
        except Exception:
            pass

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            res = await client.get(
                "https://webapi.115.com/files/download",
                params={"pickcode": pickcode},
                headers=self.headers,
            )
            data = _safe_json(res)
            if data.get("state") is False:
                return None, data.get("error") or data.get("msg") or "115 下载地址获取失败"
            url = data.get("file_url") or data.get("url")
            if not url:
                return None, "115 下载地址为空"
            return url, "success"

    def _extract_share_code(self, share_url: str):
        match = re.search(r'/s/([a-zA-Z0-9]+)', share_url or "")
        return match.group(1) if match else None

    def _clean_receive_code(self, share_url: str, passcode: str = ""):
        value = (passcode or "").strip()
        if value.lower() in {"none", "null", "no", "n/a", "na", "-"}:
            value = ""
        if not value:
            query = parse_qs(urlparse(share_url or "").query)
            for key in ("password", "pwd", "passcode", "receive_code", "code"):
                if query.get(key):
                    value = query[key][0]
                    break
        match = re.search(r'([A-Za-z0-9]{4})', value)
        return match.group(1) if match else value

    def _share_item_id(self, item):
        return item.get("fid") or item.get("cid") or item.get("file_id") or item.get("category_id")

    def _share_item_name(self, item):
        return item.get("n") or item.get("fn") or item.get("file_name") or item.get("category_name") or item.get("name") or ""

    def _share_item_is_folder(self, item):
        if "fid" in item and "cid" in item:
            return False
        if item.get("fc") is not None:
            return str(item.get("fc")) == "0"
        if item.get("file_category") is not None:
            return str(item.get("file_category")) == "0"
        return bool(item.get("cid")) and not item.get("fid")

    async def get_share_file_list(self, share_url: str, passcode: str = "", cid: str = "0"):
        share_code = self._extract_share_code(share_url)
        if not share_code:
            return None, "无法解析 115 分享码"
        receive_code = self._clean_receive_code(share_url, passcode)
        try:
            data = self._client().share_snap({
                "share_code": share_code,
                "receive_code": receive_code,
                "cid": cid or "0",
                "limit": 1000,
                "offset": 0,
            })
            if isinstance(data, dict) and data.get("state") is False:
                return None, data.get("error") or data.get("msg") or "115 分享解析失败"
            items = (data.get("data") or {}).get("list") or []
            return items, "success"
        except Exception as e:
            return None, str(e)

    async def save_share(self, share_url: str, passcode: str = "", save_dir: str = "0"):
        if not self.cookie:
            return False, "未配置 115 Cookie"
        share_code = self._extract_share_code(share_url)
        if not share_code:
            return False, "无法解析 115 分享链接"
        receive_code = self._clean_receive_code(share_url, passcode)
        clean_save_dir = (save_dir or "0").split("-")[0].strip() or "0"

        items, msg = await self.get_share_file_list(share_url, receive_code, "0")
        if items is None:
            return False, msg
        if not items:
            return False, "115 分享内无文件或目录"

        filtered = []
        for item in items:
            name = self._share_item_name(item).lower()
            if self._share_item_is_folder(item) or name.endswith(VALID_VIDEO_EXTS):
                item_id = self._share_item_id(item)
                if item_id:
                    filtered.append(str(item_id))

        if not filtered:
            return False, "115 分享内未找到视频、字幕或目录"

        payload = {
            "share_code": share_code,
            "receive_code": receive_code,
            "file_id": ",".join(filtered),
            "cid": clean_save_dir,
        }
        try:
            client = self._client()
            data = client.share_receive(payload)
            ok, msg = self._format_result(data, "115 文件转存成功")
            if ok:
                return ok, msg
            try:
                data = client.share_receive_app(payload, app="android")
                return self._format_result(data, "115 文件转存成功")
            except Exception:
                return ok, msg
        except Exception as e:
            return False, str(e)

    async def add_offline_download(self, url: str, save_dir: str = "0"):
        if not self.cookie:
            return False, "未配置 115 Cookie"
        if not (url or "").strip():
            return False, "下载链接为空"
        clean_save_dir = (save_dir or "0").split("-")[0].strip() or "0"
        try:
            data = self._client().offline_add_url({
                "url": url.strip(),
                "wp_path_id": clean_save_dir,
            })
            return self._format_result(data, "115 离线下载任务已提交")
        except Exception as e:
            return False, f"115 离线下载失败: {str(e)}"

    def _client(self):
        ensure_p115_runtime_home()
        from p115client import P115Client
        return P115Client(self.cookie)

    def _format_result(self, data, success_msg):
        if isinstance(data, dict):
            code = data.get("code") or data.get("errno") or data.get("errNo")
            if data.get("state") is False or code not in (None, 0, "0", 200, "200"):
                return False, data.get("error") or data.get("msg") or data.get("message") or "115 操作失败"
        return True, success_msg

    async def make_dir(self, parent_id: str, dir_name: str):
        if not self.cookie:
            return False, "未配置 115 Cookie"
        try:
            data = self._client().fs_makedirs_app(dir_name, pid=parent_id or "0", app="chrome")
            return self._format_result(data, "115 文件夹创建成功")
        except Exception as e:
            return False, str(e)

    async def rename(self, file_id: str, new_name: str):
        return False, "当前 115 Web Cookie 暂不支持重命名，请在 115 客户端操作"

    async def delete(self, file_id: str):
        return False, "当前 115 Web Cookie 暂不支持删除，请在 115 客户端操作"


class AliyunDrive:
    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self.access_token = None
        self.default_drive_id = None
        self.timeout = 20.0
        self.api_url = "https://api.alipan.com"
        self.open_api_url = "https://openapi.aliyundrive.com"
        self.oauth_url = "https://aliyundrive-oauth.messense.me"

    def _format_error(self, data, default="请求失败"):
        if not isinstance(data, dict):
            return default
        message = data.get("message") or data.get("error_description") or data.get("error") or data.get("code") or default
        return str(message)

    def _extract_share_id(self, share_url: str):
        match = re.search(r'/s/([a-zA-Z0-9]+)', share_url)
        return match.group(1) if match else None

    def _clean_share_pwd(self, passcode: str = ""):
        passcode = (passcode or "").strip()
        if passcode.lower() in {"none", "null", "no", "n/a", "na", "-"} or passcode in {"无", "暂无", "无提取码"}:
            return ""
        if not passcode:
            return ""
        match = re.search(r'([A-Za-z0-9]{4})', passcode)
        return match.group(1) if match else passcode

    def _get_auth_header(self):
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    async def _refresh_access_token(self):
        if not self.refresh_token:
            return False, "未配置阿里云盘 Refresh Token"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.oauth_url}/oauth/access_token",
                    json={"refresh_token": self.refresh_token, "grant_type": "refresh_token"},
                )
                data = _safe_json(res)
                if "access_token" not in data:
                    return False, self._format_error(data, "阿里云盘 Token 刷新失败")
                self.access_token = data["access_token"]
                new_refresh_token = data.get("refresh_token", self.refresh_token)
                if new_refresh_token and new_refresh_token != self.refresh_token:
                    self.refresh_token = new_refresh_token
                    try:
                        from database import get_db
                        conn = get_db()
                        conn.execute("REPLACE INTO system_configs (config_key, config_value) VALUES ('token_aliyun', ?)", (new_refresh_token,))
                        conn.commit()
                        conn.close()
                    except Exception:
                        pass
                self.default_drive_id = data.get("default_drive_id")
                if not self.default_drive_id:
                    info_res = await client.post(
                        f"{self.open_api_url}/adrive/v1.0/user/getDriveInfo",
                        json={},
                        headers=self._get_auth_header(),
                    )
                    info = _safe_json(info_res)
                    self.default_drive_id = info.get("default_drive_id")
                if not self.default_drive_id:
                    return False, "阿里云盘 Drive ID 获取失败"
                return True, "success"
        except Exception as e:
            return False, str(e)

    async def get_share_token(self, share_id: str, passcode: str = ""):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/v2/share_link/get_share_token", json={"share_id": share_id, "share_pwd": passcode})
            data = _safe_json(res)
            token = data.get("share_token")
            if not token: return None, self._format_error(data, "获取 Share Token 失败")
            return token, "success"

    async def get_share_file_list(self, share_id: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/adrive/v3/share_link/get_share_by_anonymous?share_id={share_id}", json={"share_id": share_id}, headers=self._get_auth_header())
            data = _safe_json(res)
            return data.get("file_infos", []), self._format_error(data, "获取分享文件列表失败") if not data.get("file_infos") else "success"

    async def save_share(self, share_url: str, passcode: str = "", save_dir: str = "root"):
        share_id = self._extract_share_id(share_url)
        if not share_id: return False, "解析失败"
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        share_token, msg = await self.get_share_token(share_id, passcode)
        if not share_token: return False, msg
        file_infos, msg = await self.get_share_file_list(share_id)
        if not file_infos: return False, msg

        requests_list = []
        for f in file_infos:
            fname = (f.get("name") or "").lower()
            is_folder = f.get("type") == "folder"
            if not is_folder and not fname.endswith(VALID_VIDEO_EXTS):
                continue
            requests_list.append({
                "body": {
                    "file_id": f["file_id"], "share_id": share_id, "auto_rename": True,
                    "to_parent_file_id": save_dir.split('-')[0].strip() if save_dir else "root",
                    "to_drive_id": self.default_drive_id
                },
                "headers": {"Content-Type": "application/json"}, "id": str(len(requests_list)), "method": "POST", "url": "/file/copy"
            })

        if not requests_list:
            return False, "分享链接内未找到视频格式文件"

        headers = self._get_auth_header()
        headers["x-share-token"] = share_token
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                res = await client.post(f"{self.api_url}/v3/batch", json={"requests": requests_list, "resource": "file"}, headers=headers)
                data = _safe_json(res)
                if res.status_code in [200, 202]:
                    responses = data.get("responses") or []
                    failed = [item for item in responses if int(item.get("status", 200)) >= 400]
                    if failed:
                        first = failed[0].get("body") or failed[0]
                        return False, self._format_error(first, "阿里云盘转存被拒绝")
                    return True, "阿里云盘文件转存成功"
                return False, self._format_error(data, "阿里云盘转存被拒绝")
            except Exception as e: return False, str(e)

    async def get_share_token(self, share_id: str, passcode: str = ""):
        payload = {"share_id": share_id, "share_pwd": self._clean_share_pwd(passcode), "expire_sec": 7200}
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://www.alipan.com",
            "Referer": f"https://www.alipan.com/s/{share_id}",
            "User-Agent": "Mozilla/5.0",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            last_data = {}
            for host in [self.api_url, "https://api.aliyundrive.com"]:
                res = await client.post(f"{host}/v2/share_link/get_share_token", json=payload, headers=headers)
                data = _safe_json(res)
                token = data.get("share_token")
                if token:
                    return token, "success"
                last_data = data
            return None, self._format_error(last_data, "获取 Share Token 失败")

    async def get_share_file_list(self, share_id: str, share_token: str, parent_file_id: str = "root"):
        headers = {
            "Content-Type": "application/json",
            "x-share-token": share_token,
            "Referer": f"https://www.alipan.com/s/{share_id}",
            "User-Agent": "Mozilla/5.0",
        }
        items = []
        marker = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while True:
                payload = {
                    "share_id": share_id,
                    "parent_file_id": parent_file_id,
                    "limit": 100,
                    "order_by": "updated_at",
                    "order_direction": "DESC",
                }
                if marker:
                    payload["marker"] = marker
                res = await client.post(f"{self.api_url}/v2/file/list", json=payload, headers=headers)
                data = _safe_json(res)
                if res.status_code >= 400 or data.get("code"):
                    return [], self._format_error(data, "获取分享文件列表失败")
                items.extend(data.get("items", []))
                marker = data.get("next_marker")
                if not marker:
                    break
        return items, "success"

    async def save_share(self, share_url: str, passcode: str = "", save_dir: str = "root"):
        share_id = self._extract_share_id(share_url)
        if not share_id:
            return False, "解析失败"
        success, msg = await self._refresh_access_token()
        if not success:
            return False, msg
        share_token, msg = await self.get_share_token(share_id, passcode)
        if not share_token:
            return False, msg
        file_infos, msg = await self.get_share_file_list(share_id, share_token)
        if not file_infos:
            return False, msg

        copy_items = []
        for item in file_infos:
            fname = (item.get("name") or "").lower()
            is_folder = item.get("type") == "folder"
            if is_folder or fname.endswith(VALID_VIDEO_EXTS):
                copy_items.append(item)

        if not copy_items:
            return False, "分享链接内未找到视频格式文件"

        headers = self._get_auth_header()
        headers["x-share-token"] = share_token
        target_parent_id = save_dir.split('-')[0].strip() if save_dir else "root"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for item in copy_items:
                payload = {
                    "share_id": share_id,
                    "file_id": item["file_id"],
                    "to_drive_id": self.default_drive_id,
                    "to_parent_file_id": target_parent_id,
                    "auto_rename": True,
                }
                res = await client.post(f"{self.open_api_url}/v2/file/copy", json=payload, headers=headers)
                data = _safe_json(res)
                if res.status_code >= 400 or data.get("code"):
                    return False, self._format_error(data, "阿里云盘转存被拒绝")
        return True, "阿里云盘文件转存成功"

    async def list_files(self, parent_file_id: str = "root"):
        success, msg = await self._refresh_access_token()
        if not success: return [], msg
        items = []
        marker = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while True:
                payload = {
                    "drive_id": self.default_drive_id,
                    "parent_file_id": parent_file_id,
                    "limit": 200,
                    "fields": "*",
                    "order_by": "updated_at",
                    "order_direction": "DESC",
                }
                if marker:
                    payload["marker"] = marker
                res = await client.post(f"{self.open_api_url}/adrive/v1.0/openFile/list", json=payload, headers=self._get_auth_header())
                data = _safe_json(res)
                if res.status_code >= 400:
                    return [], self._format_error(data, "阿里云盘目录读取失败")
                items.extend(data.get("items", []))
                marker = data.get("next_marker")
                if not marker:
                    break
            return items, "success"

    async def get_download_url(self, file_id: str):
        success, msg = await self._refresh_access_token()
        if not success:
            return None, msg
        payload = {"drive_id": self.default_drive_id, "file_id": file_id, "expire_sec": 14400}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.open_api_url}/adrive/v1.0/openFile/getDownloadUrl", json=payload, headers=self._get_auth_header())
            data = _safe_json(res)
            url = data.get("url") or data.get("download_url")
            if url:
                return url, "success"
            return None, self._format_error(data, "获取下载地址失败")

    async def make_dir(self, parent_file_id: str, dir_name: str):
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.open_api_url}/adrive/v1.0/openFile/create", json={"check_name_mode": "refuse", "drive_id": self.default_drive_id, "name": dir_name, "parent_file_id": parent_file_id, "type": "folder"}, headers=self._get_auth_header())
            data = _safe_json(res)
            return res.status_code in [200, 201], self._format_error(data, "执行完成") if res.status_code >= 400 else "执行完成"

    async def rename(self, file_id: str, new_name: str):
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.open_api_url}/adrive/v1.0/openFile/update", json={"drive_id": self.default_drive_id, "file_id": file_id, "name": new_name}, headers=self._get_auth_header())
            data = _safe_json(res)
            return res.status_code == 200, self._format_error(data, "执行完成") if res.status_code >= 400 else "执行完成"

    async def delete(self, file_id: str):
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.open_api_url}/adrive/v1.0/openFile/recyclebin/trash", json={"drive_id": self.default_drive_id, "file_id": file_id}, headers=self._get_auth_header())
            data = _safe_json(res)
            return res.status_code in [200, 202], self._format_error(data, "执行完成") if res.status_code >= 400 else "执行完成"


class Drive123Open:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = 25.0
        self.api_url = "https://open-api.123pan.com"
        self.access_token = None
        self.expires_at = 0

    def _format_error(self, data, default="123云盘请求失败"):
        if not isinstance(data, dict):
            return default
        return str(data.get("message") or data.get("msg") or data.get("error") or data.get("code") or default)

    async def _get_token(self):
        if self.access_token and self.expires_at > time.time() + 300:
            return True, "success"
        if not self.client_id or not self.client_secret:
            return False, "未配置 123云盘 Client ID / Client Secret"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                f"{self.api_url}/api/v1/access_token",
                json={"clientID": self.client_id, "clientSecret": self.client_secret},
                headers={"Platform": "open_platform", "Content-Type": "application/json"},
            )
            data = _safe_json(res)
            if res.status_code >= 400 or data.get("code") not in (0, None):
                return False, self._format_error(data, "123云盘 Token 获取失败")
            token_data = data.get("data") or {}
            token = token_data.get("accessToken")
            if not token:
                return False, "123云盘 Token 返回为空"
            self.access_token = token
            expired_at = token_data.get("expiredAt")
            try:
                self.expires_at = datetime.datetime.fromisoformat(str(expired_at).replace("Z", "+00:00")).timestamp()
            except Exception:
                self.expires_at = time.time() + 3600
            return True, "success"

    async def _headers(self):
        ok, msg = await self._get_token()
        if not ok:
            return None, msg
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Platform": "open_platform",
            "Content-Type": "application/json",
        }, "success"

    async def list_files(self, parent_file_id: str = "0"):
        headers, msg = await self._headers()
        if not headers:
            return [], msg
        items = []
        last_file_id = 0
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while last_file_id != -1:
                params = {"parentFileId": parent_file_id or "0", "limit": 100, "lastFileId": last_file_id}
                res = await client.get(f"{self.api_url}/api/v2/file/list", params=params, headers=headers)
                data = _safe_json(res)
                if res.status_code >= 400 or data.get("code") not in (0, None):
                    return [], self._format_error(data, "123云盘目录读取失败")
                body = data.get("data") or {}
                page_items = [item for item in (body.get("fileList") or []) if int(item.get("trashed") or 0) == 0]
                items.extend(page_items)
                next_last_file_id = int(body.get("lastFileId") if body.get("lastFileId") is not None else -1)
                if next_last_file_id == last_file_id:
                    break
                last_file_id = next_last_file_id
        return items, "success"

    async def get_download_url(self, file_id: str):
        headers, msg = await self._headers()
        if not headers:
            return None, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"{self.api_url}/api/v1/direct-link/url", params={"fileID": file_id}, headers=headers)
            data = _safe_json(res)
            if res.status_code >= 400 or data.get("code") not in (0, None):
                return None, self._format_error(data, "123云盘直链获取失败")
            url = (data.get("data") or {}).get("url")
            return (url, "success") if url else (None, "123云盘直链为空")

    async def make_dir(self, parent_file_id: str, dir_name: str):
        headers, msg = await self._headers()
        if not headers:
            return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                f"{self.api_url}/upload/v1/file/mkdir",
                json={"name": dir_name, "parentID": int(parent_file_id or 0)},
                headers=headers,
            )
            data = _safe_json(res)
            return data.get("code") == 0, self._format_error(data, "执行完成") if data.get("code") else "执行完成"

    async def rename(self, file_id: str, new_name: str):
        headers, msg = await self._headers()
        if not headers:
            return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.put(
                f"{self.api_url}/api/v1/file/name",
                json={"fileId": int(file_id), "fileName": new_name},
                headers=headers,
            )
            data = _safe_json(res)
            return data.get("code") == 0, self._format_error(data, "执行完成") if data.get("code") else "执行完成"

    async def delete(self, file_id: str):
        headers, msg = await self._headers()
        if not headers:
            return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                f"{self.api_url}/api/v1/file/trash",
                json={"fileIDs": [int(file_id)]},
                headers=headers,
            )
            data = _safe_json(res)
            return data.get("code") == 0, self._format_error(data, "执行完成") if data.get("code") else "执行完成"
from aliyun_drive_mobile import AliyunDrive as AliyunDrive
