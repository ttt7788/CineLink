# CineLink 飞牛 Docker 版 FPK

该目录用于构建 CineLink 飞牛 Docker 应用包。安装后由飞牛应用包管理 Docker 编排，并使用已发布镜像运行 CineLink。

默认镜像：

```text
akjehsmhq5/cinelink:v2.2.0
```

## 默认目录

```text
配置与数据库：飞牛应用配置目录 / cinelink_config
STRM 输出：飞牛应用配置目录 / cinelink_media
Web 端口：8000
内置 AList 端口：5244
```

安装时可在飞牛配置界面修改配置目录、STRM 输出目录、Web 端口和播放公开地址。

## 构建

```bash
fnpack build --directory fnos/cinelink
```

## 版权声明

Copyright (c) 2026 ttt7788.

CineLink 云幕智链项目代码全程由 Codex 编写。第三方组件和外部服务遵循其各自许可协议、服务条款和版权声明。
