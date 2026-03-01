import uvicorn
import mimetypes
import os
import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# 导入系统模块
from database import init_db, get_sys_config
from api_routes import router
from scheduler import auto_subscription_task
from logger import add_log

# ==========================================
# 【核心修复】：强制修正 Windows 注册表 MIME 类型 Bug
# 解决本地静态文件被识别为纯文本，导致 Vue 无法执行和页面白屏的问题
# ==========================================
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

# 初始化 FastAPI 应用
app = FastAPI(title="影视全自动订阅中枢")

# ==========================================
# 生命周期与后台任务管理
# ==========================================
@app.on_event("startup")
async def startup_event():
    # 1. 初始化数据库表结构
    init_db()
    add_log("INFO", "系统启动：数据库已就绪。")
    
    # 2. 启动后台守护任务
    asyncio.create_task(background_task_loop())

async def background_task_loop():
    add_log("INFO", "守护进程：后台自动搜刮与转存任务已启动。")
    # 稍微延迟启动，等待服务完全拉起
    await asyncio.sleep(5) 
    while True:
        try:
            # 执行核心调度逻辑
            await auto_subscription_task()
        except Exception as e:
            add_log("ERROR", f"后台守护任务异常: {e}")
        
        # 默认每 30 分钟 (1800秒) 轮询一次全网资源
        await asyncio.sleep(1800) 

# ==========================================
# 路由与静态资源挂载
# ==========================================

# 1. 注册 API 接口路由
app.include_router(router)

# 2. 挂载本地静态文件目录 (允许浏览器加载 static/lib 里的 JS 和 CSS)
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. 根路由：返回前端操作页面
@app.get("/")
async def root():
    # 确保根目录下有 index.html 文件
    if not os.path.exists("index.html"):
        return {"error": "找不到 index.html 文件，请检查项目目录。"}
    return FileResponse("index.html")

# ==========================================
# 主程序入口
# ==========================================
if __name__ == "__main__":
    print("=======================================================")
    print("🎬 影视中枢控制台启动中...")
    print("👉 请在浏览器访问: http://127.0.0.1:8000")
    print("=======================================================")
    uvicorn.run("main:app", host="0.0.0.0", port=8000)