#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
SOURCE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TARGET="${1:-/opt/cinema}"
[[ $EUID -eq 0 && $# -le 1 ]] || { echo '用法：sudo bash scripts/update_portal.sh [/opt/cinema]' >&2; exit 1; }
TARGET="$(python3 - "$TARGET" <<'PY_TARGET'
from pathlib import Path
import sys
p = Path(sys.argv[1])
if not p.is_absolute() or any(v.is_symlink() for v in (p, *p.parents)) or p == Path('/'):
    raise SystemExit('部署路径必须是无符号链接的绝对路径。')
if not all((p / n).is_file() and not (p / n).is_symlink() for n in ('.env', 'compose.yaml')):
    raise SystemExit('找不到有效的影院部署配置。')
print(p.resolve(strict=True))
PY_TARGET
)"
cd "$TARGET"
docker compose config --quiet
mapfile -t RUNNING < <(docker compose --profile '*' ps --status running --services)
BACKUP=''
STAMP="$(date -u +%Y%m%dT%H%M%S)"
declare -A OLD_IMAGES
for service in portal uploader relay; do
    container="$(docker compose ps -aq "$service")"
    if [[ -n $container ]]; then
        OLD_IMAGES[$service]="$(docker inspect --format '{{.Config.Image}}' "$container")"
        docker tag "$(docker inspect --format '{{.Image}}' "$container")" "cinema-update-backup:$service-$STAMP"
    fi
done
restore_running() {
    if ((${#RUNNING[@]})); then docker compose --profile '*' up -d --no-build "${RUNNING[@]}"; fi
}
restore_on_error() {
    echo '更新未完成，正在恢复旧源码和应用镜像；数据库保留当前状态，备份位于部署目录 backups。' >&2
    if [[ -n $BACKUP && -f $BACKUP ]]; then
        python3 - "$BACKUP" "$TARGET" <<'PY_ROLLBACK'
from pathlib import Path
import sys
import tarfile
with tarfile.open(sys.argv[1]) as archive:
    names = set(archive.getnames())
    for name in names:
        member = archive.getmember(name)
        if member.isfile() and not name.startswith('state/') and name not in {'.env','compose.override.yaml','compose.override.yml'}:
            target = Path(sys.argv[2]) / name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
PY_ROLLBACK
    fi
    for service in "${!OLD_IMAGES[@]}"; do docker tag "cinema-update-backup:$service-$STAMP" "${OLD_IMAGES[$service]}"; done
    restore_running
    if docker compose ps --status running --services | grep -qx gateway; then
        docker compose exec -T gateway caddy reload --config /etc/caddy/Caddyfile
    fi
}
trap restore_on_error ERR
docker compose stop portal uploader
BACKUP="$(python3 - "$SOURCE" "$TARGET" <<'PY_UPDATE'
import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import sys
import tarfile

source, raw_target = map(Path, sys.argv[1:])
if not raw_target.is_absolute() or any(p.is_symlink() for p in (raw_target, *raw_target.parents)):
    raise SystemExit('部署路径必须是无符号链接的绝对路径。')
target = raw_target.resolve(strict=True)
if target == source.resolve() or target == Path('/') or not all((target / p).is_file() for p in ('.env', 'compose.yaml', 'state/relay/config.yml')):
    raise SystemExit('需要已安装的影院目录，且更新包必须放在另一目录。')
for name in ('state', 'media'):
    if not (target / name).is_dir() or (target / name).is_symlink():
        raise SystemExit('state/media 必须是部署目录下的真实目录。')
backup = target / 'backups' / ('before-portal-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz')
backup.parent.mkdir(exist_ok=True)
files = ('install.sh', 'compose.yaml', 'configure.py', 'integration.py', 'archive.py', 'README.md', 'SOURCES.md', 'DESIGN.md', 'Open.cmd', '.dockerignore')
folders = ('gateway', 'relay', 'uploader', 'portal', 'docs', 'scripts', 'tests')
if backup.parent.is_symlink():
    raise SystemExit('备份目录不可是符号链接。')
for name in (*files, *folders):
    origin = source / name
    for item in (origin, *(origin.rglob('*') if origin.is_dir() else [])):
        destination = target / item.relative_to(source)
        if item.is_symlink() or any(p.is_symlink() for p in (destination, *destination.parents)):
            raise SystemExit('更新源码和目标路径不可包含符号链接。')
        if not destination.resolve().is_relative_to(target):
            raise SystemExit('更新路径超出部署目录。')
databases = []
for path in (target/'state').rglob('*'):
    if path.is_file() and not path.is_symlink():
        with path.open('rb') as handle:
            if handle.read(16) == b'SQLite format 3\0':
                databases.append(path)
excluded = {p.relative_to(target).as_posix() for db in databases for p in (db,Path(str(db)+'-wal'),Path(str(db)+'-shm'))}
def tar_filter(info):
    return None if info.name in excluded else info
with tarfile.open(backup, 'w:gz') as tar:
    for name in (*files, *folders, '.env', 'compose.override.yaml', 'compose.override.yml', 'state'):
        if (target / name).exists():
            tar.add(target / name, arcname=name, filter=tar_filter)
    with tempfile.TemporaryDirectory() as temp:
        for index, path in enumerate(databases):
            snapshot = Path(temp)/str(index)
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(snapshot) as dst:
                src.backup(dst)
            tar.add(snapshot,arcname=str(path.relative_to(target)))
os.chmod(backup, 0o600)
with tarfile.open(backup) as tar:
    if not {'.env', 'compose.yaml', 'state/relay/config.yml'} <= set(tar.getnames()):
        raise SystemExit('备份校验失败，停止更新。')
print(str(backup), flush=True)
for name in files:
    shutil.copy2(source / name, target / name)
for name in folders:
    if (target / name).is_symlink():
        raise SystemExit('源码目录不可是符号链接。')
    shutil.copytree(source / name, target / name, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
for name in ('state/uploader', 'state/uploader/data', 'state/openlist-runtime', 'state/relay/lib/playback'):
    path = target / name
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise SystemExit('运行目录不可包含符号链接。')
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, 1000, 1000)
    os.chmod(path, 0o750)
PY_UPDATE
)"
echo "源码及配置状态已备份：$BACKUP"
cd "$TARGET"
docker compose config --quiet
if [[ ! -s state/openlist-runtime/instance-id ]]; then
    python3 - <<'PY_UPLOADS'
import json, sqlite3
from pathlib import Path
from integration import Client
p=Path('state/uploader/data/uploads.sqlite3')
if p.exists():
    with sqlite3.connect(p) as db:
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('uploading','queued','moving')").fetchone():
            raise SystemExit('请等待正在上传或发布的任务结束后，再安装此次升级。')
config=json.loads(Path('state/uploader/config.json').read_text())
client=Client('http://127.0.0.1:5244/openlist')
client.opener.addheaders=[('Authorization',config['openlist_token'])]
value=client.json('GET','/api/admin/task/upload/undone')
if value.get('code')!=200 or not isinstance(value.get('data'),list) or value['data']:
    raise SystemExit('OpenList 仍有上传任务，请结束后再升级。')
PY_UPLOADS
    docker compose --profile connected up -d --no-deps openlist
fi
docker compose --profile connected up -d --build --force-recreate --no-deps portal uploader relay
if docker compose ps --status running --services | grep -qx gateway; then
    docker compose exec -T gateway caddy validate --config /etc/caddy/Caddyfile
    docker compose exec -T gateway caddy reload --config /etc/caddy/Caddyfile
fi
trap - ERR
echo '更新完成：访问 /discover/，使用 Emby 管理员账号登录。原生管理页仍保留。'
