import datetime
import re

from database import get_db


LOG_MODULES = {
    "system": "系统",
    "startup": "启动检查",
    "tmdb": "媒体库采集",
    "subscription": "订阅搜刮",
    "transfer": "转存下载",
    "manual_transfer": "手动转存",
    "series": "剧集追更",
    "strm": "STRM",
    "drive": "网盘文件",
    "play": "播放代理",
    "alist": "内置 AList",
    "plugin": "插件",
    "auth": "授权登录",
    "link_check": "链接检测",
    "pansou": "盘搜搜索",
}


_MODULE_RULES = [
    ("startup", ("启动检查", "CineLink 核心引擎", "系统启动", "SQLite 数据库")),
    ("tmdb", ("今日热门", "库同步", "电影库", "剧集库", "TMDB", "基础库")),
    ("strm", ("STRM", "strm", "STRM写入进度", "STRM 写入进度", "映射", "元数据下载进度", "AList 扫描进度", "数据库比对缓存")),
    ("subscription", ("定时任务", "搜刮", "推送", "自动订阅", "候选资源", "目标网盘", "资源已入库", "全部尝试失败", "网盘已有极佳版本")),
    ("manual_transfer", ("手动转存",)),
    ("transfer", ("转存下载",)),
    ("series", ("剧集绑定", "剧集追更")),
    ("drive", ("内置网盘", "网盘", "目录读取", "下载地址获取")),
    ("play", ("播放代理", "AList 播放")),
    ("alist", ("内置AList", "内置 AList")),
    ("plugin", ("插件", "回收站")),
    ("auth", ("二维码", "扫码", "登录", "Refresh Token", "Cookie")),
    ("link_check", ("链接检测", "PanCheck")),
    ("pansou", ("盘搜",)),
]


def infer_log_module(message: str, module: str = "") -> str:
    """Return a stable module key for log filtering."""
    explicit = (module or "").strip()
    text = str(message or "")
    if "STRM" in text or "strm" in text:
        return "strm"
    if explicit:
        return explicit
    bracket = re.match(r"^[\s🚀✅🌐🎉🛑⏰🧩🔗📁📄⚠️❌]*【([^】]+)】", text)
    if bracket:
        label = bracket.group(1)
        for key, keywords in _MODULE_RULES:
            if any(word in label for word in keywords):
                return key
    for key, keywords in _MODULE_RULES:
        if any(word in text for word in keywords):
            return key
    return "system"


def add_log(level: str, message: str, module: str = ""):
    """写入系统日志到数据库，自动补充功能模块。"""
    conn = get_db()
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_module = infer_log_module(message, module)
        conn.execute(
            "INSERT INTO system_logs (level, module, message, created_at) VALUES (?, ?, ?, ?)",
            ((level or "INFO").upper(), log_module, message, now),
        )
        conn.commit()
    except Exception as e:
        print(f"写入日志失败: {e}")
    finally:
        conn.close()


def get_logs(limit: int = 100, module: str = "", level: str = ""):
    """获取最新日志，支持按功能模块和级别筛选。"""
    limit = max(1, min(int(limit or 100), 500))
    conn = get_db()
    try:
        params = []
        where = []
        if level and level != "all":
            if level.upper() == "WARNING":
                where.append("UPPER(level) IN ('WARNING', 'WARN')")
            else:
                where.append("UPPER(level) = ?")
                params.append(level.upper())
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        fetch_limit = min(limit * 5, 1000) if module and module != "all" else limit
        rows = conn.execute(
            f"""
            SELECT id, level, COALESCE(module, 'system') AS module, message, created_at
            FROM system_logs
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, fetch_limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            raw_module = item.get("module", "")
            item["module"] = infer_log_module(item.get("message", ""), "" if raw_module == "system" else raw_module)
            if module and module != "all" and item["module"] != module:
                continue
            item["module_label"] = LOG_MODULES.get(item["module"], item["module"])
            result.append(item)
            if len(result) >= limit:
                break
        return result
    finally:
        conn.close()


def get_log_modules():
    """返回日志模块统计，用于前端快速筛选。"""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(module, 'system') AS module, message
            FROM system_logs
            ORDER BY id DESC
            LIMIT 2000
            """
        ).fetchall()
        counts = {}
        for row in rows:
            raw_module = row["module"] or "system"
            module = infer_log_module(row["message"] or "", "" if raw_module == "system" else raw_module)
            counts[module] = counts.get(module, 0) + 1
        modules = []
        seen = set()
        for module, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
            seen.add(module)
            modules.append({
                "module": module,
                "label": LOG_MODULES.get(module, module),
                "count": count,
            })
        for module, label in LOG_MODULES.items():
            if module not in seen:
                modules.append({"module": module, "label": label, "count": 0})
        return modules
    finally:
        conn.close()
