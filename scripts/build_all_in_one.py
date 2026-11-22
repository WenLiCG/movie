"""Build dist/cinema-all-in-one.sh with an allowlisted, checksummed source payload."""
import argparse
import base64
import gzip
import hashlib
import inspect
import io
from pathlib import Path
import tarfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]
FILES = ('install.sh', 'compose.yaml', 'configure.py', 'integration.py', 'archive.py', 'README.md', 'SOURCES.md', 'DESIGN.md', 'Open.cmd', '.dockerignore')
DIRECTORIES = ('gateway', 'relay', 'uploader', 'portal', 'docs', 'scripts', 'tests')
SUFFIXES = {'.py', '.md', '.yml', '.yaml', '.js', '.cjs', '.css', '.html', '.sh', '.patch', '.txt', '.go'}
EXCLUDED = {'state', 'media', '.env', '.upstream', '.tools', '.git', 'dist', '__pycache__'}
FIXTURES = {'tests/fixtures/emby-4.10.2-default-options-movies.json',
            'tests/fixtures/emby-4.10.2-default-options-tvshows.json'}


def package_path(path):
    name = path.as_posix()
    if name in FILES:
        return True
    return (len(path.parts) > 1 and path.parts[0] in DIRECTORIES
            and not any(part in EXCLUDED or part.startswith('.') or ':' in part or '\\' in part for part in path.parts)
            and name != 'docs/DEPLOYMENT.md'
            and (name in FIXTURES or path.suffix in SUFFIXES or path.name in ('Dockerfile', 'Caddyfile')
                 or path.name.startswith('LICENSE')))


def source_files(root):
    files = [root / name for name in FILES]
    for folder in DIRECTORIES:
        base = root / folder
        if not base.is_dir() or base.is_symlink():
            raise ValueError(f'Missing package directory: {folder}')
        for path in base.rglob('*'):
            if path.is_file() and package_path(path.relative_to(root)):
                files.append(path)
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing or unsafe package file: {path}')
        if root.resolve() not in path.resolve().parents:
            raise ValueError(f'Package path escapes checkout: {path}')
    return sorted(files)


def make_payload(root):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for path in source_files(root):
            data = path.read_bytes()
            if path.suffix == '.sh' and b'\r' in data:
                raise ValueError(f'Shell script must use LF: {path}')
            entry = tarfile.TarInfo(path.relative_to(root).as_posix())
            entry.size = len(data)
            entry.mode = 0o755 if path.suffix == '.sh' else 0o644
            archive.addfile(entry, io.BytesIO(data))
    return gzip.compress(stream.getvalue(), mtime=0)


def extract_payload(data, target, expected):
    """Verify everything before creating files; existing installations are never overwritten."""
    import hashlib
    import io
    import json
    import os
    from pathlib import Path, PurePosixPath
    import shutil
    import tarfile
    import tempfile

    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('安装包校验失败，请重新取得原始文件。')
    target = Path(target).absolute()
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError('安装目录及上级路径不可为符号链接。')
    target = target.resolve()
    if target == Path('/'):
        raise ValueError('不可安装到文件系统根目录。')
    marker = target / '.cinema-package.json'
    required = {'install.sh', 'compose.yaml', 'configure.py', 'integration.py'}
    if target.exists() and any(target.iterdir()):
        if marker.is_file() and not marker.is_symlink() and json.loads(marker.read_text()) == {'sha256': expected}:
            if any(not (target / name).is_file() or (target / name).is_symlink() for name in required):
                raise ValueError('已有安装缺少关键文件或包含符号链接，请从备份恢复。')
            return target
        raise ValueError('目标目录已有内容或另一版本；拒绝覆盖，请指定新的 --dir。')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        seen, total = set(), 0
        for entry in members:
            path = PurePosixPath(entry.name)
            if not entry.isfile() or path.is_absolute() or '..' in path.parts or '\\' in entry.name or not path.parts:
                raise ValueError('安装包包含不安全路径或链接。')
            if entry.name in seen or (path.name.startswith('.') and entry.name != '.dockerignore'):
                raise ValueError('安装包包含重复或隐藏文件。')
            seen.add(entry.name)
            total += entry.size
        if total > 32 * 1024 * 1024:
            raise ValueError('安装包解压大小超出限制。')
        if not required.issubset(seen):
            raise ValueError('安装包缺少关键文件。')
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.cinema-stage-', dir=target.parent))
        try:
            for entry in members:
                destination = staging / entry.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(0o755 if destination.suffix == '.sh' else 0o644)
            (staging / '.cinema-package.json').write_text(json.dumps({'sha256': expected}) + '\n')
            (staging / '.cinema-source-files.json').write_text(json.dumps(sorted(seen)) + '\n', encoding='utf-8')
            for directory in staging.rglob('*'):
                if directory.is_dir():
                    directory.chmod(0o755)
            staging.chmod(0o755)
            if target.exists():
                target.rmdir()  # Only an empty, previously checked directory can be replaced.
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target


def build(root, destination):
    data = make_payload(root)
    digest = hashlib.sha256(data).hexdigest()
    prelude = r'''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DEST=/opt/cinema
while (($#)); do
    case "$1" in
        --dir) [[ $# -ge 2 ]] || { echo '缺少 --dir 路径' >&2; exit 1; }; DEST=$2; shift 2 ;;
        --help|-h) echo '用法：sudo bash cinema-all-in-one.sh [--dir /opt/cinema]'; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 1 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash 运行。' >&2; exit 1; }
. /etc/os-release
[[ ${ID:-} == debian && (${VERSION_ID:-} == 12 || ${VERSION_ID:-} == 13) ]] || { echo '仅支持 Debian 12/13。' >&2; exit 1; }
case "$(dpkg --print-architecture)" in amd64|arm64) ;; *) echo '仅支持 amd64/arm64。' >&2; exit 1 ;; esac
if ! command -v python3 >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove python3
fi
python3 - "$0" "$DEST" <<'PY_EXTRACT'
import base64
from pathlib import Path
import sys
'''
    footer = f'''
try:
    content = Path(sys.argv[1]).read_bytes().split(b"\\n__CINEMA_PAYLOAD__\\n", 1)[1]
    payload = base64.b64decode(content, validate=False)
    extract_payload(payload, sys.argv[2], "{digest}")
except (ValueError, OSError, KeyError, IndexError) as error:
    raise SystemExit("停止：" + str(error))
PY_EXTRACT
exec bash "$DEST/install.sh" install
exit 0
__CINEMA_PAYLOAD__
'''
    content = prelude + textwrap.dedent(inspect.getsource(extract_payload)) + footer
    content += '\n'.join(textwrap.wrap(base64.b64encode(data).decode(), 76)) + '\n'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding='utf-8', newline='\n')
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + '.sha256').write_text(
        f'{checksum}  {destination.name}\n', encoding='ascii', newline='\n')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/cinema-all-in-one.sh')
    args = parser.parse_args()
    print(build(ROOT, args.output))
