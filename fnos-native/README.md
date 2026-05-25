# CineLink 飞牛原生版 FPK

该目录用于构建 CineLink 飞牛原生应用包。原生版不依赖 Docker，包内包含：

- CineLink 应用源码
- Linux x86_64 Python 运行时
- `requirements.txt` 中声明的 Python 依赖
- Linux x86_64 AList 二进制
- 飞牛应用安装、配置、启动、停止脚本

## 默认目录

```text
配置与数据库：/vol1/@appdata/cinelinknative/config
STRM 输出：/vol1/@appdata/cinelinknative/media
Web 端口：8000
播放公开地址：http://127.0.0.1:8000
内置 AList：http://127.0.0.1:5244
```

安装时可在飞牛配置界面修改目录、端口和播放公开地址。若播放器从局域网其他设备访问，请把播放公开地址改为飞牛设备 IP。

## 在线构建

GitHub Actions 工作流：

```text
.github/workflows/fnos-native-fpk.yml
```

推送 `v*` 标签或手动运行工作流后，会生成 `cinelinknative.fpk`。

## 本地构建

Linux x86_64 环境可执行：

```bash
bash fnos-native/scripts/assemble-native.sh
curl -fsSL -o /tmp/fnpack https://static2.fnnas.com/fnpack/fnpack-1.2.1-linux-amd64
chmod +x /tmp/fnpack
/tmp/fnpack build --directory dist/fnos-native/cinelink
```

## 版权声明

Copyright (c) 2026 ttt7788.

CineLink 云幕智链项目代码全程由 Codex 编写。第三方组件和外部服务遵循其各自许可协议、服务条款和版权声明。
