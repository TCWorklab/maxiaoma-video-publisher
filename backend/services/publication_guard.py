"""Durable, explicit resumption of calls that have NEVER entered a publisher.

Once platform code is entered, any exception/false result/crash is ambiguous.
These calls cannot be resumed here. Exactly-once remote delivery is not claimed.
"""
import asyncio
import hashlib
import inspect
import json
import threading
import time
import uuid
from datetime import datetime, timedelta

from services import session_store as store


class PublishDeferred(RuntimeError):
    def __init__(self, job_id, message='任务已保留，请在账号管理的待处理任务中恢复'):
        self.job_id = job_id
        super().__init__(f'{message}（任务 {job_id}）')


def prepare(platform_id, operation, payload, details=()):
    # Store only ordinary publish arguments (file references, title, settings).
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(f'{platform_id}:{operation}:{raw}'.encode()).hexdigest()
    now = time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute("SELECT id,details FROM guarded_publications WHERE fingerprint=? AND state NOT IN ('success','cancelled') ORDER BY created LIMIT 1", (fingerprint,)).fetchone()
        if existing:
            merged = list(dict.fromkeys(json.loads(existing['details']) + list(details)))
            db.execute('UPDATE guarded_publications SET details=? WHERE id=?', (json.dumps(merged), existing['id']))
            return existing['id']
        job_id = str(uuid.uuid4())
        db.execute('INSERT INTO guarded_publications(id,fingerprint,platform,operation,payload,details,state,created,updated) VALUES (?,?,?,?,?,?,?,?,?)',
                   (job_id, fingerprint, platform_id, operation, raw, json.dumps(list(details)), 'pending', now, now))
    return job_id


def job(job_id):
    with store.connect() as db:
        row = db.execute('SELECT * FROM guarded_publications WHERE id=?', (job_id,)).fetchone()
    return dict(row) if row else None


def set_state(job_id, state, message=''):
    with store.connect() as db:
        db.execute('UPDATE guarded_publications SET state=?,message=?,updated=? WHERE id=?', (state, message, time.time(), job_id))
        record = db.execute('SELECT details FROM guarded_publications WHERE id=?', (job_id,)).fetchone()
        # Existing history remains linked to the SAME attempt during resumption.
        for detail in json.loads(record['details']) if record else []:
            status = {'waiting': 'waiting_auth', 'unknown': 'unknown', 'pending': 'queued', 'ready': 'queued'}.get(state, state)
            db.execute('UPDATE publish_details SET status=?,error_message=? WHERE id=?', (status, message, detail))
            if state == 'running':
                db.execute('UPDATE publish_details SET started_at=?,finished_at=NULL WHERE id=?', (datetime.now().isoformat(), detail))
            elif state in ('success', 'unknown', 'cancelled'):
                db.execute('UPDATE publish_details SET finished_at=? WHERE id=?', (datetime.now().isoformat(), detail))
            batch = db.execute('SELECT batch_id FROM publish_details WHERE id=?', (detail,)).fetchone()
            if batch:
                states = [r[0] for r in db.execute('SELECT status FROM publish_details WHERE batch_id=?', (batch[0],))]
                if 'unknown' in states:
                    batch_state = 'unknown'
                elif 'waiting_auth' in states:
                    batch_state = 'waiting_auth'
                elif any(s in ('running', 'queued', 'pending') for s in states):
                    batch_state = 'running'
                elif all(s == 'success' for s in states):
                    batch_state = 'success'
                else:
                    batch_state = 'partial' if 'success' in states else 'failed'
                db.execute('UPDATE publish_batches SET status=?,success_count=?,failed_count=? WHERE id=?',
                           (batch_state, states.count('success'), states.count('failed'), batch[0]))
                if batch_state in ('success', 'partial', 'failed') and not any(s in ('running', 'queued', 'pending', 'waiting_auth', 'unknown') for s in states):
                    link = db.execute('SELECT interval_minutes,next_batch_id FROM publish_batches WHERE id=?', (batch[0],)).fetchone()
                    if link and link['next_batch_id']:
                        due = datetime.now() + timedelta(minutes=max(0, float(link['interval_minutes'] or 0)))
                        db.execute("UPDATE publish_batches SET scheduled_at=?,updated_at=? WHERE id=? AND status='pending' AND scheduled_at=''",
                                   (due.isoformat(), datetime.now().isoformat(), link['next_batch_id']))


async def guarded_call(platform, operation, original, kwargs):
    from services.account_maintenance import check
    payload = dict(kwargs)
    job_id = payload.pop('_guard_id', None)
    details = payload.pop('_guard_details', [])
    job_id = job_id or prepare(platform.platform_id, operation, payload, details)
    entered = False
    owned = False
    try:
        async with store.leases(['publication-job:' + job_id]):
            owned = True
            record = job(job_id)
            if record['state'] == 'success':
                return True  # Same id retried after response loss: never submit twice.
            if record['state'] not in ('pending', 'ready'):
                set_state(job_id, record['state'], record['message'])
                raise PublishDeferred(job_id, record['message'] or '此任务需要人工处理，不会重复提交')
            if record['platform'] != platform.platform_id or record['operation'] != operation or json.loads(record['payload']) != payload:
                raise ValueError('恢复参数与已保存任务不一致')
            files = payload.get('account_file', [])
            if isinstance(files, str):
                files = [files]
            if not files:
                raise ValueError('未指定发布账号')
            # Shared across regular, batch, image, draft, and MCP entry points.
            async with store.leases([store.account_key(f) for f in files] + ['publication-browser']):
                for value in files:
                    account = store.account_for_file(value, platform.platform_id)
                    status = store.state_for(account['id'])
                    if status['state'] in store.HUMAN_STATES:
                        set_state(job_id, 'waiting', store.MESSAGES[status['state']])
                        raise PublishDeferred(job_id)
                    if status['state'] != 'valid' or time.time() - (status.get('last_success') or 0) > 300:
                        # Respect network backoff on repeated new requests as well.
                        if status['state'] == 'error' and (status.get('next_check') or 0) > time.time():
                            result = status
                        else:
                            result = await check(platform, value)
                        if result['state'] != 'valid':
                            set_state(job_id, 'waiting', store.MESSAGES[result['state']])
                            raise PublishDeferred(job_id)
                set_state(job_id, 'running')
                entered = True
                if inspect.iscoroutinefunction(original):
                    result = await original(platform, **payload)
                else:
                    # Keep the account lease until a synchronous publisher actually stops.
                    task = asyncio.create_task(asyncio.to_thread(original, platform, **payload))
                    try:
                        result = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        await task
                        raise
                if result is not True:
                    raise RuntimeError('平台未返回明确成功结果')
                set_state(job_id, 'success', '平台发布流程返回成功')
                return True
    except PublishDeferred:
        raise
    except BaseException as exc:
        # Do not downgrade an already-active duplicate call to "waiting".
        current = job(job_id)
        if entered:
            set_state(job_id, 'unknown', '已进入发布流程，结果不明确；请到平台核对，禁止自动重试')
        elif owned and current and current['state'] in ('pending', 'ready'):
            set_state(job_id, 'waiting', '账号忙或发布准备未完成；原任务已保留')
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise PublishDeferred(job_id, '请到账号管理查看保留任务或核对不明确结果') from None


def recover():
    """Only reclaim jobs whose OS lease is free, including a hard process stop."""
    with store.connect() as db:
        rows = db.execute("SELECT id,state FROM guarded_publications WHERE state IN ('running','ready','pending')").fetchall()
    for row in rows:
        lock = store.FileLease('publication-job:' + row['id'])
        if lock.acquire():
            try:
                if row['state'] == 'running':
                    set_state(row['id'], 'unknown', '服务中断时已进入发布流程，请先到平台核对；不会自动重试')
                else:
                    set_state(row['id'], 'waiting', '服务重启前尚未进入发布流程，可在确认账号后恢复')
            finally:
                lock.release()


def resume(job_id):
    """CAS makes rapid double-clicks harmless; no automatic login-triggered replay."""
    with store.connect() as db:
        changed = db.execute("UPDATE guarded_publications SET state='ready',updated=? WHERE id=? AND state='waiting'", (time.time(), job_id)).rowcount
    if not changed:
        return False
    def run():
        from impl.registry import get_platform
        record = job(job_id)
        payload = json.loads(record['payload'])
        payload['_guard_id'] = job_id
        fn = getattr(get_platform(record['platform']), record['operation'])
        try:
            if inspect.iscoroutinefunction(fn):
                asyncio.run(fn(**payload))
            else:
                fn(**payload)
        except (Exception, asyncio.CancelledError):
            pass  # State is already persisted by the guard.
    threading.Thread(target=run, daemon=True, name='publication-resume').start()
    return True
