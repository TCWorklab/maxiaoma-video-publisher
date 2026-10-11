"""Isolated simulated acceptance: no real accounts, browsers, or platform writes.

Run with: python -m unittest tests.test_account_maintenance -v
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from services import session_store as store
from services import session_probe as probe
from services import account_maintenance as maintenance
from services import publication_guard as guard


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'db').mkdir()
        self.patches = [patch.object(store, 'BASE_DIR', self.root), patch.object(maintenance, 'read_settings', return_value={})]
        for p in self.patches:
            p.start()
        with store.connect() as db:
            db.execute('CREATE TABLE user_info(id INTEGER PRIMARY KEY,type INTEGER,filePath TEXT,userName TEXT,status INTEGER)')
            db.executemany('INSERT INTO user_info VALUES (?,?,?,?,?)', [(1,3,'one.json','one',1),(2,3,'two.json','two',1)])
        self.original = {'cookies': [{'name': 'synthetic', 'value': 'old'}], 'origins': []}
        for f in ('one.json', 'two.json'):
            store.atomic_state(store.cookie_path(f), self.original)
        self.platform = type('FakePlatform', (), {'platform_id': 3})()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def run_async(self, awaitable):
        return asyncio.run(awaitable)

    def status(self, account_id):
        with store.connect() as db:
            return db.execute('SELECT status FROM user_info WHERE id=?',(account_id,)).fetchone()[0]

    def test_positive_evidence_required_on_all_three_platforms(self):
        for pid, url in probe.URLS.items():
            self.assertEqual(probe.classify(pid,url,{}),'error')
            self.assertEqual(probe.classify(pid,url,{'identity':True}),'error')
            self.assertEqual(probe.classify(pid,url,{'identity':True,'dashboard':True}),'valid')
            self.assertEqual(probe.classify(pid,url,{'login':True}),'expired')
            self.assertEqual(probe.classify(pid,url,{'identity':True,'dashboard':True,'challenge':True}),'verification_required')
            self.assertEqual(probe.classify(pid,'https://example.com/',{'identity':True,'dashboard':True}),'error')

    def test_network_failure_preserves_account_status_and_original_file(self):
        store.record_result(1, 'valid')
        success = store.state_for(1)['last_success']
        with patch.object(maintenance, 'probe', AsyncMock(return_value={'state':'error'})):
            self.run_async(maintenance.check(self.platform,'one.json'))
        self.assertEqual(self.status(1),1)
        self.assertEqual(store.state_for(1)['state'],'error')
        self.assertEqual(store.state_for(1)['last_success'],success)
        self.assertEqual(json.loads(store.cookie_path('one.json').read_text()),self.original)
        self.assertEqual(store.state_for(2)['state'],'unknown')

    def test_expiry_blocks_only_one_account_and_stops_background_probes(self):
        with patch.object(maintenance, 'probe', AsyncMock(return_value={'state':'expired'})) as fn:
            self.run_async(maintenance.check(self.platform,'one.json'))
            self.run_async(maintenance.check(self.platform,'one.json',force=False))
            self.assertEqual(fn.await_count,1)
        self.assertEqual(self.status(1),0)
        self.assertEqual(self.status(2),1)
        self.assertIsNone(store.state_for(1)['next_check'])

    def test_error_backoff_is_bounded_and_increases(self):
        first = store.record_result(1,'error')
        second = store.record_result(1,'error')
        self.assertGreater(second['next_check']-second['last_check'],first['next_check']-first['last_check'])
        for _ in range(12):
            last = store.record_result(1,'error')
        self.assertLessEqual(last['next_check']-last['last_check'],86400)

    def test_failed_replace_preserves_original_and_cleans_candidate(self):
        path = store.cookie_path('one.json')
        before = path.read_bytes()
        with patch.object(store.os,'replace',side_effect=PermissionError('synthetic')):
            with self.assertRaises(PermissionError):
                store.atomic_state(path,{'cookies':[{'name':'synthetic','value':'new'}],'origins':[]})
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(list(path.parent.glob('.session-*')),[])

    def test_empty_or_outside_session_rejected(self):
        with self.assertRaises(ValueError):
            store.atomic_state(store.cookie_path('one.json'), {'cookies':[],'origins':[]})
        with self.assertRaises(ValueError):
            store.cookie_path('../escape.json')

    def test_same_account_excludes_other_thread_but_other_account_works(self):
        async def exercise():
            key = store.account_key('one.json')
            async with store.leases([key]):
                result = []
                def contender():
                    async def take():
                        try:
                            async with store.leases([key]):
                                result.append('wrong')
                        except store.BusyError:
                            result.append('busy')
                        async with store.leases([store.account_key('two.json')]):
                            result.append('independent')
                    asyncio.run(take())
                t=threading.Thread(target=contender); t.start(); t.join()
                self.assertEqual(result,['busy','independent'])
        self.run_async(exercise())

    def test_os_lock_excludes_second_process_and_releases(self):
        lock=store.FileLease('synthetic-process-lock'); self.assertTrue(lock.acquire())
        code="from services.session_store import FileLease; x=FileLease('synthetic-process-lock'); print(x.acquire()); x.release()"
        env=dict(os.environ,SAU_DATA_DIR=str(self.root))
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'False')
        lock.release()
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'True')

    def test_probe_saves_only_valid_session(self):
        async def exercise(state):
            context=type('Context',(),{})()
            context.storage_state=AsyncMock(return_value={'cookies':[{'name':'synthetic','value':'new'}],'origins':[]})
            context.new_page=AsyncMock(return_value=type('Page',(),{'goto':AsyncMock(return_value=type('Response',(),{'status':200})())})())
            self.platform.create_browser=AsyncMock(return_value=object())
            self.platform.create_context=AsyncMock(return_value=context)
            self.platform.close_browser=AsyncMock()
            with patch.object(probe,'inspect_page',AsyncMock(return_value=(state,{}))):
                return await probe.probe(self.platform,'one.json')
        self.assertEqual(self.run_async(exercise('expired'))['state'],'expired')
        self.assertEqual(json.loads(store.cookie_path('one.json').read_text()),self.original)
        self.assertEqual(self.run_async(exercise('valid'))['state'],'valid')
        self.assertEqual(json.loads(store.cookie_path('one.json').read_text())['cookies'][0]['value'],'new')
        self.assertEqual(json.loads(store.cookie_path('two.json').read_text()),self.original)

    def test_publish_deferred_before_entering_platform_and_deduplicated(self):
        store.record_result(1,'expired')
        fn=AsyncMock(return_value=True)
        payload=dict(title='synthetic',account_file=['one.json'],files=['video.mp4'])
        ids=[]
        for _ in range(2):
            with self.assertRaises(guard.PublishDeferred) as caught:
                self.run_async(guard.guarded_call(self.platform,'publish_video',fn,payload))
            ids.append(caught.exception.job_id)
        self.assertEqual(ids[0],ids[1]); fn.assert_not_called()
        self.assertEqual(guard.job(ids[0])['state'],'waiting')

    def test_resume_claim_double_click_then_success_cannot_republish(self):
        payload=dict(title='synthetic',account_file=['one.json'])
        jid=guard.prepare(3,'publish_video',payload)
        guard.set_state(jid,'waiting')
        with patch.object(guard.threading.Thread,'start'):
            self.assertTrue(guard.resume(jid))
            self.assertFalse(guard.resume(jid))
        store.record_result(1,'valid')
        fn=AsyncMock(return_value=True)
        for _ in range(2):
            self.assertTrue(self.run_async(guard.guarded_call(self.platform,'publish_video',fn,{**payload,'_guard_id':jid})))
        self.assertEqual(fn.await_count,1)
        self.assertFalse(guard.resume(jid))

    def test_ambiguous_result_cannot_be_resumed_or_automatically_retried(self):
        store.record_result(1,'valid')
        payload=dict(title='synthetic',account_file=['one.json'])
        fn=AsyncMock(side_effect=TimeoutError('after possible submit'))
        with self.assertRaises(guard.PublishDeferred) as caught:
            self.run_async(guard.guarded_call(self.platform,'publish_image',fn,payload))
        jid=caught.exception.job_id
        self.assertEqual(guard.job(jid)['state'],'unknown')
        self.assertFalse(guard.resume(jid))
        with self.assertRaises(guard.PublishDeferred):
            self.run_async(guard.guarded_call(self.platform,'publish_image',fn,payload))
        self.assertEqual(fn.await_count,1)

    def test_restart_retains_waiting_and_marks_running_unknown(self):
        a=guard.prepare(3,'publish_video',dict(title='a',account_file=['one.json']))
        b=guard.prepare(3,'publish_video',dict(title='b',account_file=['two.json']))
        guard.set_state(b,'running')
        guard.recover(); guard.recover()
        self.assertEqual(guard.job(a)['state'],'waiting')
        self.assertEqual(guard.job(b)['state'],'unknown')

    def test_recovery_does_not_touch_job_active_in_another_process(self):
        jid=guard.prepare(3,'publish_video',dict(account_file=['one.json']))
        guard.set_state(jid,'running')
        lock=store.FileLease('publication-job:'+jid); self.assertTrue(lock.acquire())
        try:
            guard.recover()
            self.assertEqual(guard.job(jid)['state'],'running')
        finally:
            lock.release()

    def test_scheduler_stops_and_restarts_without_duplicate_thread(self):
        scheduler=maintenance.MaintenanceScheduler()
        scheduler.start(); original=scheduler.thread; scheduler.start()
        self.assertIs(scheduler.thread,original)
        self.assertTrue(scheduler.stop())
        scheduler.start(); self.assertIsNot(scheduler.thread,original)
        self.assertTrue(scheduler.stop())

    def test_all_managed_entry_points_are_guarded(self):
        from impl.registry import get_platform
        for pid in (1,2,3):
            platform=get_platform(pid)
            self.assertEqual(platform.check_cookie.__module__,maintenance.__name__)
            self.assertEqual(platform.sync_profile.__module__,maintenance.__name__)
            self.assertTrue(hasattr(platform.publish_video,'__wrapped__'))
            if pid in (1,3):
                self.assertTrue(hasattr(platform.publish_image,'__wrapped__'))

    def test_publish_holds_lease_against_login_sync_and_maintenance(self):
        from impl.registry import get_platform
        from queue import Queue
        platform = get_platform(3)
        started, release = threading.Event(), threading.Event()
        store.record_result(1, 'valid')
        async def publisher(_self, **_kwargs):
            started.set()
            await asyncio.to_thread(release.wait, 3)
            return True
        errors = []
        def publish():
            try:
                self.run_async(guard.guarded_call(platform, 'publish_video', publisher, {'account_file':['one.json']}))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=publish); thread.start()
        self.assertTrue(started.wait(2))
        try:
            with patch.object(maintenance, 'probe', AsyncMock()) as fn:
                self.assertEqual(self.run_async(maintenance.check(platform, 'one.json'))['state'], 'busy')
                with self.assertRaises(RuntimeError):
                    self.run_async(platform.sync_profile('one.json'))
                queue = Queue()
                self.run_async(platform.login('synthetic', queue, account_id=1))
                self.assertEqual(json.loads(queue.get_nowait())['status'], 'error')
                fn.assert_not_called()
        finally:
            release.set(); thread.join(4)
        self.assertEqual(errors, [])

    def test_scheduler_cancels_active_probe_before_releasing_lease(self):
        started = threading.Event()
        closed = threading.Event()
        async def slow(*args):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                closed.set()
        scheduler = maintenance.MaintenanceScheduler()
        with patch.object(maintenance, 'probe', slow), patch.object(maintenance, 'config', return_value={'enabled':True,'intervalMinutes':60}):
            scheduler.start()
            self.assertTrue(started.wait(2))
            self.assertTrue(scheduler.stop())
            self.assertTrue(closed.is_set())
        lock = store.FileLease(store.account_key('one.json'))
        self.assertTrue(lock.acquire()); lock.release()

    def test_invalid_import_does_not_replace_existing_account(self):
        from impl.registry import get_platform
        from queue import Queue
        platform = get_platform(3)
        path = store.cookie_path('one.json')
        before = path.read_bytes()
        # Base importer references its own BASE_DIR, so isolate it explicitly.
        with patch('impl.base_platform.BASE_DIR', self.root), patch.object(maintenance,'probe',AsyncMock(return_value={'state':'error'})):
            with self.assertRaises(RuntimeError):
                self.run_async(platform.import_cookie('synthetic=value', Queue(), account_id=1))
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(store.account_for_file('one.json')['id'],1)

    def test_snapshot_and_configuration_api_never_return_payload_or_cookie(self):
        from flask import Flask
        from blueprints.maintenance_bp import maintenance_bp
        app = Flask('isolated-maintenance'); app.register_blueprint(maintenance_bp)
        client = app.test_client()
        guard.prepare(3, 'publish_video', {'title':'synthetic','account_file':['one.json'],'files':['private-path.mp4']})
        result = client.get('/api/account-maintenance')
        self.assertEqual(result.status_code,200)
        body = result.get_data(as_text=True)
        self.assertNotIn('one.json',body); self.assertNotIn('private-path',body)
        self.assertNotIn('payload',result.json['data']['jobs'][0])
        self.assertEqual(client.post('/api/account-maintenance/config',json={'enabled':True,'intervalMinutes':1}).status_code,400)
        self.assertEqual(client.post('/api/account-maintenance/1',json={'enabled':False}).status_code,200)
        self.assertEqual(store.state_for(1)['enabled'],0)

    def test_duplicate_while_running_cannot_change_state_or_enter_twice(self):
        store.record_result(1, 'valid')
        payload = {'account_file':['one.json']}
        jid = guard.prepare(3,'publish_video',payload)
        store_lock = store.FileLease('publication-job:'+jid)
        self.assertTrue(store_lock.acquire())
        guard.set_state(jid,'running')
        fn = AsyncMock(return_value=True)
        try:
            with self.assertRaises(guard.PublishDeferred):
                self.run_async(guard.guarded_call(self.platform,'publish_video',fn,{**payload,'_guard_id':jid}))
            self.assertEqual(guard.job(jid)['state'],'running')
            fn.assert_not_called()
        finally:
            store_lock.release()

    def test_resumed_publication_updates_original_history_and_releases_chain(self):
        with store.connect() as db:
            db.execute('''CREATE TABLE publish_batches(id TEXT PRIMARY KEY,status TEXT,success_count INTEGER,
                failed_count INTEGER,interval_minutes REAL,next_batch_id TEXT,scheduled_at TEXT,updated_at TEXT)''')
            db.execute('''CREATE TABLE publish_details(id TEXT PRIMARY KEY,batch_id TEXT,status TEXT,
                error_message TEXT,started_at TEXT,finished_at TEXT)''')
            db.execute("INSERT INTO publish_batches VALUES ('b1','running',0,0,5,'b2','','')")
            db.execute("INSERT INTO publish_batches VALUES ('b2','pending',0,0,0,'','','')")
            db.execute("INSERT INTO publish_details VALUES ('d1','b1','queued','',NULL,NULL)")
        payload = {'account_file':['one.json']}
        jid = guard.prepare(3,'publish_video',payload,['d1'])
        store.record_result(1,'expired')
        fn=AsyncMock(return_value=True)
        with self.assertRaises(guard.PublishDeferred):
            self.run_async(guard.guarded_call(self.platform,'publish_video',fn,{**payload,'_guard_id':jid}))
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM publish_details WHERE id='d1'").fetchone()[0],'waiting_auth')
            self.assertEqual(db.execute("SELECT scheduled_at FROM publish_batches WHERE id='b2'").fetchone()[0],'')
        store.record_result(1,'valid')
        guard.set_state(jid,'ready')
        self.run_async(guard.guarded_call(self.platform,'publish_video',fn,{**payload,'_guard_id':jid}))
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publish_details').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT status FROM publish_details WHERE id='d1'").fetchone()[0],'success')
            self.assertTrue(db.execute("SELECT scheduled_at FROM publish_batches WHERE id='b2'").fetchone()[0])
        self.assertEqual(fn.await_count,1)

    def test_queue_cancelled_journal_is_not_revived_on_restart(self):
        from ext_api.task_queue import TaskQueue, PublishTask
        task=PublishTask(platform_type=3,payload={'account_file':['one.json'],'title':'fixture'})
        TaskQueue._prepare_managed_task(task)
        jid=task.payload['_guard_id']
        self.assertEqual(guard.job(jid)['state'],'pending')
        TaskQueue._cancel_managed_task(task)
        guard.recover()
        self.assertEqual(guard.job(jid)['state'],'cancelled')
        self.assertFalse(guard.resume(jid))


if __name__ == '__main__':
    unittest.main()
