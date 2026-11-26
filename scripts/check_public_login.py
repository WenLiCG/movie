"""匿名检查公网 Emby 及影院管理员入口，全程不使用账号或 Cookie。"""
import argparse
from contextlib import closing
from http.client import HTTPException, HTTPSConnection
import json
import re
from urllib.parse import urljoin, urlsplit


def portal_redirect(origin, path, location):
    try:
        if '\\' in location or location.startswith('///'):
            return False
        target = urlsplit(urljoin(origin.geturl() + path, location))
        return (target.scheme == 'https' and target.hostname == origin.hostname
                and (target.port if target.port is not None else 443) == (origin.port or 443) and target.username is None
                and target.path == '/discover/' and not target.query and not target.fragment)
    except ValueError:
        return False


def check_public_login(address):
    try:
        origin = urlsplit(address)
        if (origin.scheme != 'https' or not origin.hostname or origin.username is not None
                or origin.path not in ('', '/') or origin.query or origin.fragment or origin.port == 0
                or any(c.isspace() or ord(c) < 32 for c in address)):
            raise ValueError
        origin = origin._replace(path='')
    except ValueError:
        raise ValueError('请输入 HTTPS 站点根地址，不含账号、路径、查询参数或片段。') from None
    passed = True
    for path in ('/web/index.html', '/discover/', '/discover/api/session', '/search/', '/sonarr/', '/qb/', '/openlist/'):
        try:
            # One direct TLS request per route. No cookie jar, environment proxy or redirect following.
            with closing(HTTPSConnection(origin.hostname, origin.port, timeout=12)) as conn:
                conn.request('GET', path, headers={'User-Agent':'Cinema-Public-Login-Check/1'})
                with conn.getresponse() as response:
                    status = response.status
                    mime = response.headers.get_content_type()
                    body = response.read(1024*1024) if path in ('/web/index.html','/discover/','/discover/api/session') else b''
                    location = response.getheader('Location', '')
        except (HTTPException, OSError, ValueError):
            print(f'失败 {path} · 连接、TLS 或 HTTP 响应异常')
            return False
        if path == '/web/index.html':
            ok = (status == 200 and mime == 'text/html' and
                  re.search(rb'<title\b[^>]*>\s*Emby(?:\s+(?:Web|Server))?\s*</title\s*>', body, re.I))
            expected = 'Emby 页面'
        elif path == '/discover/':
            ok = status == 200 and mime == 'text/html' and all(marker in body for marker in (
                b'<form id="login-form"', b'id="username"', b'id="password"', b'/discover/app.js'))
            expected = '影院管理员登录表单'
        elif path == '/discover/api/session':
            try:
                payload = json.loads(body) if status == 401 and mime == 'application/json' else None
            except (ValueError, RecursionError):
                payload = None
            ok = isinstance(payload, dict) and isinstance(payload.get('error'), str) and bool(payload['error'])
            expected = '未登录会话返回 JSON 401'
        else:
            ok = status in (301,302,303,307,308) and bool(location) and portal_redirect(origin, path, location)
            expected = '未登录请求转回同站 /discover/'
        print(f'{"通过" if ok else "失败"} {path} · HTTP {status} · 预期{expected}')
        if not ok:
            passed = False
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin', metavar='https://domain')
    args = parser.parse_args()
    try:
        raise SystemExit(0 if check_public_login(args.origin) else 1)
    except ValueError as error:
        parser.exit(2, str(error)+'\n')
