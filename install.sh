#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
MODE="${1:-install}"

die() { printf '停止：%s\n' "$*" >&2; exit 1; }
help() {
    cat <<'HELP'
用法：sudo bash install.sh [install|setup|public|status|stop|help]
  install  检查 Debian/Docker，初始化并启动基础管理服务（默认）
  setup    录入网盘与下载器凭据，自动接线，构建并启动播放和上传服务
  public   检查账号和配置，显式开启 80/443 HTTPS 公网入口
  status   查看本项目容器
  stop     停止本项目容器，保留配置和媒体
HELP
}

check_host() {
    [[ $EUID -eq 0 ]] || die '请使用 sudo bash install.sh。'
    [[ -r /etc/os-release ]] || die '无法识别系统。'
    . /etc/os-release
    [[ ${ID:-} == debian && (${VERSION_ID:-} == 12 || ${VERSION_ID:-} == 13) ]] || die '仅支持 Debian 12/13。'
    case "$(dpkg --print-architecture)" in amd64|arm64) ;; *) die '仅支持 amd64/arm64。' ;; esac
}

ensure_docker() {
    if ! command -v python3 >/dev/null 2>&1; then
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove python3
    fi
    if command -v docker >/dev/null 2>&1; then
        docker compose version >/dev/null 2>&1 || die '已有 Docker，但缺少 Compose v2 插件；请为现有引擎安装 Compose 插件后重试，不会替换现有 Docker。'
    else
        # Docker's official Debian repository; never remove an existing runtime.
        for package in docker.io docker-compose docker-doc docker-buildx podman-docker containerd runc; do
            if [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true) == installed ]]; then
                die "检测到已有 $package；请先人工确认运行时兼容性，本脚本不卸载它。"
            fi
        done
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove ca-certificates curl
        if ! grep -Rqs 'download.docker.com/linux/debian' /etc/apt/sources.list /etc/apt/sources.list.d; then
            install -m 0755 -d /etc/apt/keyrings
            [[ ! -e /etc/apt/sources.list.d/cinema-docker.sources ]] || die '已有同名 Docker 仓库文件，拒绝覆盖。'
            curl -fsSL --retry 3 https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/cinema-docker.asc.tmp
            chmod 0644 /etc/apt/keyrings/cinema-docker.asc.tmp
            if [[ -e /etc/apt/keyrings/cinema-docker.asc ]]; then
                cmp -s /etc/apt/keyrings/cinema-docker.asc.tmp /etc/apt/keyrings/cinema-docker.asc || die '已有同名签名密钥与官方文件不同，拒绝覆盖。'
                rm /etc/apt/keyrings/cinema-docker.asc.tmp
            else
                mv /etc/apt/keyrings/cinema-docker.asc.tmp /etc/apt/keyrings/cinema-docker.asc
            fi
            cat > /etc/apt/sources.list.d/cinema-docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/cinema-docker.asc
EOF
            chmod 0644 /etc/apt/sources.list.d/cinema-docker.sources
        fi
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        systemctl start docker
    fi
    docker info >/dev/null 2>&1 || die 'Docker daemon 不可用；请检查现有服务后重试，本脚本不会重启它。'
}

preflight() {
    python3 "$ROOT/scripts/compose_preflight.py" "$@"
}

case "$MODE" in help|--help|-h) help; exit 0 ;; install|setup|public|status|stop) ;; *) help; die "未知命令：$MODE" ;; esac
[[ $# -le 1 ]] || die '参数过多。'
check_host
if [[ $MODE != status ]]; then
    exec 9<.
    flock -n 9 || die '另一项部署操作正在进行，请在结束后重试。'
fi
if [[ $MODE == install ]]; then
    ensure_docker
    if [[ ! -f .env ]]; then python3 configure.py; fi
    [[ -f state/relay/config.yml ]] || die '配置不完整，请运行 python3 configure.py 检查。'
    docker compose config --quiet
    preflight openlist emby prowlarr sonarr qbittorrent portal
    docker compose up -d --build openlist emby prowlarr sonarr qbittorrent portal uploader
    date -u '+%Y-%m-%dT%H:%M:%SZ' > state/base-services-started
    printf '\n基础服务已启动。按 README 建立 SSH 隧道，完成各应用账号初始化。域名就绪后可用 public 开启 HTTPS，再在网页完成云盘授权。\n云盘挂载后运行：\n  cd %q\n  sudo bash install.sh setup\n' "$ROOT"
else
    command -v python3 >/dev/null 2>&1 || die '缺少 Python 3，请先运行 install。'
    command -v docker >/dev/null 2>&1 || die '缺少 Docker，请先运行 install。'
    [[ -f .env ]] || die '尚未初始化，请先运行 install。'
    preflight
    case "$MODE" in
        setup)
            preflight relay uploader
            python3 configure.py --connect
            docker compose --profile connected build relay uploader
            docker compose --profile connected up -d --no-build relay uploader
            ;;
        public)
            python3 configure.py --check-public
            preflight gateway relay
            python3 configure.py --migrate-relay-token
            docker compose --profile public up -d gateway
            ;;
        status) docker compose --profile '*' ps ;;
        stop) docker compose --profile '*' stop ;;
    esac
fi
