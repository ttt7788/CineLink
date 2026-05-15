import os
import subprocess
import sys
from fastapi import APIRouter, BackgroundTasks, HTTPException
from config_guard import require_drive_ready
from database import get_db, get_sys_config
from models import StrmConfigModel, StrmSettingsModel, ReplaceDomainModel, StrmTaskModel
from logger import add_log

# 就是这一行缺失或未保存导致了报错
strm_router = APIRouter()

INTERNAL_ROOTS = {"115_internal": "/115", "aliyun_internal": "/aliyun", "quark_internal": "/quark", "123_internal": "/123"}
INTERNAL_DRIVES = {"115_internal": "115", "aliyun_internal": "aliyun", "quark_internal": "quark", "123_internal": "123"}
INTERNAL_SAVE_DIR_KEYS = {"115": "drive115_save_dir", "aliyun": "aliyun_save_dir", "quark": "quark_save_dir", "123": "drive123_save_dir"}


def clean_config_dir_id(value, fallback=""):
    value = str(value or "").strip()
    if not value:
        return fallback
    return value.split("-")[0].strip() or fallback


def normalize_internal_rootpath(source_type, rootpath):
    base = INTERNAL_ROOTS[source_type]
    raw = str(rootpath or "").replace("\\", "/").strip()
    if not raw or raw == "/":
        return base
    raw = raw.replace("/dav", "", 1) if raw.startswith("/dav") else raw
    if not raw.startswith("/"):
        raw = "/" + raw
    if raw == base or raw.startswith(base.rstrip("/") + "/"):
        return raw.rstrip("/")
    return (base.rstrip("/") + "/" + raw.strip("/")).rstrip("/")


def normalize_strm_config(config: StrmConfigModel):
    source_type = config.source_type or "webdav"
    if source_type in INTERNAL_ROOTS:
        drive_type = INTERNAL_DRIVES[source_type]
        ready, ready_msg = require_drive_ready(drive_type)
        if not ready:
            raise HTTPException(status_code=400, detail=ready_msg)
        cfg = get_sys_config()
        fallback_id = clean_config_dir_id(cfg.get(INTERNAL_SAVE_DIR_KEYS[drive_type]), "root" if drive_type == "aliyun" else "0")
        root_id = clean_config_dir_id(config.root_id, fallback_id)
        return source_type, "internal://alist", "", "", normalize_internal_rootpath(source_type, config.rootpath), root_id
    return source_type, config.url, config.username, config.password, config.rootpath, ""

@strm_router.get("/api/strm/configs")
def get_strm_configs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM strm_configs").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@strm_router.post("/api/strm/configs")
def add_strm_config(config: StrmConfigModel):
    conn = get_db()
    source_type, url, username, password, rootpath, root_id = normalize_strm_config(config)
    conn.execute('''INSERT INTO strm_configs
        (source_type, config_name, url, username, password, rootpath, root_id, target_directory, download_enabled, update_mode, download_interval_range)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (source_type, config.config_name, url, username, password, rootpath, root_id,
         config.target_directory, config.download_enabled, config.update_mode, config.download_interval_range))
    conn.commit(); conn.close()
    add_log("INFO", f"🔗 新增 STRM 节点: [{config.config_name}] ({source_type})")
    return {"message": "STRM节点添加成功"}

@strm_router.put("/api/strm/configs/{config_id}")
def update_strm_config(config_id: int, config: StrmConfigModel):
    conn = get_db()
    source_type, url, username, password, rootpath, root_id = normalize_strm_config(config)
    conn.execute('''UPDATE strm_configs SET
        source_type=?, config_name=?, url=?, username=?, password=?, rootpath=?, root_id=?, target_directory=?,
        download_enabled=?, update_mode=?, download_interval_range=? WHERE id=?''',
        (source_type, config.config_name, url, username, password, rootpath, root_id,
         config.target_directory, config.download_enabled, config.update_mode, config.download_interval_range, config_id))
    conn.commit(); conn.close()
    add_log("INFO", f"📝 修改 STRM 节点: [{config.config_name}] (ID: {config_id}, 来源: {source_type})")
    return {"message": "节点配置已更新"}

@strm_router.delete("/api/strm/configs/{config_id}")
def delete_strm_config(config_id: int):
    conn = get_db()
    conn.execute("DELETE FROM strm_configs WHERE id = ?", (config_id,))
    conn.commit(); conn.close()
    add_log("WARNING", f"🗑️ 删除 STRM 节点 (ID: {config_id})")
    return {"message": "配置已删除"}

@strm_router.get("/api/strm/settings")
def get_strm_settings():
    conn = get_db()
    row = conn.execute("SELECT * FROM strm_settings LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {}

@strm_router.post("/api/strm/settings")
def update_strm_settings(settings: StrmSettingsModel):
    conn = get_db()
    conn.execute('''UPDATE strm_settings SET 
        video_formats=?, subtitle_formats=?, image_formats=?, metadata_formats=?, size_threshold=?, download_threads=? 
        WHERE id=(SELECT id FROM strm_settings LIMIT 1)''',
        (settings.video_formats, settings.subtitle_formats, settings.image_formats, settings.metadata_formats, 
         settings.size_threshold, settings.download_threads))
    conn.commit(); conn.close()
    add_log("INFO", f"⚙️ 更新 STRM 全局规则 (并发线程: {settings.download_threads}, 过滤体积: {settings.size_threshold}MB)")
    return {"message": "STRM 生成规则保存成功"}

@strm_router.post("/api/strm/run/{config_id}")
def run_strm_generator(config_id: int, background_tasks: BackgroundTasks):
    conn = get_db()
    row = conn.execute("SELECT source_type, config_name FROM strm_configs WHERE id=?", (config_id,)).fetchone()
    conn.close()
    if row and row["source_type"] in INTERNAL_DRIVES:
        ready, ready_msg = require_drive_ready(INTERNAL_DRIVES[row["source_type"]])
        if not ready:
            add_log("WARNING", f"【STRM】节点 [{row['config_name']}] 未启动：{ready_msg}")
            raise HTTPException(status_code=400, detail=ready_msg)
    script_path = os.path.join(os.path.dirname(__file__), 'strm_generator.py')
    def run_script():
        add_log("INFO", f"🚀 正在拉起 STRM 矩阵生成作业 (关联节点ID: {config_id})...")
        subprocess.Popen([sys.executable, script_path, str(config_id)])
    background_tasks.add_task(run_script)
    return {"message": "STRM 生成任务已在后台多线程启动，请查看日志。"}

@strm_router.post("/api/strm/replace_domain")
def replace_domain(req: ReplaceDomainModel, background_tasks: BackgroundTasks):
    script_path = os.path.join(os.path.dirname(__file__), 'replace_domain.py')
    def run_replace():
        add_log("INFO", f"🔧 启动域名一键替换作业: 将目录 [{req.target_directory}] 中的 {req.old_domain} 替换为 {req.new_domain}")
        subprocess.Popen([sys.executable, script_path, req.target_directory, req.old_domain, req.new_domain])
    background_tasks.add_task(run_replace)
    return {"message": "批量域名替换任务已投递后台。"}

@strm_router.get("/api/strm/records")
def get_strm_records(page: int = 1, size: int = 50, config_id: int = 0):
    conn = get_db()
    offset = (page - 1) * size
    summaries = conn.execute('''SELECT c.id, c.config_name, c.source_type, COUNT(r.id) AS record_count
                                FROM strm_configs c
                                LEFT JOIN strm_records r ON r.config_id = c.id
                                GROUP BY c.id
                                ORDER BY c.id''').fetchall()
    where = ""
    params = []
    if config_id:
        where = "WHERE r.config_id = ?"
        params.append(config_id)
    query = '''SELECT r.*, c.config_name FROM strm_records r
               LEFT JOIN strm_configs c ON r.config_id = c.id
               {where}
               ORDER BY r.id DESC LIMIT ? OFFSET ?'''.format(where=where)
    rows = conn.execute(query, (*params, size, offset)).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM strm_records r {where}", params).fetchone()[0]
    conn.close()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "summaries": [dict(row) for row in summaries],
    }

@strm_router.delete("/api/strm/records/clear")
def clear_strm_records(config_id: int = 0):
    conn = get_db()
    if config_id:
        conn.execute("DELETE FROM strm_records WHERE config_id=?", (config_id,))
        config = conn.execute("SELECT config_name FROM strm_configs WHERE id=?", (config_id,)).fetchone()
        target = config["config_name"] if config else f"ID {config_id}"
        add_log("WARNING", f"🧹 用户手动清空了 STRM 节点 [{target}] 的成功记录缓存，下次生成将重新比对。")
    else:
        conn.execute("DELETE FROM strm_records")
        add_log("WARNING", "🧹 用户手动清空了全部 STRM 成功记录缓存！下次生成将执行全量比对。")
    conn.commit(); conn.close()
    return {"message": "记录缓存已清空"}

@strm_router.get("/api/strm/tasks")
def get_strm_tasks():
    conn = get_db()
    rows = conn.execute("SELECT * FROM strm_tasks").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@strm_router.post("/api/strm/tasks")
def add_strm_task(task: StrmTaskModel):
    conn = get_db()
    conn.execute("INSERT INTO strm_tasks (task_name, config_id, cron_expression, is_enabled) VALUES (?,?,?,?)", 
                 (task.task_name, task.config_id, task.cron_expression, task.is_enabled))
    conn.commit(); conn.close()
    add_log("INFO", f"⏰ 新增自动化定时任务: [{task.task_name}] (Cron: {task.cron_expression})")
    return {"message": "任务创建成功"}

@strm_router.put("/api/strm/tasks/{task_id}")
def update_strm_task(task_id: int, task: StrmTaskModel):
    conn = get_db()
    conn.execute('''UPDATE strm_tasks SET 
                    task_name=?, config_id=?, cron_expression=?, is_enabled=? 
                    WHERE id=?''', 
                 (task.task_name, task.config_id, task.cron_expression, task.is_enabled, task_id))
    conn.commit(); conn.close()
    add_log("INFO", f"📝 修改定时任务: [{task.task_name}] (ID: {task_id})")
    return {"message": "任务修改成功"}

@strm_router.delete("/api/strm/tasks/{task_id}")
def delete_strm_task(task_id: int):
    conn = get_db()
    conn.execute("DELETE FROM strm_tasks WHERE id=?", (task_id,))
    conn.commit(); conn.close()
    add_log("WARNING", f"🗑️ 删除定时任务 (ID: {task_id})")
    return {"message": "任务已删除"}

@strm_router.post("/api/strm/tasks/status")
def toggle_task_status(req: dict):
    conn = get_db()
    conn.execute("UPDATE strm_tasks SET is_enabled=? WHERE id=?", (req['is_enabled'], req['id']))
    conn.commit(); conn.close()
    status_str = "启用" if req['is_enabled'] == 1 else "停用"
    add_log("INFO", f"⏸️ 更新定时任务状态: 任务 ID {req['id']} 已{status_str}")
    return {"message": "状态更新成功"}
