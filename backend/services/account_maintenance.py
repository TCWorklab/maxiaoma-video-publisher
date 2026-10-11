"""One scheduler and one guard for every entry into the three managed platforms."""
import asyncio
import atexit
import functools
import inspect
import threading
import time

from impl.settings import read_settings
from services import session_store as store
from services.session_probe import URLS, probe


def config():
    settings = read_settings()
    def minutes(key):
        try:
            return max(15, min(1440, int(settings.get(key, 60))))
        except (ValueError, TypeError):
            return 60
    return {
        'enabled': str(settings.get('accountMaintenanceEnabled', False)).lower() == 'true',
        'intervalMinutes': minutes('accountMaintenanceIntervalMinutes'),
        'concurrency': 1,
    }


async def check(platform, value, force=True):
    try:
        account = store.account_for_file(value, platform.platform_id)
    except ValueError:
        # Cookie import uses an isolated, as-yet-unregistered candidate file.
        account = None
    try:
        async with store.leases([store.account_key(value)]):
            if account and not force:
                old = store.state_for(account['id'])
                if old['state'] in store.HUMAN_STATES or (old.get('next_check') or 0) > time.time():
                    return old
            async with store.leases(['maintenance-browser'], timeout=20 if force else 0):
                result = await asyncio.wait_for(probe(platform, value), timeout=65)
            if account:
                result.update(store.record_result(account['id'], result['state'], config()['intervalMinutes']))
            return result
    except store.BusyError:
        return {'state': 'busy', 'message': store.MESSAGES['busy']}
    except Exception:
        return store.record_result(account['id'], 'error') if account else {'state': 'error'}


def guard_platform(cls):
    """Applied once at class definition: registry, direct class use, video and image APIs.

    Sync implementations retain their sync signature; their asyncio.run remains
    in a worker thread. No thread-owned RLock is held across async awaits.
    """
    if cls.platform_id not in store.SUPPORTED:
        return

    async def check_cookie(self, cookie_file):
        result = await check(self, cookie_file)
        return result['state'] == 'valid'

    async def sync_profile(self, cookie_file):
        result = await check(self, cookie_file)
        if result['state'] != 'valid':
            raise RuntimeError(store.MESSAGES[result['state']])
        return result

    async def open_creator_center(self, cookie_file):
        async with store.leases([store.account_key(cookie_file)]):
            browser = await self.create_browser(headless=False)
            try:
                context = await self.create_context(browser, storage_state=str(store.cookie_path(cookie_file)))
                page = await context.new_page()
                await page.goto(URLS[self.platform_id], timeout=30000)
                while not page.is_closed() and browser.is_connected():
                    await asyncio.sleep(1)
            finally:
                await self.close_browser(browser)

    cls.check_cookie = check_cookie
    cls.sync_profile = sync_profile
    cls.open_creator_center = open_creator_center

    original_login = cls.login

    @functools.wraps(original_login)
    async def login(self, id, status_queue, account_id=None):
        import json
        try:
            keys = ['interactive-login']
            if account_id:
                with store.connect() as db:
                    row = db.execute('SELECT type,filePath FROM user_info WHERE id=?', (account_id,)).fetchone()
                if not row or row['type'] != self.platform_id:
                    raise ValueError('重新登录的账号与平台不匹配')
                keys.append(store.account_key(row['filePath']))
            async with store.leases(keys):
                await asyncio.wait_for(original_login(self, id, status_queue, account_id=account_id), timeout=600)
        except (Exception, asyncio.CancelledError) as exc:
            message = str(exc) if isinstance(exc, (store.BusyError, ValueError)) else '登录未完成，请关闭登录窗口后重试（最长等待 10 分钟）'
            status_queue.put(json.dumps({'status': 'error', 'msg': message}))
    cls.login = login

    original_import = cls.import_cookie

    async def import_cookie(self, cookie_str, status_queue, account_id=None):
        keys = ['interactive-login']
        if account_id:
            with store.connect() as db:
                row = db.execute('SELECT type,filePath FROM user_info WHERE id=?', (account_id,)).fetchone()
            if not row or row['type'] != self.platform_id:
                raise ValueError('导入账号与平台不匹配')
            keys.append(store.account_key(row['filePath']))
        async with store.leases(keys):
            result = await original_import(self, cookie_str, status_queue, account_id=account_id)
            store.record_result(result['account_id'], 'valid', config()['intervalMinutes'])
            return result
    cls.import_cookie = import_cookie

    from services.publication_guard import guarded_call
    for method in ('publish_video', 'publish_image', 'publish_note'):
        original = cls.__dict__.get(method)
        if original is None:
            continue
        if inspect.iscoroutinefunction(original):
            def make_async(fn, operation):
                @functools.wraps(fn)
                async def wrapped(self, **kwargs):
                    return await guarded_call(self, operation, fn, kwargs)
                return wrapped
            setattr(cls, method, make_async(original, method))
        else:
            def make_sync(fn, operation):
                @functools.wraps(fn)
                def wrapped(self, **kwargs):
                    return asyncio.run(guarded_call(self, operation, fn, kwargs))
                return wrapped
            setattr(cls, method, make_sync(original, method))


class MaintenanceScheduler:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.startup = False

    def start(self, startup=False):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.startup = startup
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True, name='account-maintenance')
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=8)
        return not self.thread or not self.thread.is_alive()

    async def _check_until_stopped(self, platform, value, force):
        task = asyncio.create_task(check(platform, value, force=force))
        while not task.done():
            if self.stop_event.is_set() or (not force and not config()['enabled']):
                task.cancel()
                break
            await asyncio.sleep(0.25)
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _run(self):
        from impl.registry import get_platform
        while not self.stop_event.is_set():
            cfg = config()
            startup = self.startup
            self.startup = False
            if cfg['enabled'] or startup:
                leader = store.FileLease('maintenance-scheduler')
                if leader.acquire():
                    try:
                        with store.connect() as db:
                            rows = db.execute('SELECT id,type,filePath FROM user_info WHERE type IN (1,2,3) ORDER BY id').fetchall()
                        for row in rows:
                            if self.stop_event.is_set():
                                break
                            state = store.state_for(row['id'])
                            if not state['enabled'] or state['state'] in store.HUMAN_STATES:
                                continue
                            if not startup and (state.get('next_check') or 0) > time.time():
                                continue
                            if not startup and not config()['enabled']:
                                break
                            asyncio.run(self._check_until_stopped(get_platform(row['type']), row['filePath'], startup))
                            if self.stop_event.wait(3):
                                break
                    except Exception:
                        # Do not log exceptions containing platform URLs / credential material.
                        pass
                    finally:
                        leader.release()
            self.stop_event.wait(10)


scheduler = MaintenanceScheduler()
atexit.register(scheduler.stop)
