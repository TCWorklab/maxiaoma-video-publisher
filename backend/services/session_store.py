"""Local account state. Never stores credentials in the database or logs."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from conf import BASE_DIR

SUPPORTED = (1, 2, 3)
HUMAN_STATES = ('expired', 'verification_required', 'missing')
MESSAGES = {
    'valid': '已确认登录有效',
    'expired': '已确认退出登录，请重新扫码',
    'verification_required': '平台要求人工验证，请打开登录窗口处理',
    'missing': '登录文件缺失，请重新扫码',
    'error': '网络、页面或会话保存异常，未认定登录失效；请稍后检测',
    'unknown': '尚未确认登录状态',
    'busy': '账号正在使用，请稍后重试',
}
_held = contextvars.ContextVar('account_leases', default=frozenset())


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect():
    db = sqlite3.connect(str(BASE_DIR / 'db' / 'database.db'), timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS account_maintenance (
        account_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT 'unknown',
        last_check REAL, last_success REAL, next_check REAL, failures INTEGER DEFAULT 0,
        message TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1)''')
    db.execute('''CREATE TABLE IF NOT EXISTS guarded_publications (
        id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, platform INTEGER NOT NULL,
        operation TEXT NOT NULL, payload TEXT NOT NULL, details TEXT NOT NULL DEFAULT '[]',
        state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
        updated REAL NOT NULL)''')
    db.commit()
    return db


def cookie_path(value):
    root = (BASE_DIR / 'cookiesFile').resolve()
    path = (root / str(value)).resolve()
    if path.parent != root or path.suffix != '.json':
        raise ValueError('无效的登录文件路径')
    return path


def account_for_file(value, platform_id=None):
    path = cookie_path(value)
    with connect() as db:
        rows = db.execute('SELECT id,type,filePath,status FROM user_info').fetchall()
    matches = [dict(r) for r in rows if cookie_path(r['filePath']) == path]
    if len(matches) != 1 or (platform_id is not None and matches[0]['type'] != platform_id):
        raise ValueError('登录文件必须属于当前平台的唯一账号')
    return matches[0]


def state_for(account_id):
    with connect() as db:
        row = db.execute('SELECT * FROM account_maintenance WHERE account_id=?', (account_id,)).fetchone()
    return dict(row) if row else dict(account_id=account_id, state='unknown', last_check=None,
                                    last_success=None, next_check=None, failures=0, enabled=1)


def record_result(account_id, state, interval=60):
    now = time.time()
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO account_maintenance(account_id) VALUES (?)', (account_id,))
        old = db.execute('SELECT * FROM account_maintenance WHERE account_id=?', (account_id,)).fetchone()
        failures = old['failures'] + 1 if state == 'error' else 0
        # No background retries for explicit logout / human challenges.
        delay = min(24 * 3600, 300 * 2 ** min(failures - 1, 8)) if failures else interval * 60
        next_check = None if state in HUMAN_STATES else now + delay
        db.execute('''UPDATE account_maintenance SET state=?,last_check=?,last_success=?,
                      next_check=?,failures=?,message=? WHERE account_id=?''',
                   (state, now, now if state == 'valid' else old['last_success'], next_check,
                    failures, MESSAGES[state], account_id))
        if state == 'valid' or state in HUMAN_STATES:
            db.execute('UPDATE user_info SET status=? WHERE id=?', (int(state == 'valid'), account_id))
    return state_for(account_id)


def atomic_state(path, state):
    """Same-directory fsync + replace. A failed write leaves the previous file intact."""
    path = cookie_path(path)
    if not isinstance(state, dict) or not isinstance(state.get('cookies'), list) or not isinstance(state.get('origins'), list):
        raise ValueError('会话格式不完整')
    if not state['cookies'] and not state['origins']:
        raise ValueError('拒绝保存空会话')
    raw = json.dumps(state, ensure_ascii=False).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.session-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class BusyError(RuntimeError):
    pass


class FileLease:
    """OS releases advisory locks on process death (Windows and POSIX)."""
    def __init__(self, key):
        root = BASE_DIR / '.account-locks'
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / (hashlib.sha256(key.encode()).hexdigest() + '.lock')
        self.stream = None

    def acquire(self):
        stream = open(self.path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if stream.seek(0, 2) == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def release(self):
        if self.stream:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()
            self.stream = None


@asynccontextmanager
async def leases(keys, timeout=0):
    keys = sorted(set(str(k) for k in keys) - _held.get())
    acquired = []
    deadline = time.monotonic() + timeout
    token = None
    try:
        for key in keys:
            lock = FileLease(key)
            while not lock.acquire():
                if time.monotonic() >= deadline:
                    raise BusyError(MESSAGES['busy'])
                await asyncio.sleep(0.2)
            acquired.append(lock)
        token = _held.set(_held.get() | set(keys))
        yield
    finally:
        if token is not None:
            _held.reset(token)
        for lock in reversed(acquired):
            lock.release()


def account_key(value):
    return 'account:' + os.path.normcase(str(cookie_path(value)))
