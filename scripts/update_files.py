"""Back up, install and restore the source files managed by the cinema release."""
import argparse
from contextlib import closing
import datetime
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile

from configure import migrate_relay_token
from scripts.build_all_in_one import package_path, source_files

MANIFEST = '.cinema-source-files.json'
ROLLBACK = '.cinema-rollback.json'
RUNTIME = ('state/uploader', 'state/uploader/data', 'state/openlist-runtime', 'state/relay/lib/playback', 'state/relay-token')


def managed_file(root, name):
    path = PurePosixPath(name)
    if path.as_posix() != name or (name != MANIFEST and not package_path(path)):
        raise ValueError('发行文件清单含未允许的路径。')
    file = root / name
    if any(p.is_symlink() for p in (file, *file.parents)) or (
            file.exists() and (not file.is_file() or file.stat().st_nlink != 1)):
        raise ValueError(f'需要普通发行文件，且路径不可包含链接：{name}')
    return file


def roots(source, target):
    if not target.is_absolute() or any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('部署路径必须是无符号链接的绝对路径。')
    target = target.resolve(strict=True)
    source = source.resolve(strict=True)
    if target == Path('/') or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('更新包和部署目录必须分开。')
    for name in ('.env', 'compose.yaml', 'state/relay/config.yml'):
        path = target/name
        if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('找不到有效的影院部署配置。')
    for name in ('state', 'media', 'backups', *RUNTIME):
        path = target/name
        if any(p.is_symlink() for p in (path, *path.parents)) or path.exists() and not path.is_dir():
            raise ValueError(f'需要真实部署目录：{name}')
    if not (target/'media').is_dir():
        raise ValueError('缺少影院媒体目录。')
    return source, target


def release_files(source, target):
    incoming = {p.relative_to(source).as_posix(): p for p in source_files(source)}
    manifest = managed_file(target, MANIFEST)
    previous = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
    if not isinstance(previous, list) or any(not isinstance(p, str) or not package_path(PurePosixPath(p)) for p in previous):
        raise ValueError('旧发行文件清单无效。')
    touched = {name:managed_file(target, name) for name in sorted(incoming.keys() | set(previous) | {MANIFEST})}
    return incoming, touched


def snapshot(source, target):
    _, files = release_files(source, target)
    folder = target/'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = folder/f'before-portal-{stamp}.tar.gz'
    databases = []
    for path in (target/'state').rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as handle:
                if handle.read(16) == b'SQLite format 3\0':
                    databases.append(path)
    excluded = {p.relative_to(target).as_posix() + suffix for p in databases for suffix in ('', '-wal', '-shm', '-journal')}
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
        temp = Path(handle.name)
    try:
        with tarfile.open(temp, 'w:gz') as tar:
            metadata = json.dumps(list(files)).encode()
            member = tarfile.TarInfo(ROLLBACK)
            member.size = len(metadata)
            tar.addfile(member, io.BytesIO(metadata))
            for name in (*files, '.env', 'compose.override.yaml', 'compose.override.yml', 'state'):
                path = target/name
                if path.exists():
                    tar.add(path, arcname=name, filter=lambda info: None if info.name in excluded else info)
            with tempfile.TemporaryDirectory() as scratch:
                for index, path in enumerate(databases):
                    copy = Path(scratch)/str(index)
                    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(copy)) as dst:
                        src.backup(dst)
                    # Restore the service's ownership and mode along with the consistent snapshot.
                    member = tar.gettarinfo(str(path), arcname=path.relative_to(target).as_posix())
                    member.type, member.linkname = tarfile.REGTYPE, ''
                    member.size = copy.stat().st_size
                    with copy.open('rb') as data:
                        tar.addfile(member, data)
        with tarfile.open(temp) as tar:
            required = {ROLLBACK, '.env', 'compose.yaml', 'state/relay/config.yml'}
            required.update(path.relative_to(target).as_posix() for path in databases)
            if not required <= set(tar.getnames()):
                raise ValueError('备份校验失败。')
        temp.replace(backup)
    finally:
        temp.unlink(missing_ok=True)
    return backup


def apply(source, target, backup):
    incoming, touched = release_files(source, target)
    with tarfile.open(backup) as tar:
        if set(json.load(tar.extractfile(ROLLBACK))) != touched.keys():
            raise ValueError('发行文件清单已变化，请重新生成备份。')
    # A release extracted under umask 077 can leave even existing bind-mounted
    # source directories unreadable to the service account.
    directories = {parent for name in incoming for parent in Path(name).parents if parent != Path('.')}
    for directory in sorted(directories):
        path = target / directory
        if not path.exists() or directory.as_posix() in {'portal', 'relay', 'relay/custom-js', 'relay/custom-css', 'scripts', 'gateway'}:
            path.mkdir(exist_ok=True)
            path.chmod(0o755)
    for name, path in touched.items():
        if name in incoming:
            shutil.copyfile(incoming[name], path)
            path.chmod(0o755 if path.suffix == '.sh' else 0o644)
        elif name != MANIFEST:
            path.unlink(missing_ok=True)
    touched[MANIFEST].write_text(json.dumps(sorted(incoming)), encoding='utf-8')
    for name in RUNTIME:
        path = target/name
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'posix' and os.geteuid() == 0:
            os.chown(path, 1000, 1000)
        path.chmod(0o750)
    migrate_relay_token(target)


def restore(target, backup):
    with tarfile.open(backup) as tar:
        files = {name:managed_file(target, name) for name in json.load(tar.extractfile(ROLLBACK))}
        members = {member.name:member for member in tar.getmembers() if member.name in files}
        if any(not member.isfile() for member in members.values()):
            raise ValueError('备份发行文件包含链接或目录。')
        for name, path in files.items():
            if name not in members:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(members[name]) as data, path.open('wb') as output:
                shutil.copyfileobj(data, output)
            path.chmod(members[name].mode)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('check', 'snapshot', 'apply', 'restore'))
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('backup', type=Path, nargs='?')
    argv = parser.parse_args()
    try:
        source, target = roots(argv.source, argv.target)
        if argv.step == 'check':
            release_files(source, target)
            print(target)
        elif argv.step == 'snapshot':
            print(snapshot(source, target))
        elif argv.step == 'apply':
            apply(source, target, argv.backup)
        elif argv.step == 'restore':
            restore(target, argv.backup)
    except (ValueError, OSError, tarfile.TarError, sqlite3.Error) as error:
        raise SystemExit('更新：' + str(error)) from None
