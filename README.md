# 马小马视频发布平台

[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Source checks](https://github.com/TCWorklab/maxiaoma-video-publisher/actions/workflows/source-checks.yml/badge.svg)](https://github.com/TCWorklab/maxiaoma-video-publisher/actions/workflows/source-checks.yml)

可在本机部署的多账号视频发布工具。基于 [千帆云递 / QianFan Sync](https://github.com/DevilJie/social-auto-upload-web-ui) 定制，保留原项目 MIT 许可及贡献者声明。

本仓库提供完整的前端、后端、MCP 服务、测试、部署说明和 macOS 双芯片打包脚本。下载后使用你自己的账号和素材，不包含任何已登录账号、Cookie、数据库或个人视频。

## 能做什么

- 管理多个创作者账号，为不同账号选择对应视频。
- 设置标题、文案、话题标签及封面，向抖音、小红书、微信视频号等平台发起发布。
- 管理视频/图文素材、草稿及发布队列。
- 对抖音、小红书、视频号提供登录状态检查、定时维护和人工重新登录入口。
- 使用定制品牌和精简界面；移除了侧栏的反馈、赞助、关于作者、更新日志与发布历史入口。

平台功能由浏览器自动化完成，平台改版可能需要更新适配。登录维护不能保证永久在线；扫码、验证码和风控验证需要本人完成。发布结果不明确时应先到平台核对，避免重复发布。详细说明见 [账号登录维护](docs/account-maintenance.md)。

## 下载与部署

**[下载完整源码 ZIP](https://github.com/TCWorklab/maxiaoma-video-publisher/archive/refs/heads/main.zip)**，解压后按 [部署说明](docs/DEPLOYMENT.md) 操作。也可以：

```sh
git clone https://github.com/TCWorklab/maxiaoma-video-publisher.git
cd maxiaoma-video-publisher
```

| 使用方式 | 准备工作 | 入口 |
| --- | --- | --- |
| Windows 源码部署 | Python 3.12+、Node.js 24、FFmpeg/FFprobe | [Windows 步骤](docs/DEPLOYMENT.md#windows) |
| macOS Apple Silicon / Intel 源码部署 | Python 3.12+、Node.js 24、FFmpeg/FFprobe | [macOS 步骤](docs/DEPLOYMENT.md#macos--linux) |
| Linux 桌面源码部署 | 同上，扫码登录需要图形桌面 | [Linux 步骤](docs/DEPLOYMENT.md#macos--linux) |
| macOS 15+ 安装包构建 | 本机对应架构的 Mac，或 GitHub Actions | [双芯片打包说明](packaging/macos/README.md) |

前端地址：`http://localhost:5173/`；后端健康检查：`http://127.0.0.1:5409/api/health`。
源码版首次部署需要下载依赖和浏览器组件。启动脚本只运行当前源码，不会强制更新或覆盖本地修改。

## 项目结构

```text
frontend/             Vue 3 + Vite + Element Plus 界面
backend/              Flask + SQLite + Playwright/CloakBrowser
backend/tests/        后端测试
backend-mcp/          可选 TypeScript MCP 服务
packaging/macos/      原生启动器及 arm64 / x64 安装包构建
docs/                 部署、接口和账号维护说明
.github/workflows/    源码检查与 macOS 构建
```

运行数据保存在本地 `data/`，或环境变量 `SAU_DATA_DIR` 指定的位置；请自行备份。不要将账号会话、数据库或素材提交到 GitHub。本项目是个人本机工具，默认绑定回环地址；直接面向公网的多人服务部署不在当前支持范围内。

## 验证与贡献

源码检查会构建前端，使用隔离数据执行账号维护测试，并验证新实例的健康接口和空账号初始化。测试不会登录或发布到真实社交账号。

已有 macOS 打包代码分别在 Apple Silicon 和 Intel 构建机完成过启动器、前端页面、素材上传下载、FFmpeg 和 DMG 完整性检查。这不代表所有平台真实发布已完成验收。默认 Mac 包为临时签名，尚未通过 Apple 公证；首次启动需要联网下载官方浏览器组件。

欢迎通过 [Issues](https://github.com/TCWorklab/maxiaoma-video-publisher/issues) 报告问题，附上系统、版本、复现步骤和已脱敏的错误信息。请勿上传 Cookie 或账号凭据。

## 来源与许可

代码沿用 [MIT License](LICENSE)，允许在遵守许可条件的前提下使用、修改和分发，分发时保留原版权和许可文本。

- 直接上游：[DevilJie/social-auto-upload-web-ui](https://github.com/DevilJie/social-auto-upload-web-ui)
- 自动发布基础项目：[dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload)
- 本定制版维护：[TCWorklab](https://github.com/TCWorklab)
- 第三方依赖说明：[THIRD-PARTY.md](packaging/macos/THIRD-PARTY.md)

平台标识仅用于识别接入平台。本项目与这些平台不存在官方隶属关系。上游说明归档在 `docs/readme/upstream-zh.md`，当前部署以本 README 和部署文档为准。
