"""Read bounded, redacted relay evidence; Emby playback mode is not transport proof."""
import hashlib
import json
from pathlib import Path
import re
import time


def playback_records(folder, now=None):
    now = time.time() if now is None else now
    events = []
    for name in ('events.jsonl.1', 'events.jsonl'):
        try:
            with (Path(folder) / name).open('rb') as handle:
                raw = handle.read(2*1024*1024+4096)
        except FileNotFoundError:
            continue
        except OSError:
            raise ValueError('播放记录暂时无法读取，请检查播放服务及目录权限。') from None
        for line in raw.splitlines():
            try:
                event = json.loads(line)
                if (not isinstance(event, dict) or not re.fullmatch(r'[a-zA-Z0-9-]{1,128}', event.get('item', ''))
                        or not re.fullmatch(r'[a-f0-9]{24}', event.get('actor', ''))
                        or type(event.get('at')) is not int or not 0 < event['at'] <= now+300
                        or type(event.get('status')) is not int
                        or not isinstance(event.get('session',''),str)
                        or event.get('kind') not in {'media','start','progress','stop'}):
                    continue
                events.append(event)
            except (ValueError, TypeError, UnicodeError):
                continue
    records, current, sessions = [], {}, {}
    for e in sorted(events, key=lambda value: value['at']):
        actor_item = (e['actor'], e['item'])
        session = e.get('session') or ''
        key = (*actor_item, session)
        record = sessions.get(key) if session else None
        if record is None:
            # shortcut: media URLs omit session IDs; use the latest client/item report, add client-side IDs for concurrent same-item windows sharing one token.
            record = current.get(actor_item)
            if record and (e['at']-record['last_at']>1800 or record['state']=='stopped'
                           or (session and record['session'] and session != record['session'])):
                record = None
        if record is None:
            record = {'id': hashlib.sha256(f"{actor_item}:{e['at']}:{len(records)}".encode()).hexdigest()[:16],
                      'item_id':e['item'], 'session':session, 'started_at':e['at'], 'last_at':e['at'],
                      'route':'unknown', 'host':'', 'http_status':0, 'position_seconds':0,
                      'has_progress':False, 'state':'requested', 'client':'', 'device':'', 'error_status':0}
            records.append(record)
        if session:
            record['session'] = session
            sessions[key] = record
        current[actor_item] = record
        record['last_at'] = e['at']
        for field in ('client', 'device'):
            if isinstance(e.get(field), str) and e[field]: record[field] = e[field][:160]
        if e['kind'] == 'media':
            delivery = e.get('delivery')
            if delivery in {'cloud','vps'} and record['route'] != 'vps':
                host = e.get('host','')
                record.update(route=delivery, host=host if isinstance(host,str) and re.fullmatch(r'[a-z0-9.-]{0,253}',host) else '', http_status=e['status'])
            elif delivery == 'failed':
                record['error_status'] = e.get('status',0)
                record['state'] = 'failed'
            elif delivery == 'unknown' and record['route']=='unknown':
                host=e.get('host','')
                if isinstance(host,str) and re.fullmatch(r'[a-z0-9.-]{1,253}',host):
                    record.update(host=host,http_status=e['status'])
        elif 200 <= e.get('status',0) < 300:
            position = e.get('position',0)
            if type(position) is int and 0 <= position < 10**16:
                record['position_seconds'] = round(position/10**7,1)
                if e['kind'] in {'progress','stop'} and position>0: record['has_progress']=True
            record['state'] = 'stopped' if e['kind']=='stop' else 'paused' if e.get('paused') else 'playing' if record['has_progress'] else 'requested'
        else:
            record['error_status'] = e.get('status',0)
    result = []
    for record in sorted(records, key=lambda value: value['last_at'], reverse=True)[:100]:
        value = {k:v for k,v in record.items() if k!='session'}
        if now-value['last_at']>120 and value['state'] not in {'stopped','failed'}: value['state']='inactive'
        result.append(value)
    return result
