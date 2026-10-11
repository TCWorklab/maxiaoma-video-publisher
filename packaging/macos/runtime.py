"""Bundled Python entry point; never reads the developer's data directory."""
import json
import os
from pathlib import Path
import platform
import runpy
import sys
import traceback


def status(phase, message):
    path = Path(os.environ['MAXIAOMA_STATUS_FILE'])
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'phase': phase, 'message': message}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def main():
    resources = Path(__file__).resolve().parent
    data = Path(os.environ['SAU_DATA_DIR']).resolve()
    if data == resources or resources in data.parents:
        raise RuntimeError('用户数据目录不能位于应用安装包内')
    data.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    (data / 'versions').write_bytes((resources / 'versions').read_bytes())
    # Give the launcher an owned process group to stop, including browser children.
    if sys.platform == 'darwin' and os.getpgrp() != os.getpid():
        os.setsid()
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    os.environ['SAU_DATA_DIR'] = str(data)
    os.environ['SAU_HOST'] = '127.0.0.1'
    os.environ['SAU_FRONTEND_DIR'] = str(resources / 'frontend')
    os.environ['CLOAKBROWSER_CACHE_DIR'] = str(data / 'browser' / platform.machine())
    os.environ['CLOAKBROWSER_AUTO_UPDATE'] = 'false'
    os.environ['CLOAKBROWSER_VERSION'] = '145.0.7632.109.2'
    os.environ.pop('CLOAKBROWSER_BINARY_PATH', None)
    os.environ.pop('CLOAKBROWSER_LICENSE_KEY', None)
    os.environ['PATH'] = str(resources / 'bin') + os.pathsep + '/usr/bin:/bin:/usr/sbin:/sbin'
    backend = resources / 'backend'
    sys.path.insert(0, str(backend))
    os.chdir(data)
    status('preparing', '正在准备发布组件；首次启动需要联网下载，请稍候…')
    from cloakbrowser import ensure_binary
    browser = ensure_binary()
    os.environ['CLOAKBROWSER_BINARY_PATH'] = browser
    status('starting', '正在启动马小马视频发布平台…')
    runpy.run_path(str(backend / 'app.py'), run_name='__main__')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        status('error', '启动失败。请确认网络可访问 GitHub，然后点击“重试”；详情可查看日志。')
        sys.exit(1)
