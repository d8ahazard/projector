import json
import subprocess
from unittest.mock import Mock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.api import setup


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'JOBS', tmp_path / 'jobs')
    monkeypatch.setattr(setup, 'WORK_DIR', tmp_path)
    monkeypatch.setattr(setup, '_tasks', {})
    monkeypatch.setattr(setup, 'ensure_adb', lambda: None)
    monkeypatch.setattr(setup.time, 'sleep', lambda _: None)
    monkeypatch.setattr(setup, 'automatic_provisioning_available', lambda: True)
    return tmp_path


def job():
    return dict(id='abc123abc123', name='p3', ip='192.168.1.68', state='queued', completed=[], events=[])


@pytest.mark.parametrize('value', ['127.0.0.1', '8.8.8.8', '::1', '192.168.1.62', '192.168.1.2;whoami'])
def test_target_rejects_non_lan_and_controller(value):
    with pytest.raises(HTTPException): setup.validate_target(value, '192.168.1.62')


def test_non_tv_and_hardware_replacement_never_mutate(isolated, monkeypatch):
    phase = Mock(); monkeypatch.setattr(setup, 'phase', phase)
    def adb(*args, **kw):
        if args[-1] == 'ro.serialno': return 'PHONE123'
        if args[-1] == 'features': return 'feature:android.hardware.telephony\n'
        return ''
    monkeypatch.setattr(setup, 'adb', adb)
    j = job(); j['connection_port'] = 45678
    setup.execute(j)
    assert j['state'] == 'failed' and 'Phone provisioning is blocked' in j['message']
    phase.assert_not_called()
    j = job(); j.update(hardware_serial='TV123', connection_port=45678)
    setup.execute(j)
    assert 'different device' in j['message']
    phase.assert_not_called()


def test_pair_code_is_ephemeral_and_foreign_origin_blocked(isolated, monkeypatch):
    dispatch = Mock(); monkeypatch.setattr(setup, 'dispatch', dispatch)
    app = FastAPI(); app.include_router(setup.router)
    with TestClient(app) as client:
        data = dict(name='p3', ip='192.168.1.68', dedicated=True, pairing_code='123456', pairing_port=34567)
        assert client.post('/api/setup/jobs', json=data, headers={'Origin': 'http://elsewhere'}).status_code == 403
        r = client.post('/api/setup/jobs', json=data)
        assert r.status_code == 200
        saved = (setup.JOBS / (r.json()['id'] + '.json')).read_text()
        assert '123456' not in saved and 'pairing_code' not in saved
        assert dispatch.call_args.args[1:] == ('123456', 34567)


def test_wifi_pause_resume_reuses_completed_stages(isolated, monkeypatch):
    monkeypatch.setattr(setup, 'connect', lambda j, **kw: j.update(hardware_serial='TV123') or '192.168.1.68:34567')
    snap = isolated / 'devices/p3/original.json'; snap.parent.mkdir(parents=True); snap.write_text(json.dumps({'hardware_serial':'TV123'}))
    calls = []
    def phase(j, serial, name):
        calls.append(name)
        if name == 'Takeover' and calls.count(name) == 1: raise RuntimeError('Wi-Fi ownership is unverified')
        j['completed'].append(name)
    monkeypatch.setattr(setup, 'phase', phase)
    def adb(*args, **kw):
        if 'reboot' in args: raise subprocess.TimeoutExpired('adb', 15)
        return 'com.projector.app/.ApplianceHome'
    monkeypatch.setattr(setup, 'adb', adb)
    j = job(); setup.execute(j)
    assert j['state'] == 'needs_wifi_rejoin' and calls == ['Configure', 'Maintenance', 'Takeover']
    setup.execute(j)
    assert j['state'] == 'complete'
    assert calls == ['Configure', 'Maintenance', 'Takeover', 'Takeover', 'Debloat']
    assert 'Boot' in j['completed']


def test_snapshot_collision_and_restart_recovery(isolated, monkeypatch):
    monkeypatch.setattr(setup, 'connect', lambda j, **kw: j.update(hardware_serial='NEW') or '192.168.1.68:34567')
    snap = isolated / 'devices/p3/original.json'; snap.parent.mkdir(parents=True); snap.write_text(json.dumps({'hardware_serial':'OLD'}))
    phase = Mock(); monkeypatch.setattr(setup, 'phase', phase)
    j = job(); setup.execute(j)
    assert j['state'] == 'failed' and 'another device' in j['message']; phase.assert_not_called()
    j['state'] = 'running'; setup.save(j); setup.recover()
    assert setup.read(j['id'])['state'] == 'interrupted'
