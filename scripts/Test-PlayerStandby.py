"""Hardware acceptance check: observe standby, scheduled cached playback, and return to idle.

Requires an idle runner and a program targeting only the intended device. No ADB is used.
Saves samples and disables its own test schedule when finished, including on interruption.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import time
import urllib.request

p = argparse.ArgumentParser()
p.add_argument('--device', required=True)
p.add_argument('--program', required=True)
p.add_argument('--minutes', type=int, default=31)
p.add_argument('--server', default='http://127.0.0.1:8000')
p.add_argument('--output', required=True)
a = p.parse_args()
if a.minutes < 30:
    p.error('Acceptance requires at least 30 minutes in standby')
out = Path(a.output)
out.mkdir(parents=True, exist_ok=True)

def api(path, method='GET', data=None):
    req = urllib.request.Request(a.server + '/api' + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'}, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)

assert api('/runner')['state'] == 'idle', 'Stop the current show before this check'
device = next(d for d in api('/devices') if d['id'] == a.device)
assert device['online'] and device['status']['management']['loaded_decoders'] == 0
started = dt.datetime.now()
trigger = (started + dt.timedelta(minutes=a.minutes + 1)).replace(second=0, microsecond=0)
end = trigger + dt.timedelta(minutes=2)
def active(s, when):
    sh, sm = map(int, s['start'].split(':')); eh, em = map(int, s['end'].split(':'))
    start_min, end_min, minute = sh * 60 + sm, eh * 60 + em, when.hour * 60 + when.minute
    if start_min <= end_min:
        return when.weekday() in s['days'] and start_min <= minute < end_min
    return (when.weekday() in s['days'] and minute >= start_min) or ((when.weekday() - 1) % 7 in s['days'] and minute < end_min)
for s in api('/schedules')['schedules']:
    if s['enabled']:
        assert not any(active(s, trigger + dt.timedelta(seconds=n)) for n in range(-60, 181, 15)), 'Another schedule overlaps the test window'
schedule = api('/schedules', 'POST', {'program_id': a.program, 'days': [trigger.weekday()],
    'start': trigger.strftime('%H:%M'), 'end': end.strftime('%H:%M'), 'enabled': True})
manifest = {'device': a.device, 'program': a.program, 'schedule': schedule,
    'started_at': started.isoformat(), 'trigger_at': trigger.isoformat(), 'end_at': end.isoformat()}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest), flush=True)
issues, playing, standby_count, final = [], [], 0, None
try:
    with (out / 'samples.jsonl').open('a') as samples:
        while dt.datetime.now() < end + dt.timedelta(seconds=15):
            now = dt.datetime.now()
            try:
                device = next(d for d in api('/devices') if d['id'] == a.device)
                runner = api('/runner')
                m = device['status'].get('management', {})
                row = {'at': now.isoformat(), 'online': device['online'], 'last_seen': device['last_seen'],
                    'management': m, 'asset_id': device['status'].get('asset_id'),
                    'cache_ready': device['status'].get('cache_ready'), 'runner': runner}
                if not device['online']:
                    issues.append({'at': row['at'], 'error': 'Device offline'})
                if now < trigger:
                    standby_count += 1
                    if m.get('loaded_decoders') != 0 or m.get('playback_state') != 'standby' or m.get('continuous_rendering') is not False:
                        issues.append({'at': row['at'], 'error': 'Not decoder-free standby'})
                elif runner.get('schedule_id') == schedule['id'] and row['asset_id'] and m.get('playback_state') == 'playing':
                    playing.append(row['at'])
                final = row
            except Exception as exc:
                row = {'at': now.isoformat(), 'error': str(exc)}
                issues.append(row)
            samples.write(json.dumps(row) + '\n'); samples.flush()
            time.sleep(5)
finally:
    api('/schedules/' + schedule['id'], 'PATCH', {'enabled': False})
    runner = api('/runner')
    if runner.get('schedule_id') == schedule['id']:
        api('/runner/stop', 'POST')
    result = {**manifest, 'finished_at': dt.datetime.now().isoformat(), 'issues': issues,
        'standby_samples': standby_count, 'playing_samples': len(playing), 'first_playing_at': playing[0] if playing else None,
        'passed': not issues and bool(playing) and final is not None and final['management'].get('loaded_decoders') == 0,
        'scope': 'LAN telemetry, decoder release, scheduled start and stop; not audiovisual or synchronization verification'}
    (out / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
