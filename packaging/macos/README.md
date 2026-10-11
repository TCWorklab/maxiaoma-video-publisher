# 马小马视频发布平台：macOS 双架构构建

## 当前状态与输出

这是构建流程源码，不能作为已完成的 Mac 安装包发给最终用户。
必须在 Apple Silicon 和 Intel macOS 构建机分别成功执行，才能生成：

- `MaXiaoMa-1.2.8.1-macOS-arm64.dmg`：Apple 芯片 M 系列。
- `MaXiaoMa-1.2.8.1-macOS-x64.dmg`：Intel 芯片。

同时输出带 `.app` 的 ZIP、SHA-256、隔离启动验收 JSON 和页面截图。
两个版本都要求 macOS 15+。安装后拖入“应用程序”，双击启动；自动启动
本机服务并在默认浏览器打开平台。无需用户安装 Python、Node、Git 或 Homebrew。
更新应用不会覆盖 `~/Library/Application Support/MaXiaoMaPublisher/` 的用户数据。

## 首次联网与签名

Python、Python 依赖、FFmpeg/FFprobe、编译后的前端和原生启动器随应用分发。
CloakBrowser 本体不随安装包分发：官方二进制许可限制再次分发，允许最终用户
从官方渠道下载依赖。因此首次启动会自动获取官方原版浏览器；需能连接其下载站点。
构建机也会下载浏览器到临时验收目录，但不会将它复制进应用或安装包。

默认是临时签名版本，并未通过 Apple 公证；首次打开可能需要在“隐私与安全性”
中对本应用选择“仍要打开”。不要关闭 Gatekeeper 或全局降低系统保护。
如要求收件人首次双击无开发者拦截，需要可用的 Developer ID Application
证书和 Apple 公证凭据。证书、密码、API 密钥不可写入仓库或源码包。

## GitHub 云端构建

1. Fork 本公开仓库到自己的 GitHub 账号，并启用 Actions。
   不要上传工作目录的 data、cookies、视频、数据库、.local 或签名证书。
2. 在 Actions 运行 `Build MaXiaoMa macOS installers`。
3. 流程在 `macos-15`（arm64）和 `macos-15-intel`（x64）分别构建和验收。
4. 两个任务通过后，在 Artifacts 下载对应芯片的安装包。

默认流程只手动触发，将该次构建产物上传到 Actions Artifacts（保留 14 天），不创建公开 Release。
GitHub 的 macOS runner 会使用账号 Actions 配额；超出配额的费用由账号策略决定。

## 本机构建

构建机准备 Node 24、uv、Xcode Command Line Tools。在对应架构的 Mac 上运行：

```sh
python3 packaging/macos/build.py --arch arm64
# Intel Mac：
python3 packaging/macos/build.py --arch x64
```

两个架构都使用本机原生运行环境，不将 Intel 安装包重命名为 Apple 芯片版。
脚本拒绝在 Windows 或架构不匹配的主机上生成“安装包”。默认产物位于 dist/macos。
若需重建，请指定新的 `--output` 目录，避免覆盖已有验收产物。

已有签名证书和 notarytool keychain profile 时：

```sh
python3 packaging/macos/build.py --arch arm64 \
  --identity 'Developer ID Application: YOUR NAME (TEAMID)' \
  --notary-profile 'YOUR_SAVED_PROFILE'
```

脚本逐个签署内嵌 Mach-O 文件，再签署应用，完成运行验收后提交 Apple 公证、
附加公证票据并生成 DMG。公证失败时不会报告已公证成功。
云端签名需要另行配置私密的临时 keychain，当前默认 workflow 不索取或上传证书。

## 自动验收范围

- 每个 Mach-O 必须包含目标芯片架构。
- 完整 Python 在临时新数据目录启动后端、建库，初始账号数必须为零。
- FFmpeg 编码、FFprobe 读取时长，以及隔离素材上传/下载。
- 实际浏览器加载仪表盘、设置页与品牌图片，无 JavaScript 运行错误。
- 原生启动器启动和关闭服务，且不对外执行视频发布。
- 应用代码签名验证、DMG 完整性检查、产物 SHA-256。

仍需收件 Mac 验证 Gatekeeper、首次组件下载、扫码登录和三个平台的真实发布。
云端本地服务验收通过不代表三个平台发布全部通过。

## 来源

- GitHub runner 架构：https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- Apple 签名与公证：https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- CloakBrowser 许可：https://github.com/CloakHQ/CloakBrowser/blob/main/BINARY-LICENSE.md
- uv 便携 Python：https://docs.astral.sh/uv/guides/install-python/
