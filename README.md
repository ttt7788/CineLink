# CineLink 云幕智链

CineLink 是一个本地影视资源管理与 STRM 生成工具，支持 TMDB 热门数据、聚合搜索、115/夸克/阿里云盘转存、网盘文件管理、链接有效性检测，以及基于内置 AList 的 STRM 扫描与播放代理。

## Docker 快速启动

```bash
docker compose up -d --build
```

启动后访问：

- Web 控制台：http://127.0.0.1:8000
- 内置 AList：http://127.0.0.1:5244
- AList 挂载路径：`/quark`、`/aliyun`、`/115`

镜像会自动下载 AList Linux 二进制，应用启动时会把已配置的夸克、阿里云盘和 115 登录信息同步到 AList。旧的 8088 内置 WebDAV 服务已移除；外部 WebDAV 仍可作为自定义 STRM 来源使用。

## 持久化目录

默认 `docker-compose.yml` 会挂载：

```text
./data:/app/data
./data/strm_output:/data/media
```

其中：

- `/app/data` 保存 SQLite 数据库、系统配置和 AList 数据目录。
- `/data/media` 建议作为 STRM 生成目标目录。

## 服务器部署

如果播放器、Emby/Jellyfin 或其他设备不在 Docker 宿主机本机，需要把公开地址改成服务器 IP 或域名：

```bash
CINELINK_PLAY_PUBLIC_URL=http://服务器IP:8000
CINELINK_ALIST_PUBLIC_URL=http://服务器IP:5244
docker compose up -d
```

新生成的 STRM 默认使用 `CINELINK_PLAY_PUBLIC_URL` 的播放代理地址，例如 `http://192.168.1.10:8000/play/quark/...`，代理会把播放器的 Range 请求转发给网盘直链，减少播放卡顿。

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
TZ=Asia/Shanghai
CINELINK_DATA_DIR=/app/data
CINELINK_PLAY_PUBLIC_URL=http://127.0.0.1:8000
CINELINK_STRM_OUTPUT_DIR=/data/media
CINELINK_DOWNLOAD_URL_CACHE_TTL=300
CINELINK_PATH_CACHE_TTL=3600
CINELINK_PLAY_CHUNK_SIZE=4194304
CINELINK_PLAY_LOG_REQUESTS=1
CINELINK_STRM_BACKEND=play
CINELINK_ALIYUN_STRM_MODE=preview
CINELINK_QUARK_STRM_MODE=preview
CINELINK_ALIST_ENABLED=1
CINELINK_ALIST_BIN=/usr/local/bin/alist
CINELINK_ALIST_DATA_DIR=/app/data/alist
CINELINK_ALIST_BIND_HOST=0.0.0.0
CINELINK_ALIST_CHECK_HOST=127.0.0.1
CINELINK_ALIST_PORT=5244
CINELINK_ALIST_INTERNAL_URL=http://127.0.0.1:5244
CINELINK_ALIST_PUBLIC_URL=http://127.0.0.1:5244
```

`CINELINK_STRM_OUTPUT_DIR` 是容器内 STRM 输出根目录。如果节点里保存了空目录、Windows 路径或相对路径，系统会自动兜底到该目录下的网盘子目录。

`CINELINK_DOWNLOAD_URL_CACHE_TTL` 和 `CINELINK_PATH_CACHE_TTL` 用于缓存网盘直链与路径解析，减少播放器频繁探测时反复请求网盘 API。

`CINELINK_PLAY_CHUNK_SIZE` 控制播放代理向播放器发送的数据块大小，默认 4MB，适合视频播放。
