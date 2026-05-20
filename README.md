# CineLink 云幕智链

CineLink 是一个本地影视资源管理与 STRM 生成工具，支持 TMDB 热门数据、聚合搜索、115 / 夸克 / 阿里云盘 / 123 云盘接入、转存记录、剧集追更、网盘文件管理、链接有效性检测，以及基于内置 AList 的 STRM 扫描与播放代理。

## Docker 部署

推荐服务器直接使用已经在线构建好的镜像：

```bash
docker compose pull
docker compose up -d
```

默认镜像：

```text
akjehsmhq5/cinelink:latest
```

启动后访问：

- Web 控制台：http://服务器IP:8000
- 内置 AList：http://服务器IP:5244
- AList 挂载路径：`/quark`、`/aliyun`、`/115`、`/123`

旧的 `8088` 内置 WebDAV 服务已移除。现在默认使用内置 AList + CineLink 播放代理生成 STRM，外部 WebDAV 仍可作为自定义 STRM 来源使用。

## docker-compose.yml

仓库内置的 `docker-compose.yml` 适合服务器直接运行，核心挂载如下：

```text
./data:/app/data
./data/strm_output:/data/media
```

目录说明：

- `/app/data`：SQLite 数据库、系统配置、AList 数据目录。
- `/data/media`：STRM 文件输出目录。

如果播放器、Emby、Jellyfin 或其他设备不在 Docker 宿主机本机，需要设置公开访问地址：

```bash
export CINELINK_PLAY_PUBLIC_URL=http://服务器IP:8000
export CINELINK_ALIST_PUBLIC_URL=http://服务器IP:5244
docker compose up -d
```

Windows PowerShell 示例：

```powershell
$env:CINELINK_PLAY_PUBLIC_URL="http://服务器IP:8000"
$env:CINELINK_ALIST_PUBLIC_URL="http://服务器IP:5244"
docker compose up -d
```

新生成的 STRM 默认使用 `CINELINK_PLAY_PUBLIC_URL` 的播放代理地址，例如：

```text
http://192.168.68.200:8000/play/quark/...
```

播放代理会转发播放器的 `Range` 请求到网盘直链，减少直接播放时的反复解析和卡顿。

## 在线打包

本项目通过 GitHub Actions 在线构建镜像，不需要本地打包。

当前已配置：

- `.github/workflows/docker-publish.yml`：推送到 Docker Hub `akjehsmhq5/cinelink`
- `.github/workflows/ghcr-publish.yml`：推送到 GitHub Container Registry
- 触发分支：`main`、`codex/docker-package-support`
- 触发标签：`v*.*.*`
- 手动触发：GitHub Actions 页面里的 `workflow_dispatch`

Docker Hub 需要在 GitHub 仓库 Secrets 配置：

```text
DOCKER_USERNAME
DOCKER_PASSWORD
```

推送当前分支后，Actions 会自动构建并发布镜像：

```bash
git push origin codex/docker-package-support
```

## 常用命令

```bash
docker compose logs -f
docker compose restart
docker compose down
docker compose pull
```

## 环境变量

可参考 `.env.example`：

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

说明：

- `CINELINK_PLAY_PUBLIC_URL`：STRM 播放代理公开地址，服务器部署时必须改成服务器 IP 或域名。
- `CINELINK_ALIST_PUBLIC_URL`：内置 AList 公开地址，服务器部署时建议改成服务器 IP 或域名。
- `CINELINK_STRM_OUTPUT_DIR`：容器内 STRM 输出根目录。
- `CINELINK_DOWNLOAD_URL_CACHE_TTL`：网盘直链缓存时间。
- `CINELINK_PATH_CACHE_TTL`：网盘路径解析缓存时间。
- `CINELINK_PLAY_CHUNK_SIZE`：播放代理发送给播放器的数据块大小，默认 4MB。

## Docker 适配检查

当前容器运行方式：

- 基础镜像：`python:3.12-slim`
- 应用入口：`uvicorn main:app --host 0.0.0.0 --port 8000`
- 内置 AList：镜像构建时下载 Linux amd64 二进制到 `/usr/local/bin/alist`
- 健康检查：`curl -fsS http://127.0.0.1:8000/`
- 数据持久化：`/app/data`
- STRM 输出：`/data/media`

注意：

- 不要把宿主机的 `data` 目录复制进镜像，`.dockerignore` 已排除。
- 不要在服务器继续暴露旧的 `8088` WebDAV 端口。
- 如果镜像在服务器启动后播放地址仍是 `127.0.0.1`，请检查 `CINELINK_PLAY_PUBLIC_URL` 和 `CINELINK_ALIST_PUBLIC_URL`。

## ?? fnOS ??

?????????? FPK ?????

- `fnos/`?Docker ? FPK???????? `akjehsmhq5/cinelink:v2.2.0` ???
- `fnos-native/`???? FPK??????? Docker????? Linux x86_64 Python ????????? AList ????

??? FPK ? GitHub Actions ?????

```text
.github/workflows/fnos-native-fpk.yml
```

???????

```text
???????/vol1/@appdata/cinelink-native/config
STRM ???/vol1/@appdata/cinelink-native/media
Web ???8000
?????????http://127.0.0.1:8000
```

????????? x86_64 ???ARM ????????????? Python ???? AList?
