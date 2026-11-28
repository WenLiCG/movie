"""Verify the portal setup and observe the uploader heartbeat across two updates."""
import json
import sqlite3
import time

from configure import UPLOADER_CONNECTIONS
from portal.server import App


def heartbeat_advances(path):
    first = None
    deadline = time.monotonic() + 75
    while True:
        try:
            at = json.loads(path.read_text(encoding='utf-8'))['at']
            if type(at) in (int, float) and 0 <= time.time() - at < 300:
                if first is None:
                    first = at
                elif at > first:
                    return True
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(2, remaining))


def verify(app):
    config = app.config()
    connected = all(config.get(key) for key in UPLOADER_CONNECTIONS)
    if connected:
        print('正在核对上传心跳是否持续更新，最多等待 75 秒。', flush=True)
        advanced = heartbeat_advances(app.data / 'heartbeat.json')
    checks = app.status()['checks']
    if connected and checks['uploader']['complete']:
        if advanced:
            checks['uploader']['message'] += ' 已确认心跳持续更新。'
        else:
            checks['uploader'] = {'complete': False, 'message': '未观察到两次递增的有效心跳，请检查 uploader 日志与状态目录。'}
    names = {'cloud': '阿里云盘', 'downloader': '下载器', 'indexers': '搜索站点', 'television': '追剧连接',
             'retention': '保留时间', 'metadata': 'Emby 刮削', 'uploader': '上传服务'}
    for key, name in names.items():
        check = checks[key]
        print(f"{'通过' if check['complete'] else '未通过'} · {name} · {check['message']}")
    return connected and all(checks[key]['complete'] for key in names)


if __name__ == '__main__':
    try:
        raise SystemExit(0 if verify(App()) else 1)
    except (AttributeError, KeyError, OSError, TypeError, ValueError, sqlite3.Error):
        raise SystemExit('无法读取影院接线状态，请检查 portal 的配置文件、状态目录和服务连接。') from None
