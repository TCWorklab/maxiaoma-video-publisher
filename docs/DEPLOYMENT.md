# 部署马小马视频发布平台

这是本机运行的软件。先安装 Python 3.12 或更高版本（建议 3.12）、Node.js 24、FFmpeg（同时包含 ffprobe），并确保它们在 PATH 中。下载完整源码 ZIP 并解压，或使用 README 中的 git clone 命令。

下面的手动启动方式便于诊断，前端与后端各使用一个终端。日常也可在依赖就绪后运行根目录 `start.bat`、`bash start-macos.sh` 或 `bash start.sh`；这些便捷脚本还会准备可选 MCP 服务，需要 Git，macOS/Linux 还需 `lsof`。端口已占用时脚本会退出，请先关闭旧实例。

## Windows

在项目根目录打开 PowerShell：

```powershell
python --version
node --version
ffmpeg -version
ffprobe -version
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -c "from cloakbrowser import ensure_binary; ensure_binary()"
cd frontend
npm.cmd ci
cd ..
```

终端一，启动后端：

```powershell
$env:PYTHONUTF8 = '1'
$env:SAU_HOST = '127.0.0.1'
$env:SAU_PORT = '5409'
backend/.venv/Scripts/python.exe backend/app.py
```

终端二，启动前端：

```powershell
cd frontend
npm.cmd run dev -- --host localhost --port 5173 --strictPort
```

## macOS / Linux

确认已安装依赖后，在项目根目录执行：

```sh
python3 --version
node --version
ffmpeg -version
ffprobe -version
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
backend/.venv/bin/python -c "from cloakbrowser import ensure_binary; ensure_binary()"
(cd frontend && npm ci)
```

终端一：

```sh
SAU_HOST=127.0.0.1 SAU_PORT=5409 backend/.venv/bin/python backend/app.py
```

终端二：

```sh
cd frontend
npm run dev -- --host localhost --port 5173 --strictPort
```

Linux 扫码登录必须有可显示浏览器窗口的桌面会话。仅 SSH 的无桌面服务器不等同于可直接扫码使用的部署环境。

## 使用与停止

1. 打开 `http://localhost:5173/`。后端健康地址应返回 JSON：`http://127.0.0.1:5409/api/health`。
2. 在账号管理中添加自己的平台账号，按提示扫码登录。
3. 上传视频，选择发布账号，填写文案、标签和封面后发布；首次建议只用一个账号和一条测试素材验证。
4. 在两个终端分别按 Ctrl+C 停止服务。电脑重启后需要重新启动服务。

“网络连接失败”可能是后端未启动或端口不通。先检查健康地址，再检查前端终端和后端日志，不要重复提交同一条发布。

## 数据与更新

默认数据目录为项目下 `data/`，首次启动自动创建空数据库。也可在启动后端之前设置 `SAU_DATA_DIR` 为自己的绝对路径。不要将下载的新源码与旧 `data/` 直接覆盖混放；更新前停止服务并备份数据。

git clone 安装且工作区干净时，在项目根目录运行 `git pull --ff-only`；macOS/Linux 也可以 `bash update.sh`。更新后重新安装有变化的依赖，再启动。ZIP 安装请解压到新目录，通过 `SAU_DATA_DIR` 指向已备份的数据目录。

不要把 Cookie、数据库、日志、视频、签名证书或 `.env` 推送到仓库。默认新安装未启用定时账号维护；需要时在账号管理页面开启。

## 可选 MCP 服务

普通网页发布不需要 MCP。需要 AI 工具接入时，参照 [backend-mcp/README.md](../backend-mcp/README.md)，从 `.env.example` 创建自己的配置，安装依赖并按说明运行；默认端口 5410。

## macOS 安装包

本仓库提供 `Build MaXiaoMa macOS installers` 手动 Actions 工作流，分别在 macOS Apple Silicon 和 Intel 机器构建。详见 [打包说明](../packaging/macos/README.md)。构建产物与源码 ZIP 是不同的下载项，不要把源码 ZIP 当成可双击安装的软件。
