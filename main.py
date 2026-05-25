import uvicorn
import mimetypes
import os
import asyncio
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import init_db, get_db, get_sys_config
from api_routes import router
from play_routes import play_router
from strm_routes import strm_router
from scheduler import auto_subscription_task
from logger import add_log
from alist_sidecar import start_alist_sidecar, stop_alist_sidecar
from alist_integration import sync_alist_storages
from config_guard import log_startup_drive_config_status
from plugin_recycle import router as recycle_router, auto_empty_recyclebin_if_due

# 修复 Windows 注册表 MIME 类型 Bug
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

if not os.path.exists("templates"):
    os.makedirs("templates")

templates = Jinja2Templates(directory="templates")

# 修改 Jinja2 语法防止与 Vue 冲突
templates.env.block_start_string = '[%'
templates.env.block_end_string = '%]'
templates.env.variable_start_string = '[['
templates.env.variable_end_string = ']]'
templates.env.comment_start_string = '[#'
templates.env.comment_end_string = '#]'

DEFAULT_CRON_EXPRESSION = "0 10,22 * * *"

@asynccontextmanager
async def lifespan(app: FastAPI):
    add_log("INFO", "🚀 CineLink 核心引擎开始启动...")
    init_db()
    add_log("INFO", "✅ SQLite 数据库与数据表初始化就绪。")
    log_startup_drive_config_status()
    start_alist_sidecar()
    sync_alist_storages()
    task = asyncio.create_task(background_task_loop())
    recycle_task = asyncio.create_task(background_recycle_plugin_loop())
    add_log("INFO", "🌐 核心路由接口、STRM矩阵模块与静态资源加载完成。")
    add_log("INFO", "🎉 CineLink 系统启动完毕，正在监听端口请求。")
    yield
    task.cancel()
    recycle_task.cancel()
    stop_alist_sidecar()
    add_log("WARNING", "🛑 系统收到关闭信号，后台守护进程与服务器已安全终止。")

# 【核心修改】API 接口文档增加版本号 v2.0.1
app = FastAPI(title="CineLink 云幕智链 - 核心 API v2.0.1", lifespan=lifespan)

def _parse_cron_number_field(value, minimum, maximum):
    result = set()
    for part in str(value or "*").split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            result.update(range(minimum, maximum + 1))
        elif part.startswith("*/"):
            step = max(int(part[2:]), 1)
            result.update(range(minimum, maximum + 1, step))
        else:
            number = int(part)
            if minimum <= number <= maximum:
                result.add(number)
    return result


def _next_cron_run(expr, now=None):
    now = now or datetime.datetime.now()
    fields = str(expr or "").strip().split()
    if len(fields) != 5:
        raise ValueError("cron 表达式必须是 5 段：分 时 日 月 周")

    minute_field, hour_field, day_field, month_field, week_field = fields
    minutes = _parse_cron_number_field(minute_field, 0, 59)
    hours = _parse_cron_number_field(hour_field, 0, 23)
    days = None if day_field == "*" else _parse_cron_number_field(day_field, 1, 31)
    months = None if month_field == "*" else _parse_cron_number_field(month_field, 1, 12)
    weekdays = None if week_field == "*" else _parse_cron_number_field(week_field, 0, 7)

    cursor = (now + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
    deadline = now + datetime.timedelta(days=366)
    while cursor <= deadline:
        cron_weekday = (cursor.weekday() + 1) % 7
        if (
            cursor.minute in minutes
            and cursor.hour in hours
            and (days is None or cursor.day in days)
            and (months is None or cursor.month in months)
            and (weekdays is None or cron_weekday in weekdays or (cron_weekday == 0 and 7 in weekdays))
        ):
            return cursor
        cursor += datetime.timedelta(minutes=1)
    raise ValueError("未来 366 天内没有匹配的执行时间")


def _reset_invalid_cron_expression(expr):
    conn = get_db()
    try:
        conn.execute(
            "REPLACE INTO system_configs (config_key, config_value) VALUES (?, ?)",
            ("cron_expression", DEFAULT_CRON_EXPRESSION),
        )
        conn.commit()
    finally:
        conn.close()
    add_log("INFO", f"【搜刮调度】Cron 配置无效，已自动恢复为默认时间：每天 10:00 与 22:00。原配置：{expr}")


async def background_task_loop():
    add_log("INFO", "⏰ 后台搜刮调度守护已启动，默认每天 10:00 与 22:00 执行。")
    while True:
        config = get_sys_config()
        cron_expr = config.get("cron_expression") or DEFAULT_CRON_EXPRESSION
        cron_base = datetime.datetime.now()
        cron_start_after = (config.get("cron_start_after") or "").strip()
        if cron_start_after:
            try:
                start_after = datetime.datetime.fromisoformat(cron_start_after)
                if cron_base < start_after:
                    cron_base = start_after - datetime.timedelta(minutes=1)
            except Exception:
                pass
        try:
            next_run = _next_cron_run(cron_expr, cron_base)
        except Exception:
            _reset_invalid_cron_expression(cron_expr)
            cron_expr = DEFAULT_CRON_EXPRESSION
            next_run = _next_cron_run(cron_expr, cron_base)

        wait_seconds = max(1, (next_run - datetime.datetime.now()).total_seconds())
        add_log("INFO", f"【搜刮调度】下一次自动搜刮时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (Cron: {cron_expr})")
        await asyncio.sleep(wait_seconds)

        try:
            await auto_subscription_task()
        except Exception as e:
            add_log("ERROR", f"后台守护任务异常: {e}")

async def background_recycle_plugin_loop():
    add_log("INFO", "🧩 插件守护进程已启动，回收站自动清空将按配置执行。")
    await asyncio.sleep(15)
    while True:
        try:
            await auto_empty_recyclebin_if_due()
        except Exception as e:
            add_log("ERROR", f"插件守护任务异常: {e}")
        await asyncio.sleep(300)

app.include_router(router)
app.include_router(play_router)
app.include_router(strm_router)
app.include_router(recycle_router)

if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root(request: Request):
    if not os.path.exists("templates/index.html"):
        return {"error": "未找到 templates/index.html"}
    return templates.TemplateResponse(request, "index.html")

if __name__ == "__main__":
    # 【核心修改】终端启动横幅增加版本号 v2.0.1
    print("=======================================================")
    print("🎬 CineLink (云幕智链) 控制台中枢启动中... [版本: v2.0.1]")
    print("👉 请在浏览器访问: http://127.0.0.1:8000")
    print("=======================================================")
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
