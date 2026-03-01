import httpx
import datetime
import random
import re

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
        params = self._get_base_params()
        params.update({"pdir_fid": dir_fid, "sort": "update_at", "asc": "0"})
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"{self.api_url}/file/sort", params=params, headers=self.headers)
            data = _safe_json(res)
            if data.get("code") == 0: return data.get("data", {}).get("list", []), "success"
            return [], data.get("message", "获取失败")

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
class AliyunDrive:
    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self.access_token = None
        self.default_drive_id = None
        self.timeout = 20.0
        self.api_url = "https://api.alipan.com"

    def _extract_share_id(self, share_url: str):
        match = re.search(r'/s/([a-zA-Z0-9]+)', share_url)
        return match.group(1) if match else None

    def _get_auth_header(self):
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    async def _refresh_access_token(self):
        if not self.refresh_token: return False, "未配置 Token"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post("https://auth.alipan.com/v2/account/token", json={"refresh_token": self.refresh_token, "grant_type": "refresh_token"})
                data = _safe_json(res)
                if "access_token" not in data: return False, data.get("message", "刷新失败")
                self.access_token = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                self.default_drive_id = data.get("default_drive_id")
                return True, "success"
        except Exception as e: return False, str(e)

    async def get_share_token(self, share_id: str, passcode: str = ""):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/v2/share_link/get_share_token", json={"share_id": share_id, "share_pwd": passcode})
            data = _safe_json(res)
            token = data.get("share_token")
            if not token: return None, data.get("message", "失败")
            return token, "success"

    async def get_share_file_list(self, share_id: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/adrive/v3/share_link/get_share_by_anonymous?share_id={share_id}", json={"share_id": share_id}, headers=self._get_auth_header())
            return _safe_json(res).get("file_infos", [])

    async def save_share(self, share_url: str, passcode: str = "", save_dir: str = "root"):
        share_id = self._extract_share_id(share_url)
        if not share_id: return False, "解析失败"
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        share_token, msg = await self.get_share_token(share_id, passcode)
        if not share_token: return False, "获取 Token 失败"
        file_infos = await self.get_share_file_list(share_id)
        if not file_infos: return False, "无文件"
        
        requests_list = []
        for idx, f in enumerate(file_infos):
            requests_list.append({
                "body": {
                    "file_id": f["file_id"], "share_id": share_id, "auto_rename": True, 
                    "to_parent_file_id": save_dir.split('-')[0].strip() if save_dir else "root", 
                    "to_drive_id": self.default_drive_id
                },
                "headers": {"Content-Type": "application/json"}, "id": str(idx), "method": "POST", "url": "/file/copy"
            })
            
        headers = self._get_auth_header()
        headers["x-share-token"] = share_token
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                res = await client.post(f"{self.api_url}/v3/batch", json={"requests": requests_list, "resource": "file"}, headers=headers)
                if res.status_code in [200, 202]: return True, "转存成功"
                return False, _safe_json(res).get("message", "被拒绝")
            except Exception as e: return False, str(e)
            
    async def list_files(self, parent_file_id: str = "root"):
        success, msg = await self._refresh_access_token()
        if not success: return [], msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/v2/file/list", json={"drive_id": self.default_drive_id, "parent_file_id": parent_file_id, "limit": 100, "order_by": "updated_at", "order_direction": "DESC"}, headers=self._get_auth_header())
            return _safe_json(res).get("items", []), "success"

    async def make_dir(self, parent_file_id: str, dir_name: str):
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/adrive/v2/file/createWithFolders", json={"check_name_mode": "refuse", "drive_id": self.default_drive_id, "name": dir_name, "parent_file_id": parent_file_id, "type": "folder"}, headers=self._get_auth_header())
            return res.status_code in [200, 201], "执行完成"

    async def rename(self, file_id: str, new_name: str):
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/v3/file/update", json={"check_name_mode": "refuse", "drive_id": self.default_drive_id, "file_id": file_id, "name": new_name}, headers=self._get_auth_header())
            return res.status_code == 200, "执行完成"

    async def delete(self, file_id: str):
        success, msg = await self._refresh_access_token()
        if not success: return False, msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/v2/recyclebin/trash", json={"drive_id": self.default_drive_id, "file_id": file_id}, headers=self._get_auth_header())
            return res.status_code in [200, 202], "执行完成"