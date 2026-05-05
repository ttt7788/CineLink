# CineLink 云幕智链

CineLink 是一个本地影视资源管理与 STRM 生成工具，支持 TMDB 热门数据、聚合搜索、115/夸克/阿里云盘转存，以及内置 WebDAV 生成 STRM。

## Docker 快速启动

```bash
docker compose up -d --build
```

启动后访问：

- Web 控制台：http://127.0.0.1:8000
- 内置 WebDAV：http://127.0.0.1:8088
- 115 内置 WebDAV 根路径：http://127.0.0.1:8088/115
- 阿里云盘内置 WebDAV 根路径：http://127.0.0.1:8088/aliyun
- 夸克内置 WebDAV 根路径：http://127.0.0.1:8088/quark

## 持久化目录

默认 `docker-compose.yml` 会挂载：

```text
./data:/app/data
./data/strm_output:/data/media
```

其中：

- `/app/data` 保存 SQLite 数据库和系统配置。
- `/data/media` 建议作为 STRM 生成目标目录。

## 常用命令

```bash
docker compose logs -f
docker compose restart
docker compose down
docker compose pull
```

## 环境变量

可以参考 `.env.example` 调整：

```text
CINELINK_DATA_DIR=/app/data
CINELINK_WEBDAV_BIND_HOST=0.0.0.0
CINELINK_WEBDAV_PORT=8088
CINELINK_WEBDAV_INTERNAL_URL=http://127.0.0.1:8088
CINELINK_WEBDAV_PUBLIC_URL=http://127.0.0.1:8088
```

`CINELINK_WEBDAV_INTERNAL_URL` 是容器内部扫描内置 WebDAV 时使用的地址，通常保持 `http://127.0.0.1:8088`。

如果 STRM 文件需要给局域网其他设备播放，只把 `CINELINK_WEBDAV_PUBLIC_URL` 改成宿主机局域网地址，例如：

```text
CINELINK_WEBDAV_PUBLIC_URL=http://192.168.1.10:8088
```
