"""Local UI endpoints: only metadata is returned, never stored session contents."""
import json

from flask import Blueprint, jsonify, request

from impl.settings import write_setting
from services import session_store as store
from services.account_maintenance import config, scheduler
from services.publication_guard import resume, set_state

maintenance_bp = Blueprint('maintenance', __name__)


@maintenance_bp.get('/api/account-maintenance')
def snapshot():
    with store.connect() as db:
        accounts = db.execute('SELECT id,type,userName FROM user_info WHERE type IN (1,2,3) ORDER BY id').fetchall()
        names = {store.cookie_path(r['filePath']): r['userName'] for r in db.execute('SELECT filePath,userName FROM user_info WHERE type IN (1,2,3)')}
        jobs = db.execute("SELECT id,platform,operation,payload,state,message,created,updated FROM guarded_publications WHERE state NOT IN ('success','cancelled') ORDER BY created DESC LIMIT 100").fetchall()
    def job_metadata(row):
        payload = json.loads(row['payload'])
        values = payload.get('account_file', [])
        if isinstance(values, str):
            values = [values]
        labels = []
        for value in values:
            try:
                labels.append(names.get(store.cookie_path(value), '账号已移除'))
            except ValueError:
                labels.append('账号引用异常')
        return {k: row[k] for k in ('id','platform','operation','state','message','created','updated')} | {
            'title': payload.get('title', ''), 'accountNames': '、'.join(labels),
        }
    return jsonify(code=200, data={
        'config': config(),
        'accounts': [{**dict(a), **store.state_for(a['id'])} for a in accounts],
        'jobs': [job_metadata(r) for r in jobs],
        'running': bool(scheduler.thread and scheduler.thread.is_alive()),
    })


@maintenance_bp.post('/api/account-maintenance/config')
def configure():
    data = request.get_json() or {}
    if type(data.get('enabled')) is not bool or type(data.get('intervalMinutes')) is not int or not 15 <= data['intervalMinutes'] <= 1440:
        return jsonify(code=400, msg='间隔须为 15–1440 分钟，启停须为布尔值'), 400
    write_setting('accountMaintenanceEnabled', str(data['enabled']).lower())
    write_setting('accountMaintenanceIntervalMinutes', data['intervalMinutes'])
    # Changing frequency must not cause a second immediate visit or erase backoff.
    with store.connect() as db:
        db.execute("UPDATE account_maintenance SET next_check=last_check+? WHERE state='valid'", (data['intervalMinutes'] * 60,))
    if data['enabled']:
        scheduler.start()
    return jsonify(code=200, data=config(), msg='设置已保存；当前只读检查将安全结束')


@maintenance_bp.post('/api/account-maintenance/<int:account_id>')
def per_account(account_id):
    data = request.get_json() or {}
    if type(data.get('enabled')) is not bool:
        return jsonify(code=400, msg='缺少账号启停设置'), 400
    with store.connect() as db:
        row = db.execute('SELECT type FROM user_info WHERE id=?', (account_id,)).fetchone()
        if not row or row['type'] not in store.SUPPORTED:
            return jsonify(code=404), 404
        db.execute('INSERT OR IGNORE INTO account_maintenance(account_id) VALUES (?)', (account_id,))
        db.execute('UPDATE account_maintenance SET enabled=? WHERE account_id=?', (int(data['enabled']), account_id))
    return jsonify(code=200)


@maintenance_bp.post('/api/account-maintenance/jobs/<job_id>/resume')
def resume_job(job_id):
    accepted = resume(job_id)
    return jsonify(code=200 if accepted else 409, msg='恢复检查已排队' if accepted else '任务已在处理或结果不明确，不能再次提交'), 200 if accepted else 409


@maintenance_bp.post('/api/account-maintenance/jobs/<job_id>/cancel')
def cancel_job(job_id):
    # Only calls that never entered a publisher can be discarded safely.
    with store.connect() as db:
        changed = db.execute("UPDATE guarded_publications SET state='cancelled',message='用户取消尚未提交的任务' WHERE id=? AND state='waiting'", (job_id,)).rowcount
    if changed:
        set_state(job_id, 'cancelled', '用户取消尚未提交的任务')
    return jsonify(code=200 if changed else 409), 200 if changed else 409


@maintenance_bp.post('/api/account-maintenance/stop')
def stop():
    stopped = scheduler.stop()
    return jsonify(code=200, data={'stopped': stopped})
