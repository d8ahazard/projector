"""Restricted Windows appliance provisioning. No arbitrary shell or phone management API."""
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import WORK_DIR, APK_DIR, public_url
from ..models import Device, new_id
from ..db import session_scope
from ..sync.hub import hub

router = APIRouter(prefix='/api/setup', tags=['setup'])
ROOT = Path(__file__).resolve().parents[3]
JOBS = WORK_DIR / 'provisioning'
ADB = Path('D:/DevTools/Android/sdk/platform-tools/adb.exe')
SCRIPT = ROOT / 'scripts/Provision-Player.ps1'
ADB_PORT = 5038  # Dedicated service identity, independent of interactive developer ADB.
_tasks: dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()

class SetupIn(BaseModel):
    name: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,31}$')
    ip: str
    pairing_port: int | None = Field(default=None, ge=1024, le=65535)
    pairing_code: str = Field(default='', pattern=r'^([0-9]{6})?$')
    connection_port: int | None = Field(default=None, ge=1024, le=65535)
    dedicated: bool = False

class ResumeIn(BaseModel):
    connection_port: int | None = Field(default=None, ge=1024, le=65535)
    pairing_port: int | None = Field(default=None, ge=1024, le=65535)
    pairing_code: str = Field(default='', pattern=r'^([0-9]{6})?$')


def validate_target(value: str, controller_ip: str = '') -> str:
    try:
        addr = ipaddress.ip_address(value)
        allowed = any(addr in ipaddress.ip_network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        if addr.version != 4 or not allowed or str(addr) == controller_ip:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, 'Enter the private LAN address of the TV stick, not this controller')
    return str(addr)


def check_origin(request: Request):
    origin = request.headers.get('origin')
    if origin and (urlsplit(origin).netloc != request.headers.get('host') or urlsplit(origin).scheme != request.url.scheme):
        raise HTTPException(403, 'Use this server’s controller to provision players')
    if os.name != 'nt':
        raise HTTPException(409, 'Automatic provisioning is available on the Windows server')


def save(job):
    JOBS.mkdir(parents=True, exist_ok=True)
    job['updated_at'] = int(time.time() * 1000)
    path = JOBS / (job['id'] + '.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(job, indent=2), encoding='utf-8')
    temporary.replace(path)


def read(jid):
    if not re.fullmatch(r'[a-f0-9]{12}', jid):
        raise HTTPException(404, 'Setup not found')
    try:
        return json.loads((JOBS / (jid + '.json')).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise HTTPException(404, 'Setup not found')


def recover():
    if JOBS.exists():
        for path in JOBS.glob('*.json'):
            job = json.loads(path.read_text(encoding='utf-8'))
            if job['state'] in ('queued', 'running'):
                job.update(state='interrupted', message='Server restarted. Resume to verify the stick and continue from saved steps.')
                save(job)


def environment():
    env = os.environ.copy()
    folder = WORK_DIR / 'adb'
    folder.mkdir(parents=True, exist_ok=True)
    env.update(ANDROID_USER_HOME=str(folder), ADB_VENDOR_KEYS=str(folder / 'adbkey'))
    return env


def run(args, *, timeout=90, stdin=None, env=None):
    result = subprocess.run([str(v) for v in args], input=stdin, capture_output=True, text=True,
        timeout=timeout, env=env or environment(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = (result.stdout + '\n' + result.stderr).strip()
    if result.returncode:
        raise RuntimeError(output[-2000:] or 'Device command failed')
    return output


def adb(*args, **kwargs):
    return run([ADB, '-P', str(ADB_PORT), *args], **kwargs)


def ensure_adb():
    if not ADB.is_file() or not SCRIPT.is_file():
        raise RuntimeError('Android setup tools are missing on the server')
    env = environment()
    key = Path(env['ADB_VENDOR_KEYS'])
    if not key.exists():
        run([ADB, 'keygen', key], env=env)
    adb('start-server')


def connect(job, wait=35):
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        # Firmware can advertise a duplicate hostname with the wrong IP. Keep the entered IP,
        # use discovery only for the port, and verify the hardware serial before any mutation.
        records = adb('mdns', 'services', timeout=10)
        ports = []
        if job.get('connection_port'):
            ports.append(job['connection_port'])
        for line in records.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[1] == '_adb-tls-connect._tcp':
                if (job.get('hardware_serial') and job['hardware_serial'] in fields[0]) or fields[2].split(':')[0] == job['ip']:
                    try: ports.append(int(fields[2].rsplit(':', 1)[1]))
                    except ValueError: pass
        ports.append(5555)
        for port in dict.fromkeys(ports):
            serial = f"{job['ip']}:{port}"
            try:
                adb('connect', serial, timeout=6)
                hardware = adb('-s', serial, 'shell', 'getprop', 'ro.serialno', timeout=6).strip()
                if not hardware: continue
                if job.get('hardware_serial') and hardware != job['hardware_serial']:
                    adb('disconnect', serial)
                    raise ValueError('This address belongs to a different device; no changes made')
                features = adb('-s', serial, 'shell', 'pm', 'list', 'features', timeout=10)
                if 'feature:android.software.leanback' not in features.splitlines():
                    adb('disconnect', serial)
                    raise ValueError('This is not an Android TV stick. Phone provisioning is blocked.')
                job.update(hardware_serial=hardware, connection_port=port)
                save(job)
                return serial
            except (RuntimeError, subprocess.TimeoutExpired):
                continue
        time.sleep(2)
    raise RuntimeError('Cannot connect yet. Keep Wireless debugging enabled. Enter its current connection port, or pair again if Android forgot authorization.')


def phase(job, serial, name):
    args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT,
        '-Serial', serial, '-Name', job['name'], '-Phase', name, '-AdbServerPort', str(ADB_PORT), '-ServerUrl', public_url()]
    if name == 'Configure': args += ['-Apk', str(APK_DIR / 'projector.apk')]
    if name == 'Debloat': args += ['-HomeVerified']
    output = run(args, timeout=180)
    match = re.search(r'data="([a-f0-9]{12})"', output)
    if match: job['device_id'] = match[1]
    job['events'].append({'step': name, 'at': int(time.time() * 1000), 'result': output[-3000:]})
    job['completed'].append(name)
    save(job)


def execute(job, code='', pairing_port=None):
    try:
        job.update(state='running', step='connect', message='Connecting securely to your stick…'); save(job)
        ensure_adb()
        if code:
            if not pairing_port: raise ValueError('Enter the pairing port shown beside the six-digit code')
            try:
                result = adb('pair', f"{job['ip']}:{pairing_port}", stdin=code + '\n', timeout=40)
                if 'Successfully paired' not in result: raise RuntimeError()
            except Exception:
                raise RuntimeError('Pairing failed. Reopen “Pair using pairing code” and enter its fresh port and code.') from None
        serial = connect(job)
        snapshot = WORK_DIR / 'devices' / job['name'] / 'original.json'
        if snapshot.exists():
            original = json.loads(snapshot.read_text(encoding='utf-8-sig'))
            if original['hardware_serial'] != job['hardware_serial']:
                raise ValueError('This name has a recovery snapshot for another device. Choose a different player name.')
        else:
            phase(job, serial, 'Snapshot')
        for name, message in [('Configure', 'Installing Projector and setting power options…'),
                              ('Maintenance', 'Enabling future maintenance recovery…'),
                              ('Takeover', 'Making Projector the Home screen…')]:
            if name not in job['completed']:
                job.update(step=name.lower(), message=message); save(job)
                try: phase(job, serial, name)
                except RuntimeError as exc:
                    if name == 'Takeover' and 'Wi-Fi ownership' in str(exc):
                        job.update(state='needs_wifi_rejoin', message='Open Android Settings → Network, forget and rejoin your Wi-Fi there. The original setup helper owns that connection; transferring it prevents losing Wi-Fi when we replace Home. Then resume.'); save(job); return
                    raise
        if 'Boot' not in job['completed']:
            job.update(step='boot', message='Restarting the stick and checking Projector comes back…'); save(job)
            try:
                adb('-s', serial, 'reboot', timeout=15)
            except subprocess.TimeoutExpired:
                pass  # Some firmware drops TCP before acknowledging the reboot.
            time.sleep(15)
            serial = connect(job, wait=120)
            home = adb('-s', serial, 'shell', 'cmd', 'package', 'resolve-activity', '--brief', '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.HOME')
            if 'com.projector.app/' not in home: raise RuntimeError('Projector is not the default Home after restart. Recovery commands are saved; other apps have not been disabled.')
            job['completed'].append('Boot'); save(job)
        if 'Debloat' not in job['completed']:
            job.update(step='debloat', message='Disabling the selected streaming and advertising apps…'); save(job)
            phase(job, serial, 'Debloat')
        if job.get('device_id'):
            with session_scope() as session:
                device = session.get(Device, job['device_id'])
                if device:
                    device.name = job['name']; session.add(device); session.commit()
        job.update(state='complete', step='ready', message='Your stick is ready. Choose its surface, then calibrate when the projector is in position.')
        save(job)
    except Exception as exc:
        job.update(state='failed', message=str(exc)[:2000]); save(job)


async def work(job, code='', port=None):
    async with _lock:
        await asyncio.to_thread(execute, job, code, port)
    hub.mark_devices_dirty()


def dispatch(job, code='', port=None):
    _tasks[job['id']] = asyncio.create_task(work(job, code, port))


@router.get('/jobs')
def list_jobs():
    if not JOBS.exists(): return []
    return sorted([json.loads(p.read_text(encoding='utf-8')) for p in JOBS.glob('*.json')], key=lambda j: j['updated_at'], reverse=True)


@router.post('/jobs')
async def start(body: SetupIn, request: Request):
    check_origin(request)
    if not body.dedicated: raise HTTPException(400, 'Confirm this is a dedicated projection stick')
    ip = validate_target(body.ip, request.client.host if request.client else '')
    if any(not t.done() for t in _tasks.values()): raise HTTPException(409, 'Finish the current stick setup first')
    job = {'id': new_id(), 'name': body.name, 'ip': ip, 'connection_port': body.connection_port,
        'state': 'queued', 'step': 'connect', 'message': 'Waiting to connect…', 'completed': [], 'events': []}
    save(job); dispatch(job, body.pairing_code, body.pairing_port)
    return job


@router.post('/jobs/{jid}/resume')
async def resume(jid: str, body: ResumeIn, request: Request):
    check_origin(request)
    if any(not t.done() for t in _tasks.values()): raise HTTPException(409, 'A setup is already running')
    job = read(jid)
    if job['state'] == 'complete': raise HTTPException(409, 'This player is already set up')
    if body.connection_port: job['connection_port'] = body.connection_port
    job.update(state='queued', message='Resuming saved setup…'); save(job)
    dispatch(job, body.pairing_code, body.pairing_port)
    return job
