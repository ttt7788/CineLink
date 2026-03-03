import uvicorn
import mimetypes
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import init_db
from api_routes import router
from strm_routes import strm_router
from scheduler import auto_subscription_task
from logger import add_log

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    add_log("INFO", "🚀 CineLink 核心引擎开始启动...")
    init_db()
    add_log("INFO", "✅ SQLite 数据库与数据表初始化就绪。")
    task = asyncio.create_task(background_task_loop())
    add_log("INFO", "🌐 核心路由接口、STRM矩阵模块与静态资源加载完成。")
    add_log("INFO", "🎉 CineLink 系统启动完毕，正在监听端口请求。")
    yield
    task.cancel()
    add_log("WARNING", "🛑 系统收到关闭信号，后台守护进程与服务器已安全终止。")

# 【名称修改】API 接口文档标题
app = FastAPI(title="CineLink 云幕智链 - 核心 API", lifespan=lifespan)

async def background_task_loop():
    add_log("INFO", "⏰ 后台调度守护进程已启动，系统将【每天执行一次】自动搜刮与转存。")
    await asyncio.sleep(5) 
    while True:
        try:
            await auto_subscription_task()
        except Exception as e:
            add_log("ERROR", f"后台守护任务异常: {e}")
        
        await asyncio.sleep(86400) 

app.include_router(router)
app.include_router(strm_router)

if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root(request: Request):
    if not os.path.exists("templates/index.html"):
        return {"error": "未找到 templates/index.html"}
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    # 【名称修改】终端启动横幅
    print("=======================================================")
    print("🎬 CineLink (云幕智链) 控制台中枢启动中...")
    print("👉 请在浏览器访问: http://127.0.0.1:8000")
    print("=======================================================")
    uvicorn.run("main:app", host="0.0.0.0", port=8000)