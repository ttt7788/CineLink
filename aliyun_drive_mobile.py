import httpx
import re
import uuid

VALID_VIDEO_EXTS = (
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".ts", ".m2ts",
    ".rmvb", ".iso", ".vob", ".webm", ".srt", ".ass", ".sub", ".nfo",
)


def _safe_json(res):
    try:
        return res.json()
    except Exception:
        return {"code": -999, "message": f"HTTP {res.status_code}"}


class AliyunDrive:
    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self.access_token = None
        self.default_drive_id = None
        self.timeout = 25.0
        self.api_url = "https://api.alipan.com"
        self.auth_url = "https://auth.alipan.com"
        self.ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

    def _format_error(self, data, default="请求失败"):
        if not isinstance(data, dict):
            return default
        return str(
            data.get("message")
            or data.get("error_description")
            or data.get("error")
            or data.get("code")
            or default
        )

    def _extract_share_id(self, share_url: str):
        match = re.search(r"/s/([a-zA-Z0-9]+)", share_url or "")
        return match.group(1) if match else None

    def _clean_share_pwd(self, passcode: str = ""):
        passcode = (passcode or "").strip()
        if not passcode or passcode.lower() in {"none", "null", "no", "n/a", "na", "-"}:
            return ""
        if passcode in {"无", "暂无", "无提取码"}:
            return ""
        match = re.search(r"([A-Za-z0-9]{4})", passcode)
        return match.group(1) if match else passcode

    def _auth_headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": self.ua,
            "Origin": "https://www.alipan.com",
            "Referer": "https://www.alipan.com/",
            "X-Canary": "client=Android,app=adrive,version=v4.1.0",
            "x-request-id": str(uuid.uuid4()),
        }

    def _share_headers(self, share_id: str, share_token: str = ""):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.ua,
            "Origin": "https://www.alipan.com",
            "Referer": f"https://www.alipan.com/s/{share_id}",
        }
        if share_token:
            headers["x-share-token"] = share_token
        return headers

    async def _refresh_access_token(self):
        if not self.refresh_token:
            return False, "未配置阿里云盘移动端 Refresh Token"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.auth_url}/v2/account/token",
                    json={"refresh_token": self.refresh_token, "grant_type": "refresh_token"},
                    headers={"Content-Type": "application/json", "User-Agent": self.ua},
                )
                data = _safe_json(res)
                if res.status_code >= 400 or not data.get("access_token"):
                    if data.get("code") == "InvalidParameter.RefreshToken":
                        return False, "当前阿里云盘 Refresh Token 不是移动端 token 或已失效，请重新扫码获取"
                    return False, self._format_error(data, "阿里云盘移动端 Token 刷新失败")

                self.access_token = data["access_token"]
                self.default_drive_id = data.get("default_drive_id") or data.get("default_sbox_drive_id")
                new_refresh_token = data.get("refresh_token") or self.refresh_token
                if new_refresh_token and new_refresh_token != self.refresh_token:
                    self.refresh_token = new_refresh_token
                    self._save_refresh_token(new_refresh_token)

                if not self.default_drive_id:
                    return False, "阿里云盘 Drive ID 获取失败，请重新扫码获取移动端 Refresh Token"
                return True, "success"
        except Exception as e:
            return False, str(e)

    def _save_refresh_token(self, refresh_token: str):
        try:
            from database import get_db

            conn = get_db()
            conn.execute(
                "REPLACE INTO system_configs (config_key, config_value) VALUES ('token_aliyun', ?)",
                (refresh_token,),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    async def get_share_token(self, share_id: str, passcode: str = ""):
        payload = {
            "share_id": share_id,
            "share_pwd": self._clean_share_pwd(passcode),
            "expire_sec": 7200,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                f"{self.api_url}/v2/share_link/get_share_token",
                json=payload,
                headers=self._share_headers(share_id),
            )
            data = _safe_json(res)
            token = data.get("share_token")
            if token:
                return token, "success"
            return None, self._format_error(data, "获取阿里云盘 Share Token 失败")

    async def get_share_file_list(self, share_id: str, share_token: str, parent_file_id: str = "root"):
        items = []
        marker = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while True:
                payload = {
                    "share_id": share_id,
                    "parent_file_id": parent_file_id,
                    "limit": 200,
                    "order_by": "updated_at",
                    "order_direction": "DESC",
                }
                if marker:
                    payload["marker"] = marker
                res = await client.post(
                    f"{self.api_url}/adrive/v2/file/list_by_share",
                    json=payload,
                    headers=self._share_headers(share_id, share_token),
                )
                data = _safe_json(res)
                if res.status_code >= 400 or data.get("code"):
                    res = await client.post(
                        f"{self.api_url}/v2/file/list",
                        json=payload,
                        headers=self._share_headers(share_id, share_token),
                    )
                    data = _safe_json(res)
                    if res.status_code >= 400 or data.get("code"):
                        return [], self._format_error(data, "获取阿里云盘分享文件列表失败")
                items.extend(data.get("items", []))
                marker = data.get("next_marker")
                if not marker:
                    break
        return items, "success"

    async def save_share(self, share_url: str, passcode: str = "", save_dir: str = "root"):
        share_id = self._extract_share_id(share_url)
        if not share_id:
            return False, "无法解析阿里云盘分享链接"
        success, msg = await self._refresh_access_token()
        if not success:
            return False, msg
        share_token, msg = await self.get_share_token(share_id, passcode)
        if not share_token:
            return False, msg
        file_infos, msg = await self.get_share_file_list(share_id, share_token)
        if not file_infos:
            return False, msg

        target_parent_id = save_dir.split("-")[0].strip() if save_dir else "root"
        headers = self._auth_headers()
        headers["x-share-token"] = share_token
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            copied = await self._copy_share_items_recursive(
                client, share_id, share_token, file_infos, target_parent_id, headers
            )
            if copied < 0:
                return False, self._last_copy_error or "阿里云盘转存被拒绝"
            if copied == 0:
                return False, "分享链接内未找到视频、字幕或媒体元数据文件"
        return True, f"阿里云盘文件转存成功，共复制 {copied} 个媒体文件"

    async def _copy_share_items_recursive(self, client, share_id, share_token, items, target_parent_id, headers):
        copied = 0
        self._last_copy_error = ""
        for item in items:
            if item.get("type") == "folder":
                folder_id = await self._create_folder(client, target_parent_id, item.get("name") or "未命名文件夹")
                if not folder_id:
                    return -1
                children, msg = await self.get_share_file_list(share_id, share_token, item.get("file_id"))
                if msg != "success":
                    self._last_copy_error = msg
                    return -1
                child_count = await self._copy_share_items_recursive(
                    client, share_id, share_token, children, folder_id, headers
                )
                if child_count < 0:
                    return -1
                copied += child_count
                continue

            fname = (item.get("name") or "").lower()
            if not fname.endswith(VALID_VIDEO_EXTS):
                continue
            payload = {
                "drive_id": self.default_drive_id,
                "file_id": item["file_id"],
                "share_id": share_id,
                "to_drive_id": self.default_drive_id,
                "to_parent_file_id": target_parent_id,
                "auto_rename": True,
            }
            res = await client.post(f"{self.api_url}/v2/file/copy", json=payload, headers=headers)
            data = _safe_json(res)
            if res.status_code >= 400 or data.get("code"):
                self._last_copy_error = self._format_error(data, "阿里云盘转存被拒绝")
                return -1
            copied += 1
        return copied

    async def _create_folder(self, client, parent_file_id: str, folder_name: str):
        payload = {
            "drive_id": self.default_drive_id,
            "parent_file_id": parent_file_id,
            "name": folder_name,
            "type": "folder",
            "check_name_mode": "auto_rename",
        }
        res = await client.post(f"{self.api_url}/adrive/v2/file/createWithFolders", json=payload, headers=self._auth_headers())
        data = _safe_json(res)
        if res.status_code >= 400 or data.get("code"):
            self._last_copy_error = self._format_error(data, "创建阿里云盘目录失败")
            return None
        return data.get("file_id")

    async def list_files(self, parent_file_id: str = "root"):
        success, msg = await self._refresh_access_token()
        if not success:
            return [], msg
        items = []
        marker = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while True:
                payload = {
                    "drive_id": self.default_drive_id,
                    "parent_file_id": parent_file_id,
                    "limit": 200,
                    "order_by": "updated_at",
                    "order_direction": "DESC",
                }
                if marker:
                    payload["marker"] = marker
                res = await client.post(f"{self.api_url}/v2/file/list", json=payload, headers=self._auth_headers())
                data = _safe_json(res)
                if res.status_code >= 400 or data.get("code"):
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
            res = await client.post(f"{self.api_url}/v2/file/get_download_url", json=payload, headers=self._auth_headers())
            data = _safe_json(res)
            url = data.get("url") or data.get("download_url")
            if url:
                return url, "success"
            return None, self._format_error(data, "获取阿里云盘下载地址失败")

    async def make_dir(self, parent_file_id: str, dir_name: str):
        success, msg = await self._refresh_access_token()
        if not success:
            return False, msg
        payload = {
            "drive_id": self.default_drive_id,
            "parent_file_id": parent_file_id,
            "name": dir_name,
            "type": "folder",
            "check_name_mode": "auto_rename",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/adrive/v2/file/createWithFolders", json=payload, headers=self._auth_headers())
            data = _safe_json(res)
            return res.status_code in [200, 201, 202], self._format_error(data, "执行完成") if res.status_code >= 400 else "执行完成"

    async def rename(self, file_id: str, new_name: str):
        success, msg = await self._refresh_access_token()
        if not success:
            return False, msg
        payload = {"drive_id": self.default_drive_id, "file_id": file_id, "name": new_name, "check_name_mode": "auto_rename"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/v3/file/update", json=payload, headers=self._auth_headers())
            data = _safe_json(res)
            return res.status_code == 200, self._format_error(data, "执行完成") if res.status_code >= 400 else "执行完成"

    async def delete(self, file_id: str):
        success, msg = await self._refresh_access_token()
        if not success:
            return False, msg
        payload = {"drive_id": self.default_drive_id, "file_id": file_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.api_url}/v2/recyclebin/trash", json=payload, headers=self._auth_headers())
            data = _safe_json(res)
            return res.status_code in [200, 202], self._format_error(data, "执行完成") if res.status_code >= 400 else "执行完成"
