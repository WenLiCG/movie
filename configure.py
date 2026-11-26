"""Prepare a fresh Debian deployment. Uses only Python's standard library."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
DIRECTORIES = (
    "state", "state/library", "state/library/aliyun",
    "state/relay", "state/relay-token", "state/caddy", "media", "media/downloads",
    "state/emby", "state/openlist", "state/openlist-runtime", "state/prowlarr", "state/sonarr",
    "state/uploader", "state/uploader/data",
    "state/qbittorrent", "state/relay/lib", "state/relay/lib/playback", "state/caddy/data", "state/caddy/config",
    "state/library/aliyun/Movies", "state/library/aliyun/TV",
    "media/downloads/movies", "media/downloads/tv", "media/downloads/tv-manual", "media/incomplete",
    "media/library", "media/library/tv",
)


def domain(value):
    value = value.strip().lower().encode("idna").decode("ascii")
    labels = value.split(".")
    if len(value) > 253 or len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ) or labels[-1].isdigit():
        raise ValueError("请输入域名，不要包含协议、端口、路径或 IP。")
    return value


def prepare(root, movie):
    movie = domain(movie)
    targets = [root / ".env", root / "state/relay/config.yml"]
    if any(p.exists() for p in targets):
        raise ValueError("已有配置：为避免覆盖，停止初始化。修改现有文件即可。")
    # Fail before touching an existing install, even if its config was moved.
    if any((root / name).exists() for name in ("state", "media")):
        raise ValueError("发现已有 state/media 目录，请使用新的部署目录。")
    for name in DIRECTORIES:
        p = root / name
        p.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            p.chmod(0o750)
            if os.geteuid() == 0:
                os.chown(p, 1000, 1000)
    config = (root / "relay/config.example.yml").read_text(encoding="utf-8")
    config = config.replace("__RELAY_SECRET__", secrets.token_urlsafe(32))
    targets[0].write_text(f"MOVIE_DOMAIN={movie}\n", encoding="utf-8")
    targets[1].write_text(config, encoding="utf-8")
    if os.name == "posix":
        for p in targets:
            p.chmod(0o600)
        if os.geteuid() == 0:
            os.chown(targets[1], 1000, 1000)


def set_token(root, token):
    if not token or any(c.isspace() or ord(c) < 32 for c in token):
        raise ValueError("Token 不可为空或含空白。")
    path = root / "state/relay/config.yml"
    config = path.read_text(encoding="utf-8")
    marker = '  token: "__OPENLIST_TOKEN__"'
    if config.count(marker) != 1:
        raise ValueError("Token 已配置或模板已修改；不会覆盖，请手动编辑配置。")
    path.write_text(config.replace(marker, "  token: " + json.dumps(token)), encoding="utf-8")
    write_relay_token(root / 'state/relay-token', token)


def private_write(path, content):
    """Replace one owned secret file atomically without a world-readable interval."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".cinema-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix" and os.geteuid() == 0:
            os.chown(name, 1000, 1000)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare_relay_token_directory(directory):
    if directory.is_symlink():
        raise ValueError('云盘令牌目录不可为符号链接。')
    directory.mkdir(mode=0o750, exist_ok=True)
    if os.name == 'posix' and os.geteuid() == 0:
        directory.chmod(0o750)
        os.chown(directory, 1000, 1000)


def write_relay_token(directory, token):
    prepare_relay_token_directory(directory)
    private_write(directory / 'openlist-token', token)


def migrate_relay_token(root):
    directory = root / 'state/relay-token'
    old = root / 'state/uploader/openlist-token'
    current = directory / 'openlist-token'
    if directory.is_symlink() or old.is_symlink() or current.is_symlink():
        raise ValueError('云盘令牌路径不可为符号链接。')
    prepare_relay_token_directory(directory)
    if current.is_file():
        return
    if old.is_file():
        token = old.read_text(encoding='utf-8').strip()
        if not token or any(c.isspace() for c in token):
            raise ValueError('现有云盘令牌无效，请重新配置。')
        write_relay_token(directory, token)


def connect(root):
    from archive import cloud_mount
    from integration import connect_services, read_api_key
    existing_path = root / "state/uploader/config.json"
    existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.exists() else {}
    mount = cloud_mount(existing.get('cloud_mount', '/aliyun'))
    print(f"请先在网页完成 OpenList 挂载 {mount}、Prowlarr/Sonarr 登录设置和 qB 密码设置。")
    token = getpass.getpass("OpenList 管理 Token（不回显；留空沿用已保存值）: ").strip()
    token = token or existing.get("openlist_token", "")
    if not token or any(c.isspace() or ord(c) < 32 for c in token):
        raise ValueError("需要有效的 OpenList Token。")
    username = input(f"qB 用户名 [{existing.get('qb_username', 'admin')}]: ").strip()
    username = username or existing.get("qb_username", "admin")
    password = getpass.getpass("qB 密码（不回显；留空沿用已保存值）: ")
    password = password or existing.get("qb_password", "")
    if not username or not password:
        raise ValueError("需要 qB 用户名和密码。")
    prowlarr_key = read_api_key(root / "state/prowlarr/config.xml")
    sonarr_key = read_api_key(root / "state/sonarr/config.xml")
    connect_services(token, username, password, prowlarr_key, sonarr_key, mount_path=mount)
    config_path = root / "state/relay/config.yml"
    config = config_path.read_text(encoding="utf-8")
    if len(re.findall(r'^  token: .*$', config, re.MULTILINE)) != 1:
        raise ValueError("relay 配置中的 Token 行不唯一，请检查后再运行 setup。")
    config = re.sub(r'^  token: .*$', lambda _: "  token: " + json.dumps(token), config, flags=re.MULTILINE)
    private_write(config_path, config)
    write_relay_token(root / 'state/relay-token', token)
    private_write(existing_path, json.dumps({
        **existing,
        "openlist_url": "http://openlist:5244/openlist", "openlist_token": token,
        "qb_url": "http://qbittorrent:8080", "qb_username": username, "qb_password": password,
        "sonarr_url": "http://sonarr:8989/sonarr", "sonarr_api_key": sonarr_key,
    }, ensure_ascii=False, indent=2) + "\n")
    print("下载器、分类、追剧连接和上传配置已保存。PT 凭据仍在 Prowlarr 中添加。")


def check_public(root):
    import xml.etree.ElementTree as ET
    from integration import Client, read_api_key, verify_emby_accounts
    for name, port, prefix, api in (("prowlarr", 9696, "search", "v1"), ("sonarr", 8989, "sonarr", "v3")):
        client = Client(f"http://127.0.0.1:{port}/{prefix}", read_api_key(root / f"state/{name}/config.xml"))
        host = client.json("GET", f"/api/{api}/config/host")
        if (host.get("authenticationMethod", "").lower() != "forms" or
                host.get("authenticationRequired", "").lower() != "enabled" or host.get("trustedNetworks", "")):
            raise ValueError(f"{name} 必须启用 Forms 登录认证，Authentication Required=Enabled，且无免认证 Trusted Networks。")
    openlist = Client("http://127.0.0.1:5244/openlist")
    status = openlist.json("GET", "/api/public/init_status")
    if (status.get("code") != 200 or (status.get("data") or {}).get("initialized") is not True or
            openlist.json("GET", "/api/me").get("code") not in (401, 403)):
        raise ValueError("OpenList 必须先创建管理员并禁用游客访问。")
    try:
        Client("http://127.0.0.1:8080").request("GET", "/api/v2/app/version")
    except ValueError as error:
        if not any(f"HTTP {code}" in str(error) for code in (401, 403)):
            raise
    else:
        raise ValueError("qB 必须启用登录认证，关闭本地地址和子网免认证。")
    # Emby's public-info response does not reliably expose wizard state across versions.
    try:
        emby = ET.parse(root / "state/emby/config/system.xml").getroot()
    except (OSError, ET.ParseError):
        raise ValueError("Emby 首次设置尚未生成有效配置，请先完成网页向导。") from None
    if (emby.findtext("IsStartupWizardCompleted") or "").lower() != "true":
        raise ValueError("请先完成 Emby 首次设置及管理员密码配置。")
    print("为确认隐藏账号也没有免密入口，临时登录 Emby 检查用户；不会保存本次密码和会话。")
    username = input("Emby 管理员用户名 [admin]: ").strip() or "admin"
    verify_emby_accounts(username, getpass.getpass("Emby 管理员密码（不回显）: "))
    print("初始化和搜索认证检查通过。公网开启不代表真实播放验收通过。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", action="store_true", help="隐藏输入 OpenList Token")
    parser.add_argument("--connect", action="store_true", help="连接已初始化的应用并配置上传")
    parser.add_argument("--check-public", action="store_true", help="开启公网前检查初始化及搜索认证")
    parser.add_argument("--migrate-relay-token", action="store_true", help="将旧版播放令牌迁移到独立目录")
    args = parser.parse_args()
    if os.name != "posix" or (os.geteuid() != 0 and os.geteuid() != 1000):
        raise ValueError("请在 Debian 上以 root 或 UID 1000 运行；此程序不会修改系统用户。")
    if args.check_public:
        check_public(ROOT)
    elif args.migrate_relay_token:
        migrate_relay_token(ROOT)
    elif args.connect:
        connect(ROOT)
    elif args.token:
        set_token(ROOT, getpass.getpass("OpenList Token（不回显）: ").strip())
        print("已写入 Token。重启或启动 relay 生效。")
    else:
        prepare(ROOT, input("影院域名（如 movie.example.com，所有应用共用）: "))
        print("配置完成。基础管理服务启动后，按 README 完成首次授权和 setup。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, UnicodeError) as error:
        print(f"停止：{error}", file=sys.stderr)
        sys.exit(1)
