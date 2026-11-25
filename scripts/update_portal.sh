#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
SOURCE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TARGET="${1:-/opt/cinema}"
[[ $EUID -eq 0 && $# -le 1 ]] || { echo '用法：sudo bash scripts/update_portal.sh [/opt/cinema]' >&2; exit 1; }

update_files() (
    trap - ERR
    cd "$SOURCE"
    python3 -m scripts.update_files "$1" "$SOURCE" "$TARGET" "${@:2}"
)

TARGET="$(update_files check)"
cd "$TARGET"
python3 "$SOURCE/scripts/compose_preflight.py"
running="$(docker compose --profile '*' ps --status running --services)"
RUNNING=()
if [[ -n $running ]]; then mapfile -t RUNNING <<< "$running"; fi
STAMP="$(date -u +%Y%m%dT%H%M%S%N)"
declare -A OLD_IMAGES
for service in portal uploader relay; do
    container="$(docker compose --profile '*' ps -aq "$service")"
    if [[ -n $container ]]; then
        OLD_IMAGES[$service]="$(docker inspect --format '{{.Config.Image}}' "$container")"
        docker tag "$(docker inspect --format '{{.Image}}' "$container")" "cinema-update-backup:$service-$STAMP"
    fi
done
BACKUP="$(update_files snapshot)"
echo "源码及配置状态已备份：$BACKUP"

was_running() { grep -Fxq -- "$1" <<< "$running"; }

reload_gateway() {
    # A recreated container can start before its Caddy admin socket accepts reloads.
    local attempt
    for attempt in {1..10}; do
        docker compose --profile '*' exec -T gateway caddy reload --config /etc/caddy/Caddyfile && return 0
        sleep 1
    done
    return 1
}

restore_on_error() {
    local status=$? failed=0
    trap - ERR
    set +e
    echo '更新未完成，正在恢复旧源码和应用镜像。' >&2
    update_files restore "$BACKUP" || failed=1
    for service in "${!OLD_IMAGES[@]}"; do
        docker tag "cinema-update-backup:$service-$STAMP" "${OLD_IMAGES[$service]}" || failed=1
    done
    for service in portal uploader relay openlist; do
        if ! was_running "$service"; then docker compose --profile '*' stop "$service" || failed=1; fi
    done
    if ((${#RUNNING[@]})); then
        docker compose --profile '*' up -d --no-build --no-deps "${RUNNING[@]}" || failed=1
    fi
    if was_running gateway; then
        reload_gateway || failed=1
    fi
    if ((failed)); then echo "部分恢复失败，请使用备份检查：$BACKUP" >&2; fi
    exit "$status"
}
trap restore_on_error ERR
docker compose --profile '*' stop portal uploader
update_files apply "$BACKUP"
docker compose --profile '*' config --quiet
if [[ ! -s state/openlist-runtime/instance-id ]]; then
    python3 - <<'PY_UPLOADS'
import contextlib, json, sqlite3
from pathlib import Path
from integration import Client
p=Path('state/uploader/data/uploads.sqlite3')
if p.exists():
    with contextlib.closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('uploading','queued','moving')").fetchone():
                raise SystemExit('请等待正在上传或发布的任务结束后，再安装此次升级。')
config=json.loads(Path('state/uploader/config.json').read_text(encoding='utf-8'))
client=Client('http://127.0.0.1:5244/openlist')
client.opener.addheaders=[('Authorization',config['openlist_token'])]
value=client.json('GET','/api/admin/task/upload/undone')
if value.get('code')!=200 or not isinstance(value.get('data'),list) or value['data']:
    raise SystemExit('OpenList 仍有上传任务，请结束后再升级。')
PY_UPLOADS
    docker compose --profile connected up -d --no-deps openlist
fi
docker compose --profile connected up -d --build --force-recreate --no-deps portal uploader relay
if was_running gateway; then
    docker compose --profile '*' run --rm -T --no-deps gateway caddy validate --config /etc/caddy/Caddyfile
    docker compose --profile '*' up -d --no-deps gateway
    reload_gateway
fi
trap - ERR
echo '更新完成：访问 /discover/，使用 Emby 管理员账号登录。原生管理页仍保留。'
