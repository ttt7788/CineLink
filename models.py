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