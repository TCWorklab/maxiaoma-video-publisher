"""Run against the actual macOS bundle, with temporary, empty user data."""
import argparse
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import tempfile
import time

import requests


def stop(process):
    if process.poll() is None:
        if os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)


def verify(app, report):
    resources = app / 'Contents/Resources'
    checks = []
    assert not (resources / 'data').exists(), 'Personal data directory in bundle'
    assert not list((resources / 'backend').rglob('*.db')), 'Database in bundled backend'
    checks.append('clean-distribution')
    with tempfile.TemporaryDirectory(prefix='maxiaoma-acceptance-') as temporary:
        root = Path(temporary)
        data = root / 'fresh-user'
        data.mkdir()
        sample = root / 'sample.mp4'
        subprocess.run([str(resources / 'bin/ffmpeg'), '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=10', '-t', '1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(sample)], check=True)
        probe = subprocess.check_output([str(resources / 'bin/ffprobe'), '-v', 'error',
                                         '-show_format', '-show_streams', '-of', 'json', str(sample)])
        assert float(json.loads(probe)['format']['duration']) >= 0.9
        checks.append('ffmpeg-encode-and-ffprobe')
        env = dict(os.environ, SAU_DATA_DIR=str(data), SAU_PORT='5409',
                   SAU_READY_FILE=str(root / 'ready.json'), MAXIAOMA_STATUS_FILE=str(root / 'status.json'),
                   PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        with (root / 'backend.log').open('wb') as log:
            process = subprocess.Popen([str(resources / 'python/bin/python3'), '-u', '-B',
                                        str(resources / 'runtime.py')], env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 600
                session = requests.Session()
                session.trust_env = False
                url = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError((root / 'backend.log').read_text(errors='replace')[-7000:])
                    try:
                        ready = json.loads((root / 'ready.json').read_text())
                        assert ready['pid'] == process.pid
                        candidate = f"http://127.0.0.1:{ready['port']}"
                        health = session.get(candidate + '/api/health', timeout=2).json()
                        if health.get('db_ok') and health.get('db_user_count') == 0:
                            url = candidate
                            break
                    except (OSError, ValueError, requests.RequestException):
                        pass
                    time.sleep(0.5)
                assert url, 'Bundled backend did not become healthy'
                checks.append('bundled-python-fresh-database-startup')
                assert session.get(url + '/brand/maxiaoma-cutout.png', timeout=5).status_code == 200
                with sample.open('rb') as stream:
                    upload = session.post(url + '/api/materials/upload', files={'file': ('sample.mp4', stream, 'video/mp4')}, timeout=30).json()
                assert upload['code'] == 200, upload
                material_url = upload['data']['url']
                if material_url.startswith('/'):
                    material_url = url + material_url
                assert session.get(material_url, timeout=10).content == sample.read_bytes()
                checks.append('isolated-material-upload-and-download')
                os.environ.update(CLOAKBROWSER_CACHE_DIR=str(data / 'browser' / platform.machine()),
                                  CLOAKBROWSER_AUTO_UPDATE='false', CLOAKBROWSER_VERSION='145.0.7632.109.2')
                from cloakbrowser import launch
                browser = launch(headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(url, wait_until='domcontentloaded')
                    page.get_by_text('最近素材', exact=True).wait_for(timeout=30000)
                    assert '马小马视频发布平台' in page.title()
                    page.goto(url + '/#/settings', wait_until='domcontentloaded')
                    page.get_by_role('button', name='保存设置', exact=True).wait_for(timeout=30000)
                    assert '反馈系统' not in page.locator('.settings-page').inner_text()
                    page.screenshot(path=str(report.with_suffix('.png')), full_page=True)
                    assert not errors, errors
                finally:
                    browser.close()
                checks.append('real-browser-dashboard-settings-and-brand')
                with sqlite3.connect(data / 'db/database.db') as db:
                    assert db.execute('SELECT COUNT(*) FROM user_info').fetchone()[0] == 0
                    assert db.execute('SELECT COUNT(*) FROM publish_details').fetchone()[0] == 0
                checks.append('no-real-account-or-publication')
            finally:
                stop(process)
        # Exercise the compiled AppKit launcher with the already downloaded browser.
        result = root / 'launcher-result.txt'
        native_env = dict(os.environ, MAXIAOMA_SMOKE_DATA_DIR=str(data), MAXIAOMA_SMOKE_RESULT=str(result))
        print('Checks before native launcher: ' + ', '.join(checks), flush=True)
        with (root / 'native-launcher.log').open('wb') as native_log:
            launcher = subprocess.Popen([str(app / 'Contents/MacOS/MaXiaoMa'), '--smoke-test'],
                                        env=native_env, stdout=native_log, stderr=native_log)
            try:
                assert launcher.wait(timeout=120) == 0
                assert result.read_text() == 'launcher-ready'
            except Exception:
                for diagnostic in [root / 'native-launcher.log', data / 'logs/launcher.log',
                                   *sorted((data / 'run').glob('*.json'))]:
                    if diagnostic.is_file():
                        print(f'--- {diagnostic.name} ---\n{diagnostic.read_text(errors="replace")[-6000:]}', flush=True)
                for ready_path in (data / 'run').glob('*-ready.json'):
                    try:
                        ready = json.loads(ready_path.read_text())
                        print('Independent health: ' + session.get(
                            f'http://127.0.0.1:{ready["port"]}/api/health', timeout=3).text, flush=True)
                    except Exception as error:
                        print(f'Independent health failed: {error}', flush=True)
                raise
            finally:
                stop(launcher)
        checks.append('compiled-native-launcher-startup-and-shutdown')
    report.write_text(json.dumps({'passed': True, 'architecture': platform.machine(),
                                 'checks': checks, 'real_platform_publish_tested': False,
                                 'gatekeeper_on_recipient_tested': False}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    verify(args.app.resolve(), args.report.resolve())
