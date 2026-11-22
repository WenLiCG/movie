"""Administrator-only cinema UI; service credentials never leave the server."""
import collections
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import socket
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configure import private_write, write_relay_token
from integration import APIError, Client, connect_services, read_api_key, emby_defaults
from portal.metadata import metadata, resource_id
from portal.playback import playback_records
from portal.versions import NOT_GROUPS, details, rule, matches, validate_profile
from archive import archived_keys, watch_lock, read_requests, save_requests, apply_scope, space_required, video, SUBTITLES, create_actions, task_actions, delete_target

BASE = {"emby": "http://emby:8096", "openlist": "http://openlist:5244/openlist",
        "qb": "http://qbittorrent:8080", "sonarr": "http://sonarr:8989/sonarr",
        "prowlarr": "http://prowlarr:9696/search"}
STATIC = Path(__file__).parent


class TaskScopeChanged(ValueError):
    pass


class SessionCheckUnavailable(Exception):
    pass


def archive_error(code):
    messages = {
        'network_or_protocol': '传输连接中断，等待重试；不代表云盘授权失效。',
        'upload_timeout': '上传请求超时，等待重试。',
        'service_timeout': '服务响应超时，稍后自动重试。',
        'native_upload_failed': 'OpenList 后台上传任务失败，请在云盘管理的任务页面检查并重试。',
        'native_upload_retrying': 'OpenList 后台上传任务正在自动重试。',
        'upload_cache_space_low': 'VPS 暂存空间不足，保留源文件并等待释放空间。',
        'destination_conflict': '云端目标已存在，已停止发布以避免覆盖。',
        'remote_hash_mismatch': '云端 SHA1 校验不一致，已停止发布。',
        'remote_size_mismatch': '云端大小校验未通过，暂不发布。',
        'source_changed': '本地源文件发生变化，已停止发布。',
        'move_pending': '正在等待云端移动任务确认，不会重复上传。',
        'missing_upload_task': '上传任务编号未确认，请检查 OpenList 任务。',
        'openlist_code_404': '云盘上传任务或路径未找到，请检查 OpenList 任务。',
        'openlist_code_401': '云盘管理授权已失效，请重新连接。',
        'openlist_code_403': '云盘拒绝访问，请检查权限。',
        'upload_unconfirmed': '旧上传任务状态尚未确认，保留源文件和暂存文件，等待核对。',
        'upload_cancelled': '上传已取消；可以手动重新上传。',
        'upload_retry_exhausted': '多次重传仍失败，请检查云盘后重新上传。',
        'native_upload_stopping': '云盘任务正在结束，等待确认后再处理。',
        'subtitle_pending': '等待同名外挂字幕发布。',
        'remote_hash_unavailable': '云盘尚未提供完整校验证据，保留源文件并等待确认。',
        'move_retry_ready': '云端移动已结束，等待重新发布。',
        'archive_job_invalid': '归档资料未通过核对，请检查剧集编号及上传任务。',
        'invalid_episode_mapping': '剧集编号无效，等待 Sonarr 提供可确认的集数。',
        'destination_reserved': '已有归档任务占用此云端路径，请核对旧任务和保留的版本。',
    }
    return messages.get(code, '归档暂未完成，请查看云盘任务。') if code else ''


def text(value, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("请输入有效内容。")
    return value.strip()


def integer(value, low=1, high=2147483647):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"请输入 {low}–{high} 之间的整数。")
    return value


def tags(title):
    resolution = next((label for pattern, label in [(r"(?<!\d)(2160p|4k)(?!\w)", "4K"),
                       (r"(?<!\d)1080[pi](?!\w)", "1080p"), (r"(?<!\d)720p(?!\w)", "720p")]
                       if re.search(pattern, title, re.I)), "未知")
    codec = "H.265" if re.search(r"\b(hevc|[xh][ ._-]?265)\b", title, re.I) else (
        "H.264" if re.search(r"\b(avc|[xh][ ._-]?264)\b", title, re.I) else "未知")
    return resolution, codec


class Metrics:
    def __init__(self):
        self.previous = None
        self.lock = threading.Lock()

    def read(self, proc=Path("/proc"), media=Path("/media")):
        disk = shutil.disk_usage(media)
        result = {"disk": {"total": disk.total, "used": disk.used, "free": disk.free}}
        try:
            mem = {line.split(":")[0]: int(line.split()[1]) * 1024
                   for line in (proc / "meminfo").read_text().splitlines() if len(line.split()) >= 2}
            result["memory"] = {"total": mem["MemTotal"], "used": mem["MemTotal"] - mem["MemAvailable"]}
            values = list(map(int, (proc / "stat").read_text().splitlines()[0].split()[1:]))
            # guest/guest_nice are already included in user/nice.
            current = (sum(values[:8]), values[3] + values[4])
            with self.lock:
                old, self.previous = self.previous, current
            result["cpu"] = None if old is None or current[0] <= old[0] else round(
                max(0, min(100, 100 * (1 - (current[1] - old[1]) / (current[0] - old[0])))), 1)
        except (OSError, ValueError, KeyError, IndexError):
            result.update(memory=None, cpu=None)
        return result


class App:
    def __init__(self, state=Path("/state"), services=Path("/services"), origins=None):
        self.state, self.services = Path(state), Path(services)
        self.origins = origins or BASE
        self.sessions, self.attempts = {}, {}
        self.login_total = collections.deque()
        self.data = self.state / 'data'
        # shortcut: serialize admin writes; use per-series locks if concurrent administrators need independent downloads.
        self.lock, self.mutation = threading.Lock(), threading.Lock()
        self.metrics = Metrics()
        self.localized = {}
        self.recommendations_lock, self.recommendations_cache = threading.Lock(), None

    def client(self, service):
        key = read_api_key(self.services / service / "config.xml") if service in {"sonarr", "prowlarr"} else None
        return Client(self.origins[service], key)

    def config(self):
        path = self.state / "config.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def settings(self):
        path = self.state / "settings.json"
        return json.loads(path.read_text()) if path.exists() else {"retention_days": 7}

    def login(self, body, peer="local"):
        username = text(body.get("username"))
        text(body.get("password"), 1024)
        password = body["password"]
        with self.lock:
            now = time.time()
            for key in list(self.attempts):
                while self.attempts[key] and self.attempts[key][0] < now-60:
                    self.attempts[key].popleft()
                if not self.attempts[key]:
                    self.attempts.pop(key)
            while self.login_total and self.login_total[0] < now-60:
                self.login_total.popleft()
            key=(peer,username.casefold())
            attempts=self.attempts.setdefault(key,collections.deque())
            if len(attempts)>=8 or sum(len(v) for k,v in self.attempts.items() if k[0]==peer)>=16 or len(self.login_total)>=120:
                raise ValueError('登录尝试过多，请一分钟后重试。')
            if len(self.attempts)>1024:
                self.attempts.pop(next(iter(self.attempts)))
            attempts.append(now);self.login_total.append(now)
        client = self.client("emby")
        client.timeout = 15
        client.opener.addheaders = [("X-Emby-Authorization", 'Emby Client="Cinema", Device="Web", DeviceId="cinema-discover", Version="1"')]
        try:
            result = client.json("POST", "/Users/AuthenticateByName", {"Username": username, "Pw": password})
        except APIError as error:
            if error.status == 401:
                raise ValueError("登录失败：Emby 用户名或密码不正确。") from None
            raise
        if not isinstance(result, dict):
            raise ValueError("登录失败：Emby 未返回有效会话，请稍后重试。")
        if not result.get("AccessToken") or result.get("User", {}).get("Policy", {}).get("IsAdministrator") is not True:
            if result.get("AccessToken"):
                client.opener.addheaders.append(("X-Emby-Token", result["AccessToken"]))
                client.request("POST", "/Sessions/Logout")
            raise ValueError("请使用 Emby 管理员账号登录。")
        sid = secrets.token_urlsafe(32)
        session = {"token": result["AccessToken"], "user": result["User"]["Id"], "expires": now + 8 * 3600,
                   "username": username, "csrf": secrets.token_urlsafe(24), "results": {}}
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            if len(self.sessions) >= 32:
                raise ValueError("会话数量已满，请注销其他会话。")
            self.sessions[sid] = session
        try:
            self.metadata_defaults(session, only_once=True)
        except (ValueError, OSError):
            pass  # The settings check exposes incomplete initialization without blocking login.
        return sid, session

    def authenticate(self, sid):
        with self.lock:
            session = self.sessions.get(sid)
            if not session or session["expires"] <= time.time():
                self.sessions.pop(sid, None)
                raise PermissionError("请重新登录。")
        client = self.client("emby")
        client.timeout = 10
        client.opener.addheaders = [("X-Emby-Token", session["token"])]
        try:
            user = client.json("GET", "/Users/" + session["user"])
            policy = user.get('Policy') if isinstance(user, dict) else None
            if not isinstance(policy, dict) or type(policy.get('IsAdministrator')) is not bool:
                raise SessionCheckUnavailable()
        except APIError as error:
            if error.status not in {401, 403, 404}:
                raise SessionCheckUnavailable() from None
            policy = {}
        except (ValueError, OSError):
            raise SessionCheckUnavailable() from None
        with self.lock:
            if policy.get('IsAdministrator') is not True or policy.get('IsDisabled'):
                self.sessions.pop(sid, None)
                raise PermissionError("管理员权限已失效。")
            if self.sessions.get(sid) is not session or session['expires'] <= time.time():
                raise PermissionError("请重新登录。")
            return session

    def logout(self, sid, csrf):
        """Revoke the local session first; Emby may be unreachable and must not keep it alive."""
        with self.lock:
            session = self.sessions.get(sid)
            if not session:
                return True  # Already signed out; clearing the cookie is idempotent.
            if not secrets.compare_digest(csrf, session["csrf"]):
                return False
            self.sessions.pop(sid, None)
        client = self.client("emby")
        client.timeout = 10
        client.opener.addheaders = [("X-Emby-Token", session["token"])]
        try:
            client.request("POST", "/Sessions/Logout")
        except (ValueError, OSError):
            pass  # The portal session is already gone; Emby expires its own token later.
        return True

    def watch_session(self, session):
        info=self.client('emby').json('GET','/System/Info/Public')
        return {'server_id':info['Id'],'name':info.get('ServerName','私人影院'),
                'user_id':session['user'],'token':session['token']}

    def qb(self):
        config = self.config()
        if not config.get("qb_password"):
            raise ValueError("请先在设置中自动配置下载器。")
        qb = self.client("qb")
        raw = qb.request("POST", "/api/v2/auth/login", form={"username": config["qb_username"], "password": config["qb_password"]})
        if raw.strip() not in (b"", b"Ok.") or not any(re.fullmatch(r"SID|QBT_SID_[0-9]+", c.name) and c.value for c in qb.cookies):
            raise ValueError("下载器登录失败，请更新下载器账号。")
        return qb

    @staticmethod
    def ol(client, method, path, value=None):
        response = client.json(method, path, value)
        if response.get("code") != 200:
            raise ValueError("云盘操作失败，请检查授权或打开 OpenList 查看挂载状态。")
        return response.get("data")

    def openlist(self, session, body=None):
        client = self.client("openlist")
        token = self.config().get("openlist_token")
        body = body or {}
        if not token or body.get('openlist_password'):
            username=text(body.get('openlist_username') or session['username'])
            password=body.get('openlist_password')
            if not password:
                raise ValueError('请填写 OpenList 管理员账号和密码，仅用于本次连接。')
            text(password,1024)
            login = self.ol(client, 'POST', '/api/auth/login', {'username':username,'password':password})
            client.opener.addheaders = [("Authorization", login["token"])]
            token = self.ol(client, "GET", "/api/admin/setting/get?key=token")["value"]
        client.opener.addheaders = [("Authorization", token)]
        return client, token

    def configure(self, session, body):
        ol, token = self.openlist(session, body)
        refresh = body.get("refresh_token", "")
        if refresh:
            refresh = text(refresh, 4096)
            storages = self.ol(ol, "GET", "/api/admin/storage/list?page=1&per_page=1000")
            found = [s for s in storages["content"] if s["mount_path"].rstrip("/") == "/aliyun"]
            if len(found) > 1 or (found and found[0]["driver"] != "AliyundriveOpen"):
                raise ValueError("/aliyun 已被其他挂载占用，请先在 OpenList 检查。")
            storage = dict(found[0]) if found else {"mount_path": "/aliyun", "driver": "AliyundriveOpen", "cache_expiration": 30, "order": 0}
            addition = json.loads(storage.get("addition") or "{}")
            addition.update(refresh_token=refresh)
            if not found:
                addition.update(root_folder_id="root", drive_type="resource", use_online_api=True,
                                alipan_type="default", api_url_address="https://api.oplist.org/alicloud/renewapi",
                                client_id="", client_secret="", remove_way="trash", internal_upload=False,
                                rapid_upload=True, livp_download_format="jpeg")
            storage.update(addition=json.dumps(addition), web_proxy=False, webdav_policy="302_redirect", disabled=False)
            storage.pop("mount_details", None)
            self.ol(ol, "POST", "/api/admin/storage/" + ("update" if found else "create"), storage)
        remote = self.ol(ol, "POST", "/api/fs/get", {"path": "/aliyun", "password": ""})
        if not remote.get("is_dir") or remote.get("provider") != "AliyundriveOpen":
            raise ValueError("请填写阿里云盘 Open Refresh Token，或检查 /aliyun 挂载。")
        config = self.config()
        username = body.get("qb_username") or config.get("qb_username") or session["username"]
        password = body.get('qb_password') or config.get('qb_password')
        if not password:
            raise ValueError('请填写 qB 正式登录密码，后台下载服务需要保存此凭据。')
        prowlarr_key = read_api_key(self.services / "prowlarr/config.xml")
        sonarr_key = read_api_key(self.services / "sonarr/config.xml")
        connect_services(token, username, password, prowlarr_key, sonarr_key, self.origins)
        listing = self.ol(ol, "POST", "/api/fs/list", {"path": "/aliyun", "password": "", "page": 1, "per_page": 1000, "refresh": True})
        for folder in ("Movies", "TV", ".cinema-upload"):
            existing = next((i for i in listing.get("content", []) if i.get("name") == folder), None)
            if existing and not existing.get("is_dir"):
                raise ValueError("云盘目录名称已被文件占用，请在 OpenList 检查。")
            if not existing:
                self.ol(ol, "POST", "/api/fs/mkdir", {"path": "/aliyun/" + folder})
        private_write(self.state / "config.json", json.dumps({**{k:v for k,v in config.items() if k in {'emby_url','emby_token'}}, "openlist_url": self.origins["openlist"],
                      "openlist_token": token, "qb_url": self.origins["qb"], "qb_username": username,
                      "qb_password": password, "sonarr_url": self.origins["sonarr"], "sonarr_api_key": sonarr_key}))
        write_relay_token(self.state / 'relay-token', token)
        return {"message": "阿里云盘与下载器已连接，自动上传已就绪。"}

    def playbacks(self, session):
        items = playback_records(Path('/playback'))
        names = {}
        if items:
            client = self.client('emby')
            client.opener.addheaders = [('X-Emby-Token', session['token'])]
            try:
                ids = ','.join(dict.fromkeys(item['item_id'] for item in items))
                for value in client.json('GET', '/Items?' + urlencode({'Ids':ids,'Fields':'SeriesName','Recursive':'true'}))['Items']:
                    name = value.get('Name') or '未命名影片'
                    if value.get('Type') == 'Episode':
                        name = f"{value.get('SeriesName') or '剧集'} · S{value.get('ParentIndexNumber') or 0:02}E{value.get('IndexNumber') or 0:02} · {name}"
                    names[str(value['Id'])] = name
            except (ValueError, OSError):
                pass
        return {'items':[{**item,'title':names.get(item['item_id'],'媒体 #'+item['item_id'])} for item in items]}

    def metadata_defaults(self, session, only_once=False):
        with watch_lock(self.data / 'emby-defaults'):
            marker = self.state / 'emby-defaults.applied'
            if only_once and marker.is_file():
                return
            client = self.client('emby')
            client.timeout = 15
            client.opener.addheaders = [('X-Emby-Token', session['token'])]
            emby_defaults(client)
            private_write(marker, '1\n')
        self.localized.clear()
        return {'message': '已应用中文刮削推荐设置，并提交补充缺失资料和图片；已有手工修改及海报保留。'}

    def localize(self, session, values, query=None):
        def chinese(value):
            return isinstance(value, str) and bool(re.search(r'[\u3400-\u9fff]', value))
        def fetch(name, item=None):
            if not session.get('token'):
                return []
            client = self.client('emby')
            client.timeout = 12
            client.opener.addheaders = [('X-Emby-Token', session['token'])]
            info = {'Name': name, 'MetadataLanguage': 'zh-CN', 'MetadataCountryCode': 'CN'}
            if item:
                info['ProviderIds'] = {'Tvdb': str(item['tvdbId'])}
            try:
                result = client.json('POST', '/Items/RemoteSearch/Series', {'SearchInfo': info, 'IncludeDisabledProviders': False})
                return result if isinstance(result, list) and all(isinstance(v, dict) for v in result) else []
            except (ValueError, OSError):
                return None
        # Reuse Emby's installed providers; IDs, not translated titles, determine the series match.
        matches = []
        missing = [v for v in values if self.localized.get(v['tvdbId'], {}).get('expires', 0) < time.time()]
        if query and missing:
            found = fetch(query)
            matches.extend(found or [])
        if not query:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for result in pool.map(lambda v: fetch(v['title'], v), missing):
                    matches.extend(result or [])
        for value in values:
            for match in matches:
                ids = match.get('ProviderIds') or {}
                same = str(ids.get('Tvdb', '')) == str(value['tvdbId']) or (
                    value.get('tmdbId') and str(ids.get('Tmdb', '')) == str(value['tmdbId']))
                if same and chinese(match.get('Name')):
                    self.localized[value['tvdbId']] = {'title': match['Name'], 'overview': match.get('Overview'), 'expires': time.time() + 86400}
                    break
        result = {}
        if not query:
            with self.lock:
                for value in missing:
                    key = value['tvdbId']
                    if self.localized.get(key, {}).get('expires', 0) < time.time():
                        self.localized[key] = {'expires': time.time() + 300}
        for value in values:
            cached = self.localized.get(value['tvdbId'], {})
            aliases = value.get('alternateTitles') or []
            title = cached.get('title') or next((a['title'] for a in aliases if chinese(a.get('title'))), value['title'])
            result[value['tvdbId']] = {'title': title, 'original_title': value['title'] if title != value['title'] else '',
                                      'overview': cached.get('overview') or value.get('overview', '')}
        return result

    def recommendations(self):
        with self.recommendations_lock:
            client = self.client('prowlarr')
            client.timeout = 30
            indexers = [i for i in client.json('GET', '/api/v1/indexer') if i.get('enable') and i.get('supportsRss')]
            sites = {i['id']: i['name'] for i in indexers}
            cached = self.recommendations_cache
            if cached and cached['sites'] == sites and cached['expires'] > time.time():
                return cached['result']
            result = {'items': [], 'message': ''}
            if not sites:
                result['message'] = '先在设置中启用支持近期列表的 PT 站点。'
                return result
            try:
                values = client.json('GET', '/api/v1/search?' + urlencode(
                    {'query': '', 'type': 'search', 'categories': [2000, 5000],
                     'indexerIds': list(sites), 'limit': 100}, doseq=True))
                seen, counts, selected = set(), collections.Counter(), []
                for value in sorted(values, key=lambda v: v.get('seeders') or 0, reverse=True):
                    if value.get('indexerId') not in sites or not value.get('seeders'):
                        continue
                    categories = [c['id'] for c in value.get('categories', [])]
                    kind = 'movie' if any(2000 <= c < 3000 for c in categories) else (
                        'tv' if any(5000 <= c < 6000 for c in categories) else None)
                    title = text(value['title'], 2000)
                    # Keep the release's search terms when displaying a site-provided Chinese name.
                    query = re.split(r'[ ._-](?:(?:19|20)\d{2}|S\d{1,2}(?:E\d+)?|\d{3,4}p|BluRay|WEB-DL)\b', title, maxsplit=1, flags=re.I)[0]
                    query = re.sub(r'[._]+', ' ', query).strip()[:200] or title[:200]
                    key = (kind, value.get('imdbId') or query.casefold())
                    if not kind or key in seen or counts[kind] >= 6:
                        continue
                    seen.add(key); counts[kind] += 1
                    selected.append(value)
                    result['items'].append({'title': query, 'query': query, 'release': title, 'kind': kind,
                                            'site': sites[value['indexerId']], 'seeders': value['seeders']})
                def load(indexer):
                    try:
                        return indexer['id'], metadata(indexer, '', 'all')
                    except (ValueError, OSError, KeyError, TypeError, LookupError):
                        return indexer['id'], {}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    supplements = dict(pool.map(load, [i for i in indexers if i['id'] in {v['indexerId'] for v in selected}]))
                for item, value in zip(result['items'], selected):
                    subtitle = supplements.get(value['indexerId'], {}).get(resource_id(value), {}).get('subtitle', '')
                    # Site subtitles start with the name, followed by aliases, episode counts or release notes.
                    name = re.split(r'[/／|｜【\[]|\s+(?:全|第)\s*[\d一二三四五六七八九十百零两]+[季集部期]', subtitle, maxsplit=1)[0].strip()
                    if len(name) <= 80 and re.search(r'[\u3400-\u9fff]', name):
                        item['title'] = name
                if not result['items']:
                    result['message'] = '站点近期列表暂无电影或剧集热种；仍可直接搜索。'
                ttl = 1800
            except (ValueError, OSError):
                result['message'] = '站点近期列表暂时不可用，稍后重试；仍可直接搜索。'
                ttl = 60
            self.recommendations_cache = {'sites': sites, 'expires': time.time() + ttl, 'result': result}
            return result

    def search(self, session, query, kind):
        query = text(query)
        if kind not in {'movie', 'tv'}:
            raise ValueError('请选择电影或剧集／综艺搜索。')
        params = {'query': query, 'type': 'search'}
        if kind == 'movie': params['categories'] = '2000'
        values = self.client("prowlarr").json("GET", "/api/v1/search?" + urlencode(params))
        supplements, warnings = {}, []
        try:
            indexers = [i for i in self.client("prowlarr").json("GET", "/api/v1/indexer")
                        if i.get("enable") and i['id'] in {v['indexerId'] for v in values}]
            def load(indexer):
                try:
                    return indexer['id'], metadata(indexer, query, kind)
                except (ValueError, OSError, KeyError, TypeError, LookupError):
                    return indexer['id'], None
            with ThreadPoolExecutor(max_workers=4) as pool:
                supplements = dict(pool.map(load, indexers))
            if any(value is None for value in supplements.values()):
                warnings.append('部分站点的附加资料暂时不可用。')
        except (ValueError, OSError):
            if values:
                warnings.append('站点附加资料暂时不可用。')
        session['results'] = {k:v for k,v in session.get('results',{}).items() if v[0] > time.time()}
        if len(session['results']) > 5000:
            session['results'] = {}
        items = []
        for value in values:
            rid = secrets.token_urlsafe(12)
            session["results"][rid] = (time.time() + 1800, {**value, '_kind': kind, '_query': query})
            resolution, codec = tags(value["title"])
            extra = (supplements.get(value['indexerId']) or {}).get(resource_id(value), {})
            version, span = details(value['title'])
            resolution, codec = version['resolution'] or resolution, version['codec'] or codec
            items.append({"id": rid, "title": value["title"], "size": value["size"], "seeders": value.get("seeders"),
                          "site": value["indexer"], "resolution": resolution, "codec": codec,
                          'group': version['group'], 'episodes': span if kind == 'tv' else '',
                          'subtitle': extra.get('subtitle', ''), 'labels': extra.get('labels', []),
                          'metadata_available': bool(extra),
                          'categories': ', '.join(c.get('name','') for c in value.get('categories', [])),
                          "free": "freeleech" in [f.lower() for f in value.get("indexerFlags", [])]})
        for item in items:
            if re.search(r'\b(?:BDMV|BDISO|COMPLETE[. _-]BLURAY)\b', item['title'], re.I):
                item['labels'] = [*item['labels'], '原盘']
        return {"items": sorted(items, key=lambda i: i['size']), 'warnings': warnings}

    def stored_release(self, session, rid, kind=None):
        stored = session.get('results', {}).get(rid)
        if not stored or stored[0] < time.time():
            raise ValueError('结果已过期，请重新搜索。')
        if kind and stored[1].get('_kind') != kind:
            raise ValueError('请选择对应搜索入口中的资源。')
        return stored[1]

    def preview_release(self, session, body):
        rid = text(body.get('release_id'))
        release = self.stored_release(session, rid, 'tv')
        sonarr = self.client('sonarr')
        parsed = sonarr.json('GET', '/api/v3/parse?' + urlencode({'title': release['title']}))
        info = parsed.get('parsedEpisodeInfo') or {}
        if not info.get('seriesTitle'):
            return {'available': False, 'message': 'Sonarr 未能识别此资源的剧名或集数，自动追更暂不可用；不会提交到电影下载目录。'}
        if parsed.get('series'):
            candidates = [parsed['series']]
        else:
            term = 'tvdb:' + str(release['tvdbId']) if release.get('tvdbId') else info['seriesTitle']
            candidates = sonarr.json('GET', '/api/v3/series/lookup?' + urlencode({'term': term}))[:20]
        if not candidates:
            return {'available': False, 'message': 'Sonarr 没有找到可关联的剧集／综艺，自动追更暂不可用；请在高级管理核对元数据。'}
        fields, span = details(release['title'])
        native_group = info.get('releaseGroup') or ''
        if fields['group'] and not (native_group and (fields['group'].casefold() == native_group.casefold() or fields['group'].casefold().startswith(native_group.casefold() + '@'))):
            fields['group'] = ''
        if not fields['group'] and native_group and native_group.casefold() not in NOT_GROUPS:
            fields['group'] = native_group
        session.setdefault('series', {}).update({str(v['tvdbId']): v for v in candidates})
        session.setdefault('version_previews', {})[rid] = {'info': info, 'candidates': [v['tvdbId'] for v in candidates]}
        localized = self.localize(session, candidates, release['_query'])
        records = self.version_records()
        return {'available': True, 'title': release['title'], 'fields': fields, 'episodes': span,
                'missing': [key for key, value in fields.items() if not value],
                'candidates': [{'id': str(v['tvdbId']), 'title': localized[v['tvdbId']]['title'], 'year': v.get('year'),
                                'seasons': [s['seasonNumber'] for s in v.get('seasons', []) if s['seasonNumber'] > 0],
                                'version': records.get(str(v['tvdbId']), {}).get('fields')} for v in candidates]}

    def version_records(self):
        path = self.state / 'versions.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def require_version(self, sonarr, series, profiles=None):
        record = self.version_records().get(str(series['tvdbId']))
        if not record:
            raise ValueError('请先在剧集／综艺搜索中选择资源，保存该剧的版本规则。')
        profiles = sonarr.json('GET', '/api/v3/releaseprofile') if profiles is None else profiles
        return validate_profile(record,series,profiles)

    def save_version(self, session, body):
        with watch_lock(self.data):
            return self._save_version(session,body)

    def _save_version(self, session, body):
        rid = text(body.get('release_id'))
        release = self.stored_release(session, rid, 'tv')
        preview = session.get('version_previews', {}).get(rid)
        tvdb = str(body.get('id'))
        if not preview or int(tvdb) not in preview['candidates'] or body.get('confirmed') is not True:
            raise ValueError('请先预览资源并确认正确的剧集与全部版本条件。')
        mode=body.get('mode','all')
        if mode not in {'all','future','season','manual'}:
            raise ValueError('请选择追更范围。')
        candidate=session['series'][tvdb]
        if mode=='season' and body.get('season') not in [v['seasonNumber'] for v in candidate.get('seasons',[])]:
            raise ValueError('季数无效。')
        fields, pattern = rule(body.get('fields'))
        if not matches(pattern, release['title']):
            raise ValueError('请检查版本条件：所选资源标题并不同时满足这些条件，未保存规则。')
        records = self.version_records()
        old = records.get(tvdb)
        if old and old['fields'] != fields and body.get('replace') is not True:
            raise ValueError('已有版本规则，请明确确认换版；已下载和正在下载的内容会保留。')
        sonarr = self.client('sonarr')
        info = preview['info']
        session['series'][tvdb] = {**session['series'][tvdb], 'seriesType': 'daily' if info.get('isDaily') else ('anime' if info.get('isAbsoluteNumbering') else session['series'][tvdb].get('seriesType', 'standard'))}
        series = next((s for s in sonarr.json('GET', '/api/v3/series') if s['tvdbId'] == int(tvdb)), None)
        if series and old and old['fields'] == fields:
            try:
                self.require_version(sonarr, series)
                return series
            except ValueError:
                pass  # Explicit confirmation also repairs an altered native rule.
        tag_name, profile_name = 'cinema-version-tvdb-' + tvdb, 'Cinema Version ' + tvdb
        tags = sonarr.json('GET', '/api/v3/tag')
        tag = next((t for t in tags if t['label'] == tag_name), None)
        tag = tag or sonarr.json('POST', '/api/v3/tag', {'label': tag_name})
        if any(s['tvdbId'] != int(tvdb) and tag['id'] in s.get('tags', []) for s in sonarr.json('GET', '/api/v3/series')):
            raise ValueError('Sonarr 版本标签被其他剧集共用，请先在高级管理修复，避免影响其他剧。')
        profiles = [p for p in sonarr.json('GET', '/api/v3/releaseprofile') if p['name'] == profile_name]
        if len(profiles) > 1 or (profiles and profiles[0].get('tags') != [tag['id']]):
            raise ValueError('Sonarr 存在冲突的同名版本规则，请在高级管理核对后重试。')
        quality = self.quality_profile(sonarr,fields['resolution'] or 'any')
        monitored = bool(series and series.get('monitored'))
        if series:
            series['monitored'] = False
            sonarr.json('PUT', '/api/v3/series/' + str(series['id']), series)
        try:
            profile = {'name': profile_name, 'enabled': True, 'required': [pattern], 'ignored': [], 'indexerId': 0, 'tags': [tag['id']]}
            if profiles:
                profile['id'] = profiles[0]['id']
                sonarr.json('PUT', '/api/v3/releaseprofile/' + str(profile['id']), profile)
            else:
                profile = sonarr.json('POST', '/api/v3/releaseprofile', profile)
            records[tvdb] = {'fields': fields, 'pattern': pattern, 'profile_id': profile['id'], 'tag_id': tag['id']}
            private_write(self.state / 'versions.json', json.dumps(records, ensure_ascii=False))
            if not series:
                candidate = session['series'][tvdb]
                session['series'][tvdb] = {**candidate, 'tags': sorted(set(candidate.get('tags', [])) | {tag['id']})}
                mode = body.get('mode', 'all')
                # Native addOptions schedules searches after the initial metadata import, with rules already attached.
                added = self.follow(session, {'id': tvdb, 'mode': mode, 'season': body.get('season'), 'quality': fields['resolution'] or 'any'})
                series = sonarr.json('GET', '/api/v3/series/' + str(added['series_id']))
                self.require_version(sonarr, series)
                return {**series, '_cinema_new': True}
            series['tags'] = sorted(set(series.get('tags', [])) | {tag['id']})
            series['qualityProfileId'] = quality['id']
            sonarr.json('PUT', '/api/v3/series/' + str(series['id']), series)
            self.require_version(sonarr, series)
            if monitored and body.get('mode') == 'manual':
                series['monitored'] = True
                sonarr.json('PUT', '/api/v3/series/' + str(series['id']), series)
        except (ValueError, OSError):
            raise ValueError('Sonarr 版本操作未完成或结果未确认；请刷新我的追剧核对规则与监控状态后重试。') from None
        return series

    def quality_profile(self, sonarr, quality):
        if quality not in {'1080p', '1080i', '4K', '720p', 'any'}:
            raise ValueError('请选择有效画质。')
        quality = '1080p' if quality == '1080i' else quality
        wanted = '2160p' if quality == '4K' else quality
        qualities = sonarr.json('GET', '/api/v3/qualityprofile/schema')
        qualities.pop('id', None)
        qualities.update(name='Cinema ' + quality, upgradeAllowed=False)
        allowed = []
        for item in qualities['items']:
            item['allowed'] = False
            for child in item.get('items') or [item]:
                child['allowed'] = quality == 'any' or wanted in child.get('quality', {}).get('name', '')
                if child['allowed']:
                    item['allowed'] = True
                    allowed.append(item['id'] if item.get('items') else child['quality']['id'])
        if not allowed:
            raise ValueError('Sonarr 没有对应画质模板，请打开高级管理检查。')
        qualities['cutoff'] = allowed[-1]
        profile = next((p for p in sonarr.json('GET', '/api/v3/qualityprofile') if p['name'] == qualities['name']), None)
        if profile and profile.get('upgradeAllowed') is not False:
            profile['upgradeAllowed'] = False
            sonarr.json('PUT', '/api/v3/qualityprofile/' + str(profile['id']), profile)
        return profile or sonarr.json('POST', '/api/v3/qualityprofile', qualities)

    def check_space(self, size):
        if type(size) is not int or size <= 0:
            raise ValueError('请先确认资源体积。')
        disk = shutil.disk_usage('/media')
        torrents = self.qb().json('GET', '/api/v2/torrents/info')
        if not isinstance(torrents, list) or any(not isinstance(t, dict) for t in torrents):
            raise ValueError('下载器任务列表暂不可用，请重试。')
        remaining = sum(max(0, t.get('amount_left', 0)) for t in torrents
                        if t.get('category') in {'cinema-movies','cinema-tv','sonarr-tv'}
                        and t.get('state') not in {'stoppedDL','pausedDL'})
        backlog = 0
        path = self.data / 'uploads.sqlite3'
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db, db:
                backlog = db.execute("SELECT COALESCE(max(size),0) FROM jobs WHERE state IN ('pending','uploading')").fetchone()[0]
        required = space_required(size, remaining, backlog, disk.total)
        if required > disk.free:
            raise ValueError(f'VPS 空间不足：需要 {required/1024**3:.1f} GB，当前可用 {disk.free/1024**3:.1f} GB（含上传暂存和安全余量）。')

    def download_release(self, session, body, kind='tv'):
        release = self.stored_release(session, body.get('release_id'), kind)
        if release.get('_downloaded'):
            raise ValueError('已提交此资源下载，请在下载任务查看进度。')
        self.check_space(release['size'])
        prowlarr = self.client('prowlarr')
        category = 'cinema-tv' if kind == 'tv' else 'cinema-movies'
        name = 'Cinema qB TV' if kind == 'tv' else 'Cinema qB'
        clients = [c for c in prowlarr.json('GET', '/api/v1/downloadclient') if c['name'] == name]
        if len(clients) != 1:
            raise ValueError('下载器尚未完成独立下载配置，请在设置中重新自动配置。')
        client = clients[0]
        fields = {f['name']: f.get('value') for f in client['fields']}
        path = '/media/downloads/tv-manual' if kind == 'tv' else '/media/downloads/movies'
        if (not client.get('enable') or client.get('implementation') != 'QBittorrent' or client.get('categories')
                or fields.get('category') != category or fields.get('host') != 'qbittorrent'
                or fields.get('port') != 8080
                or self.qb().json('GET', '/api/v2/torrents/categories').get(category, {}).get('savePath', '').rstrip('/') != path):
            raise ValueError('下载器分类或连接已变更，请重新自动配置；未提交下载。')
        # Prowlarr grabs the exact cached GUID, with an explicit client to prevent category fallback.
        prowlarr.json('POST', '/api/v1/search', {'guid': release['guid'], 'indexerId': release['indexerId'], 'downloadClientId': client['id']})
        if kind == 'tv':
            release['_downloaded'] = True  # Keep the TV result so 按此版本追更 still works on the same row.
        else:
            session['results'].pop(body['release_id'], None)
        return {'message': '已提交此资源下载，完成后归档到 TV；合集会下载整包，不创建或修改追剧。' if kind == 'tv' else '已提交下载，请在下载任务查看进度。'}

    def remove_series(self, body):
        with watch_lock(self.data):
            return self._remove_series(body)

    def _remove_series(self, body):
        sonarr = self.client('sonarr')
        series = sonarr.json('GET', '/api/v3/series/' + str(integer(body.get('id'))))
        tags = sonarr.json('GET', '/api/v3/tag')
        tag = next((t for t in tags if t['label'] == 'cinema-unfollowed'), None)
        tag = tag or sonarr.json('POST', '/api/v3/tag', {'label': 'cinema-unfollowed'})
        # Keep native metadata so in-flight downloads can still import and archive safely.
        series.update(monitored=False, monitorNewItems='none', tags=sorted(set(series.get('tags', [])) | {tag['id']}))
        sonarr.json('PUT', '/api/v3/series/' + str(series['id']), series)
        self.cancel_request(series['tvdbId'])
        return {'message': '已删除追剧。已有文件和正在下载的任务保留，下载完成后继续归档。'}

    def cancel_request(self, tvdb):
        requests = read_requests(self.data)
        requests.pop(str(tvdb), None)
        save_requests(self.data, requests)

    def monitor_series(self, body):
        if type(body.get('monitored')) is not bool:
            raise ValueError('追更状态无效。')
        with watch_lock(self.data):
            sonarr = self.client('sonarr')
            series = sonarr.json('GET', '/api/v3/series/' + str(integer(body.get('id'))))
            if body['monitored']:
                self.require_version(sonarr, series)
                self.release_task_block(series['tvdbId'])
                requests = read_requests(self.data)
                if str(series['tvdbId']) in requests:
                    raise ValueError('请先刷新集数，再设置追更范围。')
            else:
                self.cancel_request(series['tvdbId'])
            series['monitored'] = body['monitored']
            sonarr.json('PUT', '/api/v3/series/' + str(series['id']), series)
        return {'message': '追更状态已更新。'}

    def release_task_block(self, tvdb):
        key='series:'+str(tvdb)
        value=next((a for a in task_actions(self.data/'uploads.sqlite3') if a['id']==key),None)
        if value and value['action'] in {'cancel','delete'}:
            if value['state'] in {'pending', 'blocked'}:
                raise ValueError('请等待任务取消或删除完成，再恢复追更。')
            with closing(sqlite3.connect(self.data/'uploads.sqlite3')) as db, db:
                db.execute("UPDATE task_actions SET state='released' WHERE id=?",(key,))

    def follow(self, session, body):
        with watch_lock(self.data):
            return self._follow(session,body)

    def _follow(self, session, body):
        series = session.get("series", {}).get(str(body.get("id")))
        if not series:
            raise ValueError("搜索结果已过期，请重新搜索剧名。")
        series = json.loads(json.dumps(series))
        sonarr = self.client("sonarr")
        mode = body.get("mode", "all")
        if mode not in {'future', 'all', 'season', 'manual'}:
            raise ValueError('请选择追更范围。')
        if body.get('release_id'):
            prepared = self.save_version(session, body)
            if prepared.get('_cinema_new'):
                return {'series_id': prepared['id'], 'message': '版本规则已保存，Sonarr 将在集数同步后按所选范围处理。'}
        existing = next((s for s in sonarr.json("GET", "/api/v3/series") if s["tvdbId"] == series["tvdbId"]), None)
        if mode != 'manual':
            record = self.require_version(sonarr, existing or series)
            body = {**body, 'quality': record['fields']['resolution'] or 'any'}
        removed_tags = {t['id'] for t in sonarr.json('GET', '/api/v3/tag') if t['label'] == 'cinema-unfollowed'}
        if existing and removed_tags.intersection(existing.get('tags', [])):
            existing['tags'] = [t for t in existing['tags'] if t not in removed_tags]
            sonarr.json('PUT', '/api/v3/series/' + str(existing['id']), existing)
        if existing and mode == 'manual':
            return {'series_id': existing['id']}
        if existing:
            series = json.loads(json.dumps(existing))
        quality = body.get('quality', '1080p')
        profile = self.quality_profile(sonarr,quality)
        season = integer(body.get('season')) if mode=='season' else None
        if mode=='season' and season not in [s['seasonNumber'] for s in series['seasons']]:
            raise ValueError('季数无效。')
        archived=archived_keys(self.data/'uploads.sqlite3',series['tvdbId'])
        if mode!='manual':self.release_task_block(series['tvdbId'])
        if existing:
            series['qualityProfileId']=profile['id']
            ready=apply_scope(sonarr,series,mode,season,archived)
            added=series
        else:
            series.pop('id',None)
            for entry in series['seasons']:
                entry['monitored']=mode!='manual' and entry['seasonNumber']>0 and (mode!='season' or entry['seasonNumber']==season)
            delayed=bool(archived and mode!='manual')
            series.update(qualityProfileId=profile['id'],rootFolderPath='/media/library/tv',monitored=mode!='manual' and not delayed,
                          seasonFolder=True, monitorNewItems='none' if mode in {'manual','season'} or delayed else 'all',
                          addOptions={'monitor':'none' if mode=='manual' or delayed else ('future' if mode=='future' else ('skip' if mode=='season' else 'all')),
                                      'searchForMissingEpisodes':mode not in {'manual','future'} and not delayed,'searchForCutoffUnmetEpisodes':False})
            added=sonarr.json('POST','/api/v3/series',series)
            ready=not delayed
        requests=read_requests(self.data)
        if not ready:
            if existing:
                series['monitored']=False
                sonarr.json('PUT','/api/v3/series/'+str(series['id']),series)
            requests[str(series['tvdbId'])]={'mode':mode,'season':season,'at':time.time()}
        else:
            requests.pop(str(series['tvdbId']),None)
        save_requests(self.data,requests)
        return {"message": "已添加追剧，Sonarr 将按所选范围自动寻找资源。", 'series_id': added['id']}

    def episodes(self, session, body):
        result = self.follow(session, {**body, 'mode': 'manual'})
        series_id = result['series_id']
        episodes = self.client('sonarr').json('GET', '/api/v3/episode?seriesId=' + str(series_id))
        try:
            sonarr = self.client('sonarr')
            series=sonarr.json('GET','/api/v3/series/'+str(series_id))
            self.require_version(sonarr,series)
            version_ready = True
        except ValueError:
            version_ready = False
        archived=archived_keys(self.data/'uploads.sqlite3', int(body['id']))
        downloading=self.queue_episode_ids(sonarr)
        return {'series_id':series_id,'version_ready':version_ready,'items':[
            {**{k:e.get(k) for k in ('id','seasonNumber','episodeNumber','title','airDateUtc','hasFile')},
             'archived':(e['seasonNumber'],e['episodeNumber']) in archived,'downloading':e['id'] in downloading}
            for e in episodes if e['seasonNumber']>0]}

    @staticmethod
    def queue_episode_ids(sonarr):
        value=sonarr.json('GET','/api/v3/queue?page=1&pageSize=1000&includeEpisode=true')
        return {r.get('episodeId') or (r.get('episode') or {}).get('id') for r in (value or {}).get('records',[])}

    def download_episodes(self, body):
        with watch_lock(self.data):
            return self._download_episodes(body)

    def _download_episodes(self, body):
        series_id = integer(body.get('series_id'))
        ids = body.get('episode_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 1000 or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('请选择 1–1000 集，不能重复。')
        sonarr = self.client('sonarr')
        series=sonarr.json('GET','/api/v3/series/'+str(series_id))
        self.require_version(sonarr,series)
        archived=archived_keys(self.data/'uploads.sqlite3',series['tvdbId'])
        downloading=self.queue_episode_ids(sonarr)
        available = {e['id']: e for e in sonarr.json('GET', '/api/v3/episode?seriesId=' + str(series_id))}
        if any(i not in available or available[i].get('hasFile') or i in downloading or (available[i]['seasonNumber'],available[i]['episodeNumber']) in archived for i in ids):
            raise ValueError('所选集数无效、本地或云端已有，或正在下载，请刷新后重试。')
        for start in range(0,len(ids),100):
            sonarr.json('POST','/api/v3/command',{'name':'EpisodeSearch','episodeIds':ids[start:start+100]})
        return {'message': f'已提交 {len(ids)} 集的搜索任务；仅下载符合该剧版本规则的资源；没有匹配就等待。'}

    def uploads(self, all_jobs=False):
        path = self.state / "data/uploads.sqlite3"
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db, db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
                return []
            columns={r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            where='WHERE hidden=0' if 'hidden' in columns else ''
            limit = '' if all_jobs else ' LIMIT 1000'
            values = [dict(r) for r in db.execute('SELECT * FROM jobs '+where+' ORDER BY rowid DESC'+limit)]
        position = 0
        values.reverse()
        fields = {'id', 'source', 'dest', 'state', 'error', 'progress', 'retry_at', 'torrent_hash', 'parent_id',
                  'queue_position', 'size', 'tvdb_id', 'download_source', 'added_on'}
        if all_jobs:
            fields.update({'identity', 'sha1'})
        for item in values:
            if item['state'] == 'pending':
                position += 1
            item['queue_position'] = position if item['state'] == 'pending' else None
        return [{k:v for k,v in item.items() if k in fields}
                | {'error_message': archive_error(item['error'])} for item in values]

    def retry_upload(self, body):
        job_id = text(body.get('id'), 64)
        with closing(sqlite3.connect(self.data / 'uploads.sqlite3', timeout=10)) as db, db:
            job = db.execute('SELECT state,error FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job[0] not in {'failed','uploading','queued','moving','uploaded'}:
                raise ValueError('请刷新任务列表后重试。')
            db.execute('UPDATE jobs SET retry_requested=?,retry_at=0 WHERE id=?', (int(job[0]=='failed'),job_id))
        return {'message':'已请求重新检查；只有确认旧任务结束后才会重传。'}

    def tasks(self, session=None, all_jobs=False):
        # shortcut: cap queue/history views at 1000 files; paginate for a larger multi-user library.
        qb = self.qb()
        torrents = [v for v in qb.json('GET','/api/v2/torrents/info') if v.get('category') in {'cinema-movies','cinema-tv','sonarr-tv'}]
        uploads = self.uploads(all_jobs)
        by_hash, children = collections.defaultdict(list), collections.defaultdict(list)
        visible_ids = {j['id'] for j in uploads}
        for job in uploads:
            by_hash[job.get('torrent_hash')].append(job)
            children[job.get('parent_id')].append(job)
        sonarr=self.client('sonarr')
        series=sonarr.json('GET','/api/v3/series')
        by_id={s['id']:s for s in series}
        by_tvdb={s['tvdbId']:s for s in series}
        queue=sonarr.json('GET','/api/v3/queue?page=1&pageSize=1000&includeSeries=true&includeEpisode=true').get('records',[])
        native_hash={q['downloadId'].lower():by_id.get(q.get('seriesId') or (q.get('series') or {}).get('id')) for q in queue if q.get('downloadId')}
        names=self.localize(session,series) if session else {}
        def build(t):
            jobs = [j for j in by_hash.get(t['hash'], []) if not j.get('added_on') or j['added_on'] == t.get('added_on')]
            jobs = list({v['id']:v for j in jobs for v in [j, *children.get(j['id'], [])]}.values())
            files=[{k:v.get(k) for k in ('index','name','progress','size','priority')} for v in qb.json('GET','/api/v2/torrents/files?'+urlencode({'hash':t['hash']}))]
            for file in files:
                path=Path(t['save_path'])/file['name']
                job=next((j for j in jobs if j.get('download_source')==str(path) or j['source']==str(path)),None)
                if job:file['job_id']=job['id']
            tvdbs={j['tvdb_id'] for j in jobs if j.get('tvdb_id')}
            native=native_hash.get(t['hash'].lower())
            if native:tvdbs.add(native['tvdbId'])
            tvdb=next(iter(tvdbs)) if len(tvdbs)==1 and t['category']=='sonarr-tv' else None
            return {k:t.get(k) for k in ('hash','name','progress','state','dlspeed','size','category','save_path','download_path','added_on')} | {'uploads':jobs,'files':files,'tvdb_id':tvdb}
        with ThreadPoolExecutor(max_workers=4) as pool:
            resources=list(pool.map(build,torrents))
        groups={}
        def group(key,tvdb,title):
            if key not in groups:
                native=by_tvdb.get(tvdb,{})
                groups[key]={'id':key,'tvdb_id':tvdb,'series_id':native.get('id'),'name':names.get(tvdb,{}).get('title') or native.get('title') or title,'resources':[],'uploads':[],'action':None,'resource_actions':{}}
            return groups[key]
        linked=set()
        for resource in resources:
            key='series:'+str(resource['tvdb_id']) if resource['tvdb_id'] else 'torrent:'+resource['hash']
            value=group(key,resource['tvdb_id'],resource['name'])
            value['resources'].append(resource);value['uploads'].extend(resource['uploads']);linked.update(j['id'] for j in resource['uploads'])
        for job in uploads:
            if job['id'] in linked or job.get('parent_id') in visible_ids:
                continue
            key='series:'+str(job['tvdb_id']) if job.get('tvdb_id') else ('torrent:'+job['torrent_hash'] if job.get('torrent_hash') else 'job:'+job['id'])
            value=group(key,job.get('tvdb_id'),job['dest'].removeprefix('/aliyun/').split('/')[1])
            value['uploads'].extend([job, *[j for j in children[job['id']] if j['id'] not in linked]])
        for action in task_actions(self.data/'uploads.sqlite3'):
            if action['action']=='delete' and action['state']=='done':
                continue
            payload=action['payload'];key=payload['group']
            if key not in groups and action['action'] in {'cancel','delete'}:
                group(key,payload.get('tvdb_id'),payload['name'])
            if key in groups:
                if action['id'].startswith('torrent:'):
                    current=next((r for r in groups[key]['resources'] if 'torrent:'+r['hash']==action['id']),None)
                    old=next((t for t in payload['torrents'] if 'torrent:'+t['hash']==action['id']),None)
                    if current and old and current['added_on']!=old['added_on'] and action['state'] not in {'pending','blocked'}:
                        continue
                status={k:action[k] for k in ('id','action','state','error')}
                status['can_keep'] = bool(payload.get('delete_files') and action['state'] in {'pending', 'blocked'} and action['error'])
                if action['id']==key:groups[key]['action']=status
                else:groups[key]['resource_actions'][action['id']]=status
        for value in groups.values():
            value['file_count']=len(value['uploads'])+sum(sum(1 for f in r['files'] if not f.get('job_id')) for r in value['resources'])
        issue = None
        try:
            heartbeat = json.loads((self.data/'heartbeat.json').read_text())
            found = heartbeat.get('discovery_issue')
            if isinstance(found, dict) and time.time()-heartbeat['at'] < 300:
                issue = {'dest':found['dest'],'message':archive_error(found['error'])}
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return {'items':list(groups.values()), 'discovery_issue':issue}

    def ensure_emby_maintenance(self, session):
        client = self.client('emby')
        client.opener.addheaders = [('X-Emby-Token', session['token'])]
        name = 'Cinema library maintenance'
        keys = [k for k in client.json('GET', '/Auth/Keys')['Items'] if k.get('AppName') == name]
        if not keys:
            client.request('POST', '/Auth/Keys?' + urlencode({'App': name}))
            keys = [k for k in client.json('GET', '/Auth/Keys')['Items'] if k.get('AppName') == name]
        if len(keys) != 1 or not re.fullmatch(r'[a-fA-F0-9]{32}', keys[0].get('AccessToken', '')):
            raise ValueError('Emby 维护密钥未确认，请在 Emby API 密钥中检查重复的 Cinema library maintenance。')
        config = self.config()
        config.update(emby_url=self.origins['emby'], emby_token=keys[0]['AccessToken'])
        private_write(self.state / 'config.json', json.dumps(config))

    def task_action(self, session, body):
        action=body.get('action')
        if (action not in {'pause','resume','cancel','delete','keep-remaining'} or type(body.get('delete_files',False)) is not bool
                or type(body.get('delete_all',False)) is not bool
                or type(body.get('preview',False)) is not bool
                or (body.get('preview') and (action != 'delete' or body.get('delete_files') is not True))
                or (body.get('delete_files') and action != 'delete')
                or (body.get('delete_all') and (action != 'delete' or body.get('delete_files') is not True))):
            raise ValueError('请选择有效的任务操作。')
        key=text(body.get('id'),100)
        with watch_lock(self.data), watch_lock(self.data/'task-controls'):
            actions = {a['id']: a for a in task_actions(self.data/'uploads.sqlite3')}
            previous = actions.get(key)
            if action == 'keep-remaining':
                if not previous or previous['state'] not in {'pending', 'blocked'} or not previous['payload'].get('delete_files'):
                    raise ValueError('请刷新任务列表，删除请求已处理。')
                with closing(sqlite3.connect(self.data/'uploads.sqlite3')) as db, db:
                    db.execute("UPDATE task_actions SET state='done',error=NULL WHERE id=?", (key,))
                return {'message': '已结束删除请求，保留仍存在的文件。'}
            groups=self.tasks(all_jobs=action=='delete')['items']
            group=next((g for g in groups if g['id']==key),None)
            resource=None
            if not group:
                pair=next(((g,r) for g in groups for r in g['resources'] if 'torrent:'+r['hash']==key),None)
                if not pair:raise ValueError('请刷新任务列表，项目已变化。')
                group,resource=pair
            resources=[resource] if resource else group['resources']
            jobs=list({j['id']:j for j in (resource['uploads'] if resource else group['uploads'])}.values())
            torrents = [{k:r[k] for k in ('hash','name','category','save_path','download_path','added_on')}
                        | {'files':[{k:f[k] for k in ('name','size')} for f in r['files']]} for r in resources]
            payload={'name':resource['name'] if resource else group['name'],'group':group['id'],
                     'tvdb_id':group['tvdb_id'] if not resource else None,'series_id':group['series_id'],
                     'torrents':torrents,
                     'jobs':[j['id'] for j in jobs],'delete_files':action=='delete' and body.get('delete_files',False),
                     'delete_all':body.get('delete_all',False)}
            if payload['delete_files']:
                if not jobs and not torrents:
                    raise ValueError('请刷新任务列表，当前没有可删除的文件。')
                if any(type(t['added_on']) is not int or t['added_on'] <= 0 for t in torrents):
                    raise ValueError('下载器尚未返回资源身份，请稍后重新预览。')
                payload['delete_targets'] = sorted((delete_target(j) for j in jobs), key=lambda j:j['id'])
                payload['delete_plan'] = sorted(
                    ({k:j.get(k) for k in ('id','dest','size','sha1')} for j in jobs
                     if payload['delete_all'] and j['state']=='published'), key=lambda j:j['id'])
                scope = hashlib.sha256(json.dumps([
                    key, payload['group'], payload['series_id'], payload['delete_all'],
                    sorted(torrents, key=lambda t:t['hash']), payload['delete_targets'], payload['delete_plan'],
                ], sort_keys=True).encode()).hexdigest()
                if body.get('preview'):
                    paths = {str(Path(t['save_path']) / f['name']) for t in torrents for f in t['files']}
                    paths.update(value for j in jobs for value in (j['source'], j.get('download_source')) if value)
                    return {'scope':scope, 'local':sorted(paths), 'cloud':sorted(j['dest'] for j in payload['delete_plan'])}
                if body.get('scope') != scope:
                    raise TaskScopeChanged('文件清单已变化，请核对新清单后重新确认。')
            qb=self.qb();sonarr=self.client('sonarr')
            if previous and (previous['state']=='pending' or previous['state']=='blocked' and action!='delete'):
                raise ValueError('请等待当前取消或删除完成，后台会自动重试。')
            if payload['delete_all']:
                self.ensure_emby_maintenance(session)
            parent=actions.get(group['id'])
            if resource and parent and parent['state'] in {'pending','blocked'}:
                raise ValueError('请等待项目操作完成，再继续单个资源。')
            if not resource and any(a['state'] in {'pending','blocked'} and a['id'] != key
                                    and a['payload'].get('group') == group['id'] for a in actions.values()):
                raise ValueError('请先完成项目内各资源的操作。')
            if action=='resume' and previous and previous['action'] in {'cancel','delete'} and (previous['state']!='done' or previous['action']=='delete'):
                raise ValueError('请等待取消或删除完成，再操作任务。')
            if resource and group['series_id'] and action in {'cancel','delete'}:
                queued=sonarr.json('GET','/api/v3/queue?page=1&pageSize=1000&includeEpisode=true').get('records',[])
                ids=[q.get('episodeId') or (q.get('episode') or {}).get('id') for q in queued if q.get('downloadId','').lower()==resource['hash'].lower()]
                payload['episode_ids']=[v for v in ids if type(v) is int]
                if payload['episode_ids']:
                    sonarr.json('PUT','/api/v3/episode/monitor',{'episodeIds':payload['episode_ids'],'monitored':False})
            if resource and action=='resume' and previous and previous['payload'].get('episode_ids'):
                series=sonarr.json('GET','/api/v3/series/'+str(group['series_id']))
                self.require_version(sonarr,series)
                payload['episode_ids'] = previous['payload']['episode_ids']
                sonarr.json('PUT','/api/v3/episode/monitor',{'episodeIds':payload['episode_ids'],'monitored':True})
            if group['series_id'] and not resource:
                series=sonarr.json('GET','/api/v3/series/'+str(group['series_id']))
                if action=='resume':
                    original = previous['payload'].get('was_monitored') if previous else None
                    if type(original) is bool:
                        if original:
                            self.require_version(sonarr,series)
                        payload['was_monitored'] = original
                        series['monitored'] = original
                        sonarr.json('PUT','/api/v3/series/'+str(series['id']),series)
                else:
                    payload['was_monitored']=previous['payload'].get('was_monitored',series['monitored']) if previous else series['monitored']
                    series['monitored']=False;self.cancel_request(series['tvdbId'])
                    if action=='delete':
                        self._remove_series({'id':series['id']})
                    else:
                        sonarr.json('PUT','/api/v3/series/'+str(series['id']),series)
            self.data.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(self.data/'uploads.sqlite3')) as db, db:
                create_actions(db)
                db.execute('INSERT OR REPLACE INTO task_actions VALUES (?,?,?,?,?,?)',(key,action,json.dumps(payload),'done' if action in {'pause','resume'} else 'pending',None,time.time()))
            disk_path=self.data/'disk-paused.json'
            disk=json.loads(disk_path.read_text()) if disk_path.exists() else {}
            for r in resources:
                disk.pop(r['hash'],None)
            private_write(disk_path,json.dumps(disk))
            try:
                if action in {'pause','resume'}:
                    for r in resources:
                        qb.request('POST','/api/v2/torrents/'+('start' if action=='resume' else 'stop'),form={'hashes':r['hash']})
            except (ValueError,OSError):
                with closing(sqlite3.connect(self.data/'uploads.sqlite3')) as db, db:
                    db.execute("UPDATE task_actions SET error='task_control_failed' WHERE id=?",(key,))
                raise ValueError('下载器尚未确认操作，请刷新查看实际状态后重试。') from None
            if action=='resume':
                with closing(sqlite3.connect(self.data/'uploads.sqlite3')) as db, db:
                    db.execute("UPDATE task_actions SET state='released' WHERE id=?",(key,))
                    if resource:
                        if parent and parent['action']=='cancel':
                            db.execute("UPDATE task_actions SET state='released' WHERE id=?",(group['id'],))
                    db.executemany("UPDATE jobs SET state='pending',retry_at=0,failures=0,restart_count=0 WHERE id=? AND state='cancelled'",[(j['id'],) for j in jobs])
            return {'message':'已提交删除所有；后台将逐项核实云端原件、清理索引并扫描 Emby，完成前保留此任务。' if payload['delete_all'] else {'pause':'已暂停本项目下载。','resume':'已恢复下载。','cancel':'已请求取消下载，文件保留；正在核对并结束相关上传。','delete':'已请求删除项目；确认上传及入库结束后处理本地文件，云端影片保留。'}[action]}

    def status(self):
        config = self.config()
        def client(service):
            value = self.client(service)
            value.timeout = 10
            return value
        def cloud():
            if not config.get('openlist_token'):
                return False, '尚未连接阿里云盘，请填写 Refresh Token 并连接。'
            try:
                ol = client('openlist')
                ol.opener.addheaders = [('Authorization', config['openlist_token'])]
                item = self.ol(ol, 'POST', '/api/fs/get', {'path': '/aliyun', 'password': ''})
                if not item.get('is_dir') or item.get('provider') != 'AliyundriveOpen':
                    return False, '/aliyun 尚未正确挂载阿里云盘 Open，请重新连接。'
                return True, '阿里云盘连接正常。'
            except (ValueError, OSError):
                return False, '阿里云盘连接失败，请检查授权或在 OpenList 查看挂载。'
        def downloader():
            try:
                qb = self.qb()
                categories = qb.json('GET', '/api/v2/torrents/categories')
                for name, path in [('cinema-movies', '/media/downloads/movies'), ('cinema-tv', '/media/downloads/tv-manual'), ('sonarr-tv', '/media/downloads/tv')]:
                    if categories.get(name, {}).get('savePath', '').rstrip('/') != path:
                        return False, '下载器分类或保存目录未完成，请重新自动配置下载器。'
                clients = client('prowlarr').json('GET', '/api/v1/downloadclient')
                for name, category in [('Cinema qB', 'cinema-movies'), ('Cinema qB TV', 'cinema-tv')]:
                    found = [c for c in clients if c['name'] == name and c.get('enable') and c.get('implementation') == 'QBittorrent'
                             and not c.get('categories') and any(f['name'] == 'category' and f.get('value') == category for f in c['fields'])]
                    if len(found) != 1:
                        return False, '下载器的电影或独立剧集连接未完成，请重新自动配置下载器。'
                return True, '下载器登录与电影、独立剧集、追更分类正常。'
            except (ValueError, OSError, KeyError):
                return False, '下载器尚未配置或登录失败，请检查账号并重新自动配置。'
        def indexers():
            try:
                prowlarr = client('prowlarr')
                enabled = [i for i in prowlarr.json('GET', '/api/v1/indexer') if i.get('enable')]
                health = prowlarr.json('GET', '/api/v1/health')
                warnings = any('Indexer' in h.get('source', '') for h in health)
                if not enabled:
                    return False, '尚未添加并启用搜索站点，请点击配置 Indexer。', 0
                return not warnings, (f'{len(enabled)} 个站点已启用，但 Prowlarr 报告站点异常，请逐个 Test。' if warnings else f'{len(enabled)} 个搜索站点已启用。'), len(enabled)
            except (ValueError, OSError):
                return False, 'Prowlarr 连接失败，请检查服务后配置搜索站点。', 0
        def television():
            try:
                sonarr, prowlarr = client('sonarr'), client('prowlarr')
                roots = sonarr.json('GET', '/api/v3/rootfolder')
                downloads = sonarr.json('GET', '/api/v3/downloadclient')
                apps = prowlarr.json('GET', '/api/v1/applications')
                synced = sonarr.json('GET', '/api/v3/indexer')
                problems = []
                if not any(r.get('path', '').rstrip('/') == '/media/library/tv' for r in roots): problems.append('电视剧目录')
                if not any(d.get('name') == 'Cinema qB' and d.get('enable') for d in downloads): problems.append('Sonarr 下载器')
                if not any(a.get('name') == 'Cinema Sonarr' and a.get('syncLevel') == 'fullSync' for a in apps): problems.append('Prowlarr 同步连接')
                if not any(i.get('enableRss') or i.get('enableAutomaticSearch') or i.get('enableInteractiveSearch') for i in synced): problems.append('同步后的追剧站点')
                return not problems, ('追剧配置未完成：' + '、'.join(problems) + '。请自动配置，并在 Prowlarr 检查同步。' if problems else '追剧目录、下载器与站点同步已配置。')
            except (ValueError, OSError):
                return False, 'Sonarr 或同步连接不可用，请检查服务并重新自动配置。'
        checks = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {name: pool.submit(work) for name, work in [('cloud', cloud), ('downloader', downloader), ('indexers', indexers), ('television', television)]}
            for name, future in futures.items():
                value = future.result()
                checks[name] = {'complete': value[0], 'message': value[1]}
                if name == 'indexers': count = value[2]
        try:
            settings = self.settings()
            days = settings.get('retention_days')
            valid = type(days) is int and 0 <= days <= 3650
        except (ValueError, OSError):
            settings, valid = {'retention_days': 7}, False
        checks['retention'] = {'complete': valid, 'message': ('保留时间已设置。' if valid else '保留时间无效，请填写 0–3650 天并保存。')}
        initialized = (self.state / 'emby-defaults.applied').is_file()
        checks['metadata'] = {'complete': initialized, 'message': 'Emby 中文默认配置已初始化，后续可自行调整。' if initialized else 'Emby 默认配置未完成，请点击“应用推荐刮削设置”重试并查看原因。'}
        try:
            heartbeat = json.loads((self.data / 'heartbeat.json').read_text())
            fresh = time.time() - heartbeat['at'] < 300
            alive = fresh and time.time() - heartbeat.get('activity',{}).get('at',0) < 600
            detail = '上传服务正常。' if alive else '上传服务超过 5 分钟未更新或主循环长时间没有进展，请检查服务。'
            if heartbeat.get('disk_paused'):
                detail += f" 空间保护已暂停 {heartbeat['disk_paused']} 个下载；空间恢复后自动继续。"
            checks['uploader'] = {'complete': alive and not heartbeat.get('error'), 'message': detail + (' 空间检查暂不可用。' if heartbeat.get('error') else '')}
        except (OSError, ValueError, KeyError, TypeError):
            checks['uploader'] = {'complete': False, 'message': '尚未收到上传服务心跳，请启动 uploader 服务或检查配置。'}
        if not valid: settings = {'retention_days': 7}
        cleanup = {'pending': 0, 'done': 0, 'last': None}
        dbpath = self.state / 'data/uploads.sqlite3'
        if dbpath.exists():
            with closing(sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True)) as db, db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='cleanups'").fetchone():
                    cleanup.update(dict(db.execute('SELECT state, count(*) FROM cleanups GROUP BY state')))
                    cleanup['last'] = db.execute('SELECT max(finished_at) FROM cleanups').fetchone()[0]
                    reasons = {'cleanup_not_archived':'等待视频及字幕全部发布', 'cleanup_unsupported_disc':'原盘保留本地，暂不自动清理',
                               'cleanup_unarchived_media':'资源含未归档的音轨、图形字幕或其他格式视频，保留本地；需要时在任务页手动删除',
                               'cleanup_subtitle_mapping_missing':'字幕关联未能确认，保留本地', 'cleanup_waiting_for_stop':'等待下载器停止做种',
                               'cleanup_waiting_for_removal':'等待下载器删除任务', 'cleanup_waiting_for_files':'等待导入文件删除完成',
                               'remote_size_mismatch':'云端文件校验未通过，保留本地', 'remote_hash_mismatch':'云端校验不一致，保留本地'}
                    if 'reason' in {r[1] for r in db.execute('PRAGMA table_info(cleanups)')}:
                        cleanup['reasons'] = [reasons.get(r[0],'安全复核尚未通过，保留本地并自动复查') for r in db.execute("SELECT DISTINCT reason FROM cleanups WHERE state!='done' AND reason IS NOT NULL LIMIT 5")]
        return {'settings': settings, 'configured': checks['cloud']['complete'], 'cleanup': cleanup,
                'credentials': {'openlist': not checks['cloud']['complete'], 'qb': not checks['downloader']['complete']},
                'indexers': count, 'prowlarr_ok': checks['indexers']['complete'],
                'sonarr_ok': checks['television']['complete'], 'checks': checks}


class Handler(BaseHTTPRequestHandler):
    server_version = "Cinema"
    # Client socket timeout: a stalled or partial request must not pin a worker thread forever.
    timeout = 30

    def log_message(self, *args):
        pass  # Paths and API exceptions can contain private search terms or credentials.

    def reply(self, value, status=200, cookie=None, mime="application/json; charset=utf-8"):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header('Set-Cookie', 'cinema=; Max-Age=0; Path=/discover/; HttpOnly; SameSite=Strict' + secure)
            self.send_header("Set-Cookie", cookie + "; Path=/; HttpOnly; SameSite=Strict" + secure)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        app = self.server.app
        try:
            url = urlsplit(self.path)
            path = url.path.removeprefix("/discover")
            if self.command == "GET" and path in {"/", "/index.html", "/app.js", "/theme.js", "/paint.js", "/style.css"}:
                name = "index.html" if path == "/" else path[1:]
                mime = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[name.split(".")[-1]]
                return self.reply((STATIC / name).read_bytes(), mime=mime + "; charset=utf-8")
            if not path.startswith("/api/"):
                return self.reply({"error": "页面不存在。"}, 404)
            body = {}
            if self.command == "POST":
                origin = self.headers.get("Origin", "")
                parsed = urlsplit(origin)
                if parsed.netloc != self.headers.get("Host") or parsed.scheme not in {"http", "https"}:
                    return self.reply({"error": "请求来源无效，请刷新页面。"}, 403)
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("请求格式无效。")
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 16384:
                    raise ValueError("请求大小无效。")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("请求格式无效。")
            if path == "/api/login" and self.command == "POST":
                peer = self.client_address[0]
                try:
                    trusted = ipaddress.ip_address(peer).is_loopback
                    trusted |= peer in socket.gethostbyname_ex('gateway')[2]
                    if trusted:
                        forwarded = self.headers.get('X-Forwarded-For', '').split(',')[-1].strip()
                        if forwarded:
                            peer = str(ipaddress.ip_address(forwarded))
                except (ValueError, OSError):
                    pass
                sid, session = app.login(body, peer)
                return self.reply({"csrf": session["csrf"]}, cookie="cinema=" + sid)
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            sid = cookies["cinema"].value if "cinema" in cookies else ""
            if path == "/api/logout" and self.command == "POST":
                # Logout must work while Emby is down, so it skips the remote admin check.
                if not app.logout(sid, self.headers.get("X-CSRF-Token", "")):
                    return self.reply({"error": "会话校验失败，请刷新页面。"}, 403)
                return self.reply({}, cookie="cinema=; Max-Age=0")
            session = app.authenticate(sid)
            if self.command == "POST" and not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
                return self.reply({"error": "会话校验失败，请刷新页面。"}, 403)
            query = parse_qs(url.query)
            if self.command == "GET":
                if path == "/api/session":
                    return self.reply({"csrf": session["csrf"], "username": session["username"]})
                if path == '/api/admin-check':
                    return self.reply({})
                if path == "/api/metrics":
                    return self.reply(app.metrics.read())
                if path == "/api/status":
                    return self.reply(app.status())
                if path == "/api/search":
                    return self.reply(app.search(session, query.get("q", [""])[0], query.get("kind", ["movie"])[0]))
                if path == '/api/recommendations':
                    return self.reply(app.recommendations())
                if path == "/api/series":
                    values = app.client("sonarr").json("GET", "/api/v3/series")
                    removed_tags = {t['id'] for t in app.client('sonarr').json('GET', '/api/v3/tag') if t['label'] == 'cinema-unfollowed'}
                    values = [v for v in values if not removed_tags.intersection(v.get('tags', []))]
                    session.setdefault('series', {}).update({str(v['tvdbId']): v for v in values})
                    localized = app.localize(session, values)
                    profiles = app.client('sonarr').json('GET', '/api/v3/releaseprofile')
                    versions = {}
                    for v in values:
                        try:
                            versions[v['tvdbId']] = {'valid': True, 'fields': app.require_version(app.client('sonarr'), v, profiles)['fields']}
                        except ValueError:
                            versions[v['tvdbId']] = {'valid': False}
                    pending = read_requests(app.data)
                    return self.reply({"items": [{**{k: v.get(k) for k in ("id", "tvdbId", "year", "monitored", "nextAiring", "statistics", "seasons")}, **localized[v['tvdbId']], 'version': versions[v['tvdbId']], 'archived': len(archived_keys(app.data/'uploads.sqlite3', v['tvdbId'])), 'preparing': str(v['tvdbId']) in pending} for v in values]})
                if path == "/api/tasks":
                    return self.reply(app.tasks(session))
                if path == '/api/playbacks':
                    return self.reply(app.playbacks(session))
            else:
                with app.mutation:
                    if path == "/api/settings":
                        days = integer(body.get("retention_days"), 0, 3650)
                        with watch_lock(app.data):
                            private_write(app.state / "settings.json", json.dumps({"retention_days": days}))
                        return self.reply({"message": "清理设置已保存。"})
                    if path == "/api/configure":
                        return self.reply(app.configure(session, body))
                    if path == '/api/preview-version':
                        return self.reply(app.preview_release(session, body))
                    if path == '/api/download-release':
                        return self.reply(app.download_release(session, body))
                    if path == "/api/follow":
                        return self.reply(app.follow(session, body))
                    if path == '/api/episodes':
                        return self.reply(app.episodes(session, body))
                    if path == '/api/download-episodes':
                        return self.reply(app.download_episodes(body))
                    if path == '/api/retry-upload':
                        return self.reply(app.retry_upload(body))
                    if path == '/api/task-action':
                        return self.reply(app.task_action(session,body))
                    if path == '/api/watch-session':
                        return self.reply(app.watch_session(session))
                    if path == '/api/metadata-defaults':
                        return self.reply(app.metadata_defaults(session))
                    if path == "/api/remove-series":
                        return self.reply(app.remove_series(body))
                    if path == "/api/monitor":
                        return self.reply(app.monitor_series(body))
                    if path == "/api/download":
                        return self.reply(app.download_release(session, {'release_id': body.get('id')}, 'movie'))
            self.reply({"error": "接口不存在。"}, 404)
        except TaskScopeChanged as error:
            self.reply({'error':str(error)}, 409)
        except SessionCheckUnavailable:
            self.reply({'error':'Emby 暂时无法验证会话，请稍后重试。'}, 503)
        except PermissionError:
            if path == '/api/admin-check':
                self.send_response(302)
                self.send_header('Location', '/discover/')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length','0')
                self.end_headers()
                return
            self.reply({"error": "会话已失效，请使用 Emby 管理员账号登录。"}, 401)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            # Never echo upstream payloads: they may embed tokens or signed URLs.
            message = "操作未完成，请检查输入、服务登录或配置后重试。"
            error = sys.exception()
            if isinstance(error, ValueError) and str(error).startswith(("请", "已", "搜索", "结果", "下载器", "云盘", "VPS", "/aliyun", "Sonarr", "登录", "会话", "追更", "季数")):
                message = str(error)
            self.reply({"error": message}, 400)


if __name__ == "__main__":
    os.umask(0o077)
    server = ThreadingHTTPServer(("0.0.0.0", 8097), Handler)
    server.app = App()
    server.serve_forever()
