#!/usr/bin/env bash
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"
if [[ ! -d .git ]]; then
    echo "ZIP 安装请下载新源码并保留自己的数据目录；更新说明见 docs/DEPLOYMENT.md。"
    exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "存在本地修改，请先提交或保存，再执行更新。"
    exit 1
fi
branch=$(git symbolic-ref --quiet --short HEAD)
git pull --ff-only origin "$branch"
echo "源码更新完成。请按部署说明更新依赖后重新启动。"
