"""Verify a new isolated backend starts without any personal account data."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import build_opener, ProxyHandler

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='maxiaoma-fresh-') as directory:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, SAU_HOST='127.0.0.1', SAU_PORT=str(port),
               SAU_DATA_DIR=directory, PYTHONUTF8='1', BROWSER='none')
    opener = build_opener(ProxyHandler({}))
    with (Path(directory) / 'startup.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, str(root / 'backend/app.py')],
                                   cwd=root / 'backend', env=env, stdout=log, stderr=log)
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError('Backend stopped before becoming ready')
                try:
                    with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
                        health = json.load(response)
                    with opener.open(f'http://127.0.0.1:{port}/getAccounts', timeout=2) as response:
                        accounts = json.load(response)
                    assert health.get('db_ok') is True, health
                    assert health.get('db_user_count') == 0, health
                    assert accounts.get('data') == [], accounts
                    print('PASS: fresh database ready, zero accounts, account API available')
                    break
                except (OSError, ValueError):
                    time.sleep(1)
            else:
                raise TimeoutError('Backend did not become ready in time')
        except Exception:
            log.flush()
            print((Path(directory) / 'startup.log').read_text(encoding='utf-8'))
            raise
        finally:
            if os.name == 'nt':
                # Windows venv launchers spawn a child Python; close this test tree only.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
