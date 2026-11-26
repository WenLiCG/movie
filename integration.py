"""Connect this deployment's native APIs; no third-party Python packages."""
import copy
import http.cookiejar
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from archive import cloud_mount


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class APIError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


SERVICE_HOSTS = {"emby": "Emby", "openlist": "OpenList", "qbittorrent": "qBittorrent", "sonarr": "Sonarr", "prowlarr": "Prowlarr"}
SERVICE_PORTS = {8096: "Emby", 5244: "OpenList", 8080: "qBittorrent", 8989: "Sonarr", 9696: "Prowlarr"}


def service_name(base):
    """Name the bundled app behind a base URL so outage messages say which one to check."""
    parts = urllib.parse.urlsplit(base)
    try:
        port = parts.port
    except ValueError:
        port = None
    return SERVICE_HOSTS.get(parts.hostname or "") or SERVICE_PORTS.get(port) or ""


class Client:
    timeout = 90
    def __init__(self, base, key=None):
        self.base = base.rstrip("/")
        self.key = key
        self.service = service_name(self.base)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method, path, body=None, form=None):
        headers = {"Accept": "application/json", "Referer": self.base + "/"}
        if self.key:
            headers["X-Api-Key"] = self.key
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                result = response.read(8 * 1024 * 1024 + 1)
                if len(result) > 8 * 1024 * 1024:
                    raise ValueError("API 响应超出限制。")
                return result
        except urllib.error.HTTPError as error:
            error.close()
            raise APIError(error.code, f"{path.split('?')[0]} 返回 HTTP {error.code}；请检查应用设置和登录。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            # Shared by the portal and `install.sh setup`; name the app, never the URL or credentials.
            name = f" {self.service} " if self.service else ""
            raise ValueError(f"本地应用{name}暂时无法连接或响应超时；请稍后重试，必要时用 docker compose ps 检查服务状态。") from None

    def json(self, method, path, body=None, form=None):
        raw = self.request(method, path, body, form)
        try:
            return json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            raise ValueError(f"{path.split('?')[0]} 未返回有效 JSON。") from None


def read_api_key(path):
    try:
        key = ET.parse(path).getroot().findtext("ApiKey", "")
    except (OSError, ET.ParseError):
        raise ValueError(f"{path.parent.name} 配置未生成；请先启动并初始化对应服务。") from None
    if not re.fullmatch(r"[a-fA-F0-9]{32}", key):
        raise ValueError(f"{path.parent.name} API Key 格式无效。")
    return key


def verify_emby_accounts(username, password):
    """Inspect hidden accounts too; the temporary administrator session is never saved."""
    if not username or not password:
        raise ValueError("开启公网检查需要 Emby 管理员用户名和非空密码。")
    client = Client("http://127.0.0.1:8096")
    client.opener.addheaders = [("X-Emby-Authorization",
        'Emby Client="CinemaSetup", Device="Debian", DeviceId="cinema-public-check", Version="1"')]
    result = client.json("POST", "/Users/AuthenticateByName", {"Username": username, "Pw": password})
    token = result.get("AccessToken", "")
    if not isinstance(token, str) or not token or any(ord(c) < 32 for c in token):
        raise ValueError("Emby 未返回有效的检查会话。")
    client.opener.addheaders.append(("X-Emby-Token", token))
    try:
        if (result.get("User", {}).get("Policy") or {}).get("IsAdministrator") is not True:
            raise ValueError("需要 Emby 管理员账号以检查全部用户。")
        users = client.json("GET", "/Users")
        if not isinstance(users, list) or not users:
            raise ValueError("无法核实 Emby 用户密码状态，停止开启公网。")
        for user in users:
            policy = user.get("Policy") or {}
            if policy.get("IsDisabled") is True:
                continue
            # A reverse proxy can be classified as local, so local password bypass is also rejected.
            if user.get("HasConfiguredPassword") is not True or user.get("HasPassword") is not True:
                raise ValueError("Emby 存在未设置密码或允许免密的启用账号，请检查包括隐藏账号在内的所有用户。")
    finally:
        client.request("POST", "/Sessions/Logout")


def emby_defaults(client, mounts=()):
    """Apply the cloud-library preset without replacing existing item metadata."""
    libraries = client.json('GET', '/Library/VirtualFolders/Query')['Items']
    plans = []
    optional_defaults = dict(PreferredMetadataLanguage='zh', MetadataCountryCode='CN')
    defaults = dict(EnableRealtimeMonitor=True, SaveLocalMetadata=False, MetadataSavers=[],
                    SaveLocalThumbnailSets=False, DownloadImagesInAdvance=True,
                    EnableChapterImageExtraction=False, ExtractChapterImagesDuringLibraryScan=False,
                    EnableMarkerDetection=False, EnableMarkerDetectionDuringLibraryScan=False)
    subtitle_defaults = dict(SubtitleDownloadLanguages=['zh-CN', 'zh-TW'], SubtitleDownloadMaxAgeDays=0,
                             RequirePerfectSubtitleMatch=False, SaveSubtitlesWithMedia=False,
                             SkipSubtitlesIfEmbeddedSubtitlesPresent=False, SkipSubtitlesIfAudioTrackMatches=False)
    for name, kind, path in [('电影', 'movies', '/library/aliyun/Movies'), ('电视剧', 'tvshows', '/library/aliyun/TV')]:
        matches = [lib for lib in libraries if path in [p.rstrip('/') for p in lib['Locations']]]
        allowed = {path, *('/library' + cloud_mount(m) + '/' + path.rsplit('/', 1)[1] for m in mounts)}
        if len(matches) > 1 or (matches and (matches[0]['CollectionType'] != kind or
                any(p.rstrip('/') not in allowed for p in matches[0]['Locations']))):
            raise ValueError('影院媒体库目录或类型冲突，请先在 Emby 检查。')
        existing = matches[0] if matches else None
        if not existing and any(lib['Name'] == name for lib in libraries):
            raise ValueError('同名媒体库使用了其他目录，请先在 Emby 检查。')
        schema = client.json('GET', '/Libraries/AvailableOptions?LibraryContentType=' + kind + '&IsNewLibrary=false')
        options = copy.deepcopy(schema['DefaultLibraryOptions'])
        options.update(copy.deepcopy(existing['LibraryOptions']) if existing else {})
        if not defaults.keys() <= options.keys():
            raise ValueError('Emby 媒体库字段与核查版本不符，停止应用推荐设置。')
        options.update(copy.deepcopy(defaults))
        options.update({key: value for key, value in optional_defaults.items() if key in options})
        if not options.get('SubtitleDownloadLanguages'):
            if not subtitle_defaults.keys() <= options.keys():
                raise ValueError('Emby 字幕字段与核查版本不符，停止应用推荐设置。')
            # The cloud index is read-only and cannot supply a real media-file hash.
            options.update(copy.deepcopy(subtitle_defaults))
            options['SubtitleFetcherOrder'] = [f['Name'] for f in schema.get('SubtitleFetchers', []) if f.get('DefaultEnabled')]
        types = {item['Type']: item for item in options.get('TypeOptions', [])}
        for item in schema['TypeOptions']:
            target = types.setdefault(item['Type'], {'Type': item['Type']})
            for key in ('MetadataFetchers', 'ImageFetchers'):
                available = item[key]
                if not any(f['Name'] == 'TheMovieDb' for f in available):
                    raise ValueError('Emby 缺少 TMDB 刮削源，请检查内置插件。')
                enabled = [f['Name'] for f in available if (f['DefaultEnabled'] or f['Name'] == 'TheMovieDb') and f['Name'] != 'Image Capture']
                enabled.sort(key=lambda value: value != 'TheMovieDb')
                target[key] = enabled
                target[key[:-1] + 'Order'] = enabled[:]
            target['ImageOptions'] = copy.deepcopy(item['DefaultImageOptions'])
        options['TypeOptions'] = list(types.values())
        plans.append((existing, name, kind, path, options))
    for existing, name, kind, path, options in plans:
        if existing:
            client.request('POST', '/Library/VirtualFolders/LibraryOptions', {'Id': existing['ItemId'], 'LibraryOptions': options})
        else:
            client.request('POST', '/Library/VirtualFolders', {'Name': name, 'CollectionType': kind,
                           'Paths': [path], 'RefreshLibrary': False, 'LibraryOptions': options})
    libraries = client.json('GET', '/Library/VirtualFolders/Query')['Items']
    for _, _, _, path, options in plans:
        library = next((lib for lib in libraries if path in [p.rstrip('/') for p in lib['Locations']]), None)
        if library is None:
            raise ValueError('Emby 尚未列出媒体库，请稍后重新应用。')
        expected = {key: options[key] for key in (*defaults, *subtitle_defaults, 'SubtitleFetcherOrder', 'TypeOptions')}
        expected.update({key: options[key] for key in optional_defaults if key in options})
        if any(library['LibraryOptions'].get(key) != value for key, value in expected.items()):
            raise ValueError('Emby 尚未确认推荐设置，请重新应用。')
        client.request('POST', '/Items/' + library['ItemId'] + '/Refresh?Recursive=true&MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=false&ReplaceAllImages=false')


def activate_cloud_library(client, mount_path, library_dir=Path('/library')):
    """Append to the two existing managed libraries. A retry resumes any partial addition."""
    mount = cloud_mount(mount_path)
    libraries = client.json('GET', '/Library/VirtualFolders/Query')['Items']
    additions = []
    for kind, folder in [('movies', 'Movies'), ('tvshows', 'TV')]:
        target = '/library' + mount + '/' + folder
        legacy = '/library/aliyun/' + folder
        owners = [lib for lib in libraries if legacy in [p.rstrip('/') for p in lib['Locations']]]
        if len(owners) != 1 or owners[0].get('CollectionType') != kind:
            raise ValueError('请先在影院设置应用 Emby 推荐设置，确认电影和电视剧媒体库后重试。')
        owner = owners[0]
        if any(lib is not owner and target in [p.rstrip('/') for p in lib['Locations']] for lib in libraries):
            raise ValueError('目标目录已用于其他 Emby 媒体库，请选择其他挂载。')
        if target not in [p.rstrip('/') for p in owner['Locations']]:
            additions.append((owner['ItemId'], target))
    local_root = library_dir / mount[1:]
    for directory in (local_root, local_root / 'Movies', local_root / 'TV'):
        if directory.is_symlink():
            raise ValueError('影院索引目录不可为符号链接，请检查挂载路径。')
        directory.mkdir(exist_ok=True)
    for item_id, target in additions:
        client.request('POST', '/Library/VirtualFolders/Paths',
                       {'Id': item_id, 'Path': target, 'RefreshLibrary': False})
    if additions:
        updated = client.json('GET', '/Library/VirtualFolders/Query')['Items']
        if any(not any(lib.get('ItemId') == item_id and target in [p.rstrip('/') for p in lib['Locations']]
                       for lib in updated) for item_id, target in additions):
            raise ValueError('Emby 尚未确认新增片库路径，请重试。')


def provider(client, api, resource, implementation, name, values, **settings):
    """Use the running version's schema, preserving fields outside our contract."""
    base = api + "/" + resource
    existing = [item for item in client.json("GET", base) if item.get("name") == name]
    if len(existing) > 1:
        raise ValueError(f"重复的 {name} 配置，请先在应用中整理。")
    if existing:
        item = copy.deepcopy(existing[0])
        if item.get("implementation") != implementation:
            raise ValueError(f"{name} 已被其他类型配置占用。")
    else:
        schemas = client.json("GET", base + "/schema")
        matches = [item for item in schemas if item.get("implementation") == implementation]
        if len(matches) != 1:
            raise ValueError(f"当前应用缺少预期的 {implementation} 配置模板。")
        item = copy.deepcopy(matches[0])
        item.pop("id", None)
    fields = {field["name"]: field for field in item["fields"]}
    if not values.keys() <= fields.keys():
        raise ValueError(f"{implementation} 配置字段与核查版本不符；停止自动接线。")
    for key, value in values.items():
        fields[key]["value"] = value
    item.update(name=name, **settings)
    client.json("POST", base + "/test", item)
    if existing:
        return client.json("PUT", base + "/" + str(item["id"]), item)
    return client.json("POST", base, item)


def ensure_categories(qb):
    wanted = {"cinema-movies": "/media/downloads/movies", "cinema-tv": "/media/downloads/tv-manual", "sonarr-tv": "/media/downloads/tv"}
    existing = qb.json("GET", "/api/v2/torrents/categories")
    # Validate all collisions before creating anything or changing preferences.
    for name, path in wanted.items():
        if name in existing and existing[name].get("savePath", "").rstrip("/") != path:
            raise ValueError(f"qB 分类 {name} 指向其他目录；不会移动已有任务，请先检查。")
    for name, path in wanted.items():
        if name not in existing:
            qb.request("POST", "/api/v2/torrents/createCategory", form={"category": name, "savePath": path})


def update_config(client, base, changes):
    item = client.json("GET", base)
    if not changes.keys() <= item.keys():
        raise ValueError("应用设置字段与核查版本不符；停止自动接线。")
    item.update(changes)
    return client.json("PUT", base + "/" + str(item["id"]), item)


def connect_services(token, username, password, prowlarr_key, sonarr_key, origins=None, mount_path='/aliyun'):
    mount_path = cloud_mount(mount_path)
    origins = origins or {}
    prowlarr = Client(origins.get("prowlarr", "http://127.0.0.1:9696/search"), prowlarr_key)
    sonarr = Client(origins.get("sonarr", "http://127.0.0.1:8989/sonarr"), sonarr_key)
    qb = Client(origins.get("qb", "http://127.0.0.1:8080"))
    openlist = Client(origins.get("openlist", "http://127.0.0.1:5244/openlist"))
    # Keep service secrets in headers; never in URLs, command arguments or output.
    openlist.opener.addheaders = [("Authorization", token)]
    remote = openlist.json("POST", "/api/fs/get", {"path": mount_path, "password": ""})
    if not isinstance(remote, dict) or remote.get("code") != 200 or not (remote.get("data") or {}).get("is_dir"):
        raise ValueError(f"OpenList Token 无效，或云盘路径 {mount_path} 不可用。")
    if remote["data"].get("provider") != "AliyundriveOpen":
        raise ValueError(f"{mount_path} 必须使用阿里云盘 Open 驱动；不会向其他存储上传。")
    prowlarr.json("GET", "/api/v1/system/status")
    sonarr.json("GET", "/api/v3/system/status")
    login = qb.request("POST", "/api/v2/auth/login", form={"username": username, "password": password})
    if login.strip() not in (b"", b"Ok.") or not any(
            cookie.value and re.fullmatch(r"SID|QBT_SID_[0-9]+", cookie.name) for cookie in qb.cookies):
        raise ValueError("qB 登录失败；请输入当前网页登录密码。")
    # This package owns a new qB instance; category collision guards prevent adopting unrelated data.
    ensure_categories(qb)
    qb.request("POST", "/api/v2/app/setPreferences", form={"json": json.dumps({
        "save_path": "/media/downloads", "temp_path_enabled": True,
        "temp_path": "/media/incomplete", "incomplete_files_ext": True,
        "auto_tmm_enabled": True,
    })})
    qb_fields = {"host": "qbittorrent", "port": 8080, "useSsl": False, "urlBase": "",
                 "username": username, "password": password}
    provider(prowlarr, "/api/v1", "downloadclient", "QBittorrent", "Cinema qB",
             {**qb_fields, "category": "cinema-movies"}, enable=True, categories=[])
    provider(prowlarr, "/api/v1", "downloadclient", "QBittorrent", "Cinema qB TV",
             {**qb_fields, "category": "cinema-tv"}, enable=True, priority=50, categories=[])
    provider(sonarr, "/api/v3", "downloadclient", "QBittorrent", "Cinema qB",
             {**qb_fields, "tvCategory": "sonarr-tv", "tvImportedCategory": ""},
             enable=True, removeCompletedDownloads=False, removeFailedDownloads=False)
    roots = sonarr.json("GET", "/api/v3/rootfolder")
    if not any(item.get("path", "").rstrip("/") == "/media/library/tv" for item in roots):
        sonarr.json("POST", "/api/v3/rootfolder", {"path": "/media/library/tv"})
    update_config(sonarr, "/api/v3/config/mediamanagement", {"copyUsingHardlinks": True,
                  "importExtraFiles": True, "extraFileExtensions": "srt,ass,ssa", "minimumFreeSpaceWhenImporting": 5120})
    update_config(sonarr, "/api/v3/config/downloadclient", {"enableCompletedDownloadHandling": True})
    provider(prowlarr, "/api/v1", "applications", "Sonarr", "Cinema Sonarr", {
        "prowlarrUrl": "http://prowlarr:9696/search", "baseUrl": "http://sonarr:8989/sonarr", "apiKey": sonarr_key,
    }, syncLevel="fullSync")

