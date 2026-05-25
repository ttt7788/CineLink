# CineLink 云幕智链

CineLink 是一套本地影视资源管理与 STRM 生成系统，面向家庭影音库、网盘资源整理和自动追剧场景。系统提供热门影视采集、聚合搜索、网盘转存、订阅搜刮、剧集追更、资源整理、STRM 生成和播放代理能力。

## 核心功能

- 今日热门：采集当天热门影视内容，快速发现可转存资源。
- 电影库 / 剧集库：同步 TMDB 影视条目，支持分页浏览、搜盘和订阅。
- 全网聚合搜索：对接盘搜接口，按网盘类型展示结果，支持链接检测后转存。
- 转存下载：自动识别网盘分享链接、磁力和 ED2K，保存到已配置的默认目录。
- 订阅搜刮：按计划搜索资源，自动尝试可用链接并转存到目标网盘。
- 剧集追更：剧集转存后可绑定网盘目录，定时扫描新增集数。
- 网盘文件：支持夸克、阿里云盘、115 网盘和 123 云盘的目录浏览与基础管理。
- 资源整理：按分类策略、命名规则和洗版策略整理网盘文件。
- STRM 管理：通过内置 AList 挂载网盘，生成 STRM 文件并提供播放代理。
- 插件与回收站：集中管理回收站自动清空等可选能力。
- 配置中心：统一配置搜刮源、网盘授权、默认保存目录和插件选项。
- 运行日志：按功能模块查看采集、搜索、转存、STRM、播放和网盘运行状态。

## 部署方式

### Docker 部署

推荐服务器直接使用已发布镜像：

```bash
docker compose pull
docker compose up -d
```

默认镜像：

```text
akjehsmhq5/cinelink:latest
```

启动后访问：

- Web 控制台：`http://服务器IP:8000`
- 内置 AList：`http://服务器IP:5244`
- AList 挂载路径：`/quark`、`/aliyun`、`/115`、`/123`

仓库内置的 `docker-compose.yml` 可直接用于部署。核心挂载如下：

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

### fnOS 飞牛原生版

飞牛原生版位于 `fnos-native/`，不依赖 Docker，包内包含 Linux x86_64 Python 运行时、项目依赖和 AList。

默认目录：

```text
配置与数据库：/vol1/@appdata/cinelinknative/config
STRM 输出：/vol1/@appdata/cinelinknative/media
Web 端口：8000
播放公开地址：http://127.0.0.1:8000
```

安装时可在飞牛配置界面修改配置目录、STRM 输出目录、Web 端口和播放公开地址。若播放器在其他设备上访问，请把播放公开地址改为飞牛设备的局域网 IP。

### fnOS 飞牛 Docker 版

飞牛 Docker 版位于 `fnos/`，用于通过飞牛应用包管理 Docker 编排。它使用已发布镜像运行 CineLink，适合希望继续使用容器方式部署的环境。

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

重要说明：

- `CINELINK_PLAY_PUBLIC_URL`：STRM 播放代理公开地址，服务器部署时应改成服务器 IP 或域名。
- `CINELINK_ALIST_PUBLIC_URL`：内置 AList 公开地址，服务器部署时建议改成服务器 IP 或域名。
- `CINELINK_STRM_OUTPUT_DIR`：STRM 输出根目录。
- `CINELINK_DOWNLOAD_URL_CACHE_TTL`：网盘直链缓存时间。
- `CINELINK_PATH_CACHE_TTL`：网盘路径解析缓存时间。
- `CINELINK_PLAY_CHUNK_SIZE`：播放代理发送给播放器的数据块大小，默认 4MB。

## 常用命令

```bash
docker compose logs -f
docker compose restart
docker compose down
docker compose pull
```

## STRM 播放

CineLink 默认使用内置 AList 和播放代理生成 STRM。播放器访问 STRM 后，CineLink 会解析网盘地址并转发 `Range` 请求，减少播放时的重复解析。

示例地址：

```text
http://192.168.68.200:8000/play/quark/...
```

旧的 `8088` 内置 WebDAV 服务已移除。外部 WebDAV 仍可作为自定义 STRM 来源使用。

## 在线构建

GitHub Actions 已提供自动构建流程：

- `.github/workflows/docker-publish.yml`：发布 Docker Hub 镜像。
- `.github/workflows/ghcr-publish.yml`：发布 GHCR 镜像。
- `.github/workflows/fnos-native-fpk.yml`：构建飞牛原生 FPK。

触发方式：

- 推送到指定分支。
- 推送 `v*` 标签。
- 在 GitHub Actions 页面手动运行。

## 版权声明

Copyright (c) 2026 ttt7788.

CineLink 云幕智链项目代码全程由 Codex 编写。项目中使用的第三方库、网盘接口、AList、Element Plus、Vue、TMDB 相关数据和其他外部服务分别遵循其各自的许可协议、服务条款和版权声明。

本项目仅用于个人学习、家庭媒体库管理和合法资源整理。请勿将本项目用于侵犯版权、绕过平台规则或传播未授权内容。
