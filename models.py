from pydantic import BaseModel
from typing import Optional, List

class ConfigModel(BaseModel):
    api_domain: str
    image_domain: str
    api_key: str
    pansou_domain: str
    cron_expression: str
    cms_api_url: str
    cms_api_token: str
    cookie_quark: Optional[str] = "" 
    token_aliyun: Optional[str] = ""
    quark_save_dir: Optional[str] = "0"
    aliyun_save_dir: Optional[str] = "root"
    auto_subscribe_new: Optional[str] = "0"  
    auto_subscribe_drive: Optional[str] = "115"  # 【新增】自动订阅的目标网盘

class SubscribeModel(BaseModel):
    tmdb_id: int
    media_type: str
    title: str
    overview: Optional[str] = ""
    poster_path: Optional[str] = ""
    force: Optional[bool] = False
    drive_type: Optional[str] = "115"

class BatchSubscribeModel(BaseModel):
    items: List[SubscribeModel]

class BatchDeleteModel(BaseModel):
    tmdb_ids: List[int]

class SaveLinkModel(BaseModel):
    tmdb_id: int
    title: str
    media_type: str
    poster_path: Optional[str] = ""
    url: str
    pwd: Optional[str] = ""
    drive_type: str

class DriveListReq(BaseModel):
    drive_type: str
    parent_id: str

class DriveActionReq(BaseModel):
    drive_type: str
    action: str 
    file_id: Optional[str] = None
    new_name: Optional[str] = None

class QrcodeStatusModel(BaseModel):
    uid: str
    time: int
    sign: str

class QrcodeLoginModel(BaseModel):
    uid: str

class StrmConfigModel(BaseModel):
    config_name: str
    url: str
    username: str
    password: Optional[str] = ""
    rootpath: str
    target_directory: str
    download_enabled: int = 1
    update_mode: str = "incremental"
    download_interval_range: str = "1-3"

class StrmSettingsModel(BaseModel):
    video_formats: str
    subtitle_formats: str
    image_formats: str
    metadata_formats: str
    size_threshold: int
    download_threads: int

class ReplaceDomainModel(BaseModel):
    target_directory: str
    old_domain: str
    new_domain: str

class StrmTaskModel(BaseModel):
    task_name: str
    config_id: int
    cron_expression: str
    is_enabled: int