import os
import subprocess
import sys
from fastapi import APIRouter, BackgroundTasks
from database import get_db
from models import StrmConfigModel, StrmSettingsModel, ReplaceDomainModel, StrmTaskModel
from logger import add_log

# 就是这一行缺失或未保存导致了报错
strm_router = APIRouter()

@strm_router.get("/api/strm/configs")
def get_strm_configs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM strm_configs").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@strm_router.post("/api/strm/configs")
def add_strm_config(config: StrmConfigModel):
    conn = get_db()
    conn.execute('''INSERT INTO strm_configs 
        (config_name, url, username, password, rootpath, target_directory, download_enabled, update_mode, download_interval_range) 
        VALUES (?,?,?,?,?,?,?,?,?)''', 
        (config.config_name, config.url, config.username, config.password, config.rootpath, 
         config.target_directory, config.download_enabled, config.update_mode, config.download_interval_range))
    conn.commit(); conn.close()
    add_log("INFO", f"🔗 新增 WebDAV 节点: [{config.config_name}] ({config.url})")
    return {"message": "WebDAV节点添加成功"}

@strm_router.put("/api/strm/configs/{config_id}")
def update_strm_config(config_id: int, config: StrmConfigModel):
    conn = get_db()
    conn.execute('''UPDATE strm_configs SET 
        config_name=?, url=?, username=?, password=?, rootpath=?, target_directory=?, 
        download_enabled=?, update_mode=?, download_interval_range=? WHERE id=?''', 
        (config.config_name, config.url, config.username, config.password, config.rootpath, 
         config.target_directory, config.download_enabled, config.update_mode, config.download_interval_range, config_id))
    conn.commit(); conn.close()
    add_log("INFO", f"📝 修改 WebDAV 节点: [{config.config_name}] (ID: {config_id})")
    return {"message": "节点配置已更新"}

@strm_router.delete("/api/strm/configs/{config_id}")
def delete_strm_config(config_id: int):
    conn = get_db()
    conn.execute("DELETE FROM strm_configs WHERE id = ?", (config_id,))
    conn.commit(); conn.close()
    add_log("WARNING", f"🗑️ 删除 WebDAV 节点 (ID: {config_id})")
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
def get_strm_records(page: int = 1, size: int = 50):
    conn = get_db()
    offset = (page - 1) * size
    query = '''SELECT r.*, c.config_name FROM strm_records r 
               LEFT JOIN strm_configs c ON r.config_id = c.id 
               ORDER BY r.id DESC LIMIT ? OFFSET ?'''
    rows = conn.execute(query, (size, offset)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM strm_records").fetchone()[0]
    conn.close()
    return {"items": [dict(row) for row in rows], "total": total}

@strm_router.delete("/api/strm/records/clear")
def clear_strm_records():
    conn = get_db()
    conn.execute("DELETE FROM strm_records")
    conn.commit(); conn.close()
    add_log("WARNING", "🧹 用户手动清空了全部 STRM 成功记录缓存！下次生成将执行全量比对。")
    return {"message": "历史记录已全部清空"}

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