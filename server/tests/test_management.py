from contextlib import contextmanager
import hashlib
import json
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine
from app.api import devices, system
from app.models import Device, MediaAsset, Setting, Surface
from app import releases
from app.sync import runner as runner_module
from app.sync import ws as ws_module


@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session: yield session
    engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    app=FastAPI(); app.include_router(devices.router); app.include_router(system.router); app.include_router(system.download_router)
    app.dependency_overrides[devices.get_session]=lambda:db
    monkeypatch.setattr(devices.hub,'online',lambda _:True)
    monkeypatch.setattr(devices.hub,'send_player',AsyncMock(return_value=True))
    monkeypatch.setattr(devices.hub,'mark_devices_dirty',lambda:None)
    monkeypatch.setattr(devices.hub,'status',{})
    db.add(Device(id='stick',capabilities={'management_protocol':1,'version_code':2}));db.commit()
    with TestClient(app) as c: yield c


def test_restart_is_delivered_without_arbitrary_commands(client,db):
    response=client.post('/api/devices/stick/command',json={'action':'restart'})
    assert response.status_code==200
    packet=devices.hub.send_player.call_args.args[1]
    assert packet=={'type':'command','id':response.json()['id'],'action':'restart'}
    assert db.get(Device,'stick').status['command']['state']=='sent'
    assert client.post('/api/devices/stick/command',json={'action':'shell'}).status_code==400
    assert client.post('/api/devices/stick/command',json={'action':'restart'},headers={'Origin':'https://unrelated.test'}).status_code==403


def test_offline_and_old_players_do_not_get_commands(client,db,monkeypatch):
    monkeypatch.setattr(devices.hub,'online',lambda _:False)
    assert client.post('/api/devices/stick/command',json={'action':'restart'}).status_code==409
    monkeypatch.setattr(devices.hub,'online',lambda _:True)
    d=db.get(Device,'stick');d.capabilities={};db.add(d);db.commit()
    assert client.post('/api/devices/stick/command',json={'action':'restart'}).status_code==409
    devices.hub.send_player.assert_not_called()


def test_maintenance_requires_explicit_device_capability(client,db):
    assert client.post('/api/devices/stick/command',json={'action':'maintenance_adb'}).status_code == 409
    d=db.get(Device,'stick'); d.capabilities={**d.capabilities,'maintenance_adb':True}; db.add(d); db.commit()
    assert client.post('/api/devices/stick/command',json={'action':'maintenance_adb'}).status_code == 200
    assert devices.hub.send_player.call_args.args[1]['action'] == 'maintenance_adb'


def test_player_menu_requires_supported_client(client,db):
    assert client.post('/api/devices/stick/command',json={'action':'player_settings'}).status_code == 409
    d=db.get(Device,'stick'); d.capabilities={**d.capabilities,'tv_menu':True}; db.add(d); db.commit()
    assert client.post('/api/devices/stick/command',json={'action':'player_settings'}).status_code == 200
    assert devices.hub.send_player.call_args.args[1]['action'] == 'player_settings'


@pytest.fixture
def release(tmp_path,monkeypatch):
    apk=tmp_path/'projector.apk';apk.write_bytes(b'signed-apk-fixture')
    data={'package':'com.projector.app','version_code':3,'version_name':'0.2.0','size':apk.stat().st_size,'sha256':hashlib.sha256(apk.read_bytes()).hexdigest()}
    (tmp_path/'projector-release.json').write_text(json.dumps(data))
    monkeypatch.setattr(releases,'APK_DIR',tmp_path);monkeypatch.setattr(system,'APK_DIR',tmp_path)
    return data,apk


def test_update_requires_new_matching_release(client,db,release):
    data,apk=release
    assert client.get('/api/app-release').json()['available'] is True
    assert client.post('/api/devices/stick/command',json={'action':'update'}).status_code==200
    packet=devices.hub.send_player.call_args.args[1]
    assert packet['release']['sha256']==data['sha256']
    assert client.get(packet['release']['url']).content==apk.read_bytes()
    d=db.get(Device,'stick');d.capabilities={**d.capabilities,'version_code':3};db.add(d);db.commit()
    assert client.post('/api/devices/stick/command',json={'action':'update'}).status_code==409
    apk.write_bytes(b'changed package')
    assert client.get('/api/app-release').json()['available'] is False
    assert client.get(packet['release']['url']).status_code==409


@pytest.mark.asyncio
async def test_manual_show_survives_server_restart_but_stopped_or_scheduled_does_not(db,monkeypatch):
    @contextmanager
    def scope(): yield db
    monkeypatch.setattr(runner_module,'session_scope',scope)
    r=runner_module.Runner();r.program={'id':'show'};r.remember(True)
    restored=runner_module.Runner();restored.start=AsyncMock();await restored.restore()
    restored.start.assert_awaited_once_with('show')
    r.remember(False);restored.start.reset_mock();await restored.restore();restored.start.assert_not_awaited()
    r.by_schedule='schedule';r.remember(True)
    assert db.get(Setting,'resume_program').value=={}


def test_marathon_runtime_seeds_are_persisted_and_never_repeat(db, monkeypatch):
    @contextmanager
    def scope(): yield db
    monkeypatch.setattr(runner_module, 'session_scope', scope)
    draws = iter([11, 11, 22, 33])
    monkeypatch.setattr(runner_module.secrets, 'randbits', lambda _bits: next(draws))
    r = runner_module.Runner()
    assert r._reserve_runtime_seed('haunt') == 11
    assert r._reserve_runtime_seed('haunt') == 22
    assert r._reserve_runtime_seed('another-haunt') == 33


@pytest.mark.asyncio
async def test_targeted_preview_caches_schedules_and_returns_idle_player_to_black(db, monkeypatch):
    @contextmanager
    def scope(): yield db
    db.add(Surface(id='window', name='Window', width=48, height=72))
    db.add(Device(id='stick', name='P1', surface_id='window'))
    db.add(MediaAsset(id='ghost', title='Ghost', status='ready', duration_ms=5000,
                      width=720, height=1280, sha256='abc', size=123,
                      rendition_path='renditions/ghost.mp4'))
    db.commit()
    r = runner_module.Runner()
    sent = []
    async def send_player(device_id, message):
        sent.append((device_id, message))
        if message['type'] == 'media_sync':
            await r.on_media_status(device_id, {'ready': True, 'progress': 1})
        return True
    async def send_players(message, device_ids=None):
        for device_id in device_ids or []:
            await send_player(device_id, message)
    monkeypatch.setattr(runner_module, 'session_scope', scope)
    monkeypatch.setattr(runner_module.hub, 'online', lambda device_id: device_id == 'stick')
    monkeypatch.setattr(runner_module.hub, 'send_player', send_player)
    monkeypatch.setattr(runner_module.hub, 'send_players', send_players)
    result = await r.preview({'type':'cue','id':'test','clips':['ghost'],'target':{'mode':'all'},'pattern':'together'}, ['stick'])
    assert result['assets'] == ['ghost']
    assert [message['type'] for _, message in sent] == ['media_sync', 'clear', 'schedule']
    event = sent[-1][1]['events'][0]
    assert event['asset_id'] == 'ghost' and event['device_id'] == 'stick'
    assert event['id'].startswith('preview:') and event['end'] > event['t']


def test_linked_player_receives_command_and_reports_result(db,monkeypatch):
    @contextmanager
    def scope(): yield db
    monkeypatch.setattr(ws_module,'session_scope',scope)
    monkeypatch.setattr(ws_module.runner,'on_player_connected',AsyncMock())
    monkeypatch.setattr(devices.hub,'players',{})
    monkeypatch.setattr(devices.hub,'status',{})
    monkeypatch.setattr(devices.hub,'_locks',{})
    monkeypatch.setattr(devices.hub,'mark_devices_dirty',lambda:None)
    app=FastAPI();app.include_router(ws_module.router);app.include_router(devices.router)
    app.dependency_overrides[devices.get_session]=lambda:db
    with TestClient(app) as c:
        with c.websocket_connect('/ws/player') as socket:
            socket.send_json({'type':'hello','device_id':'linked','app_version':'0.2.0','capabilities':{'management_protocol':1,'version_code':3}})
            assert socket.receive_json()['type']=='welcome'
            response=c.post('/api/devices/linked/command',json={'action':'restart'})
            assert response.status_code==200
            command=socket.receive_json()
            assert command['action']=='restart'
            socket.send_json({'type':'command_result','id':command['id'],'state':'restarting','message':'Restarting player'})
            socket.send_json({'type':'ping','t0':1})
            assert socket.receive_json()['type']=='pong'
            assert db.get(Device,'linked').status['command']['state']=='restarting'
            appliance = {'appliance_mode': True, 'default_home': True, 'playback_state': 'standby', 'loaded_decoders': 0, 'continuous_rendering': False}
            socket.send_json({'type':'heartbeat','management':appliance})
            socket.send_json({'type':'ping','t0':2})
            assert socket.receive_json()['type']=='pong'
            assert devices.hub.status['linked']['management'] == appliance
        assert not devices.hub.online('linked')
