"""Shared archive evidence and short cross-process watch updates; no media index."""
from contextlib import contextmanager, closing
from datetime import datetime
import json
import os
import re
from pathlib import Path, PurePosixPath
import sqlite3
import threading
import time

from configure import private_write


VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"}
SUBTITLES = {'.srt', '.ass', '.ssa'}
CLEANUP_EXTRAS = {'.nfo', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.avif', '.ttf', '.otf', '.woff', '.woff2'}


def episode_sidecars(movie):
    # Sonarr's episodefile deletion also removes tracked extras from the import folder.
    movie = Path(movie)
    stem = movie.stem.casefold()
    try:
        siblings = list(movie.parent.iterdir())
    except FileNotFoundError:
        return
    for path in siblings:
        if (path != movie and path.suffix.lower() not in CLEANUP_EXTRAS
                and path.name.casefold().startswith((stem + '.', stem + '_', stem + '-'))
                and not path.is_dir()):
            yield path


def sample(path):
    path = PurePosixPath(path)
    return any(p.casefold() in {'sample', 'samples'} for p in path.parts[:-1]) or bool(re.search(r'(?:^|[ ._-])sample(?:[ ._-]|$)', path.stem, re.I))


def disc(path):
    path = PurePosixPath(path)
    return path.suffix.casefold() == '.iso' or any(p.casefold() in {'bdmv', 'certificate'} for p in path.parts[:-1])


def video(path):
    return PurePosixPath(path).suffix.lower() in VIDEO and not sample(path) and not disc(path)


def subtitle_for(subtitle, movie):
    sub, movie = PurePosixPath(subtitle), PurePosixPath(movie)
    if sub.parent != movie.parent or sub.suffix.lower() not in SUBTITLES:
        return False
    suffix = sub.stem[len(movie.stem):] if sub.stem.casefold().startswith(movie.stem.casefold()) else None
    return suffix is not None and (not suffix or bool(re.fullmatch(r'(?:[._-][A-Za-z0-9-]+)+', suffix)))



_held = threading.local()


@contextmanager
def watch_lock(data):
    path = Path(data) / 'watch.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    held = getattr(_held, 'paths', set())
    if str(path.resolve()) in held:
        yield
        return
    with path.open('a+b') as handle:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            import msvcrt
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        key = str(path.resolve())
        _held.paths = held | {key}
        try:
            yield
        finally:
            _held.paths = held
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def create_archive(db):
    db.execute('''CREATE TABLE IF NOT EXISTS archived (
        tvdb_id INTEGER NOT NULL, season INTEGER NOT NULL, episode INTEGER NOT NULL,
        job_id TEXT NOT NULL, at REAL NOT NULL,
        PRIMARY KEY(tvdb_id,season,episode,job_id))''')
    db.execute('CREATE INDEX IF NOT EXISTS archived_series ON archived(tvdb_id,season,episode)')
    create_actions(db)


def create_actions(db):
    db.execute('''CREATE TABLE IF NOT EXISTS task_actions (
        id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL, error TEXT, at REAL NOT NULL)''')


def task_actions(path):
    if not Path(path).exists():
        return []
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_actions'").fetchone():
            return []
        return [{**dict(row),'payload':json.loads(row['payload'])} for row in db.execute("SELECT * FROM task_actions WHERE state!='released'")]


def torrent_instance(torrent):
    return tuple(torrent.get(k) for k in ('hash', 'category', 'save_path', 'added_on'))


def delete_target(job):
    return {key: job.get(key) for key in ('id', 'dest', 'size', 'source', 'download_source',
                                          'torrent_hash', 'added_on', 'identity', 'parent_id')}


def action_matches(job, action):
    payload=action['payload']
    matching=[t for t in payload.get('torrents',[]) if t['hash']==job.get('torrent_hash')]
    if matching and job.get('added_on') and matching[0].get('added_on') and job['added_on']!=matching[0]['added_on']:
        return False
    if job['id'] in payload.get('jobs',[]):
        return True
    if action['action'] == 'delete':
        return False
    if payload.get('tvdb_id') and job.get('tvdb_id') == payload['tvdb_id']:
        return True
    return any(job.get('torrent_hash')==t['hash'] and (not job.get('added_on') or job.get('added_on',0)==t.get('added_on',0))
               for t in payload.get('torrents',[]))


def episode_keys(value):
    keys = json.loads(value or '[]')
    if not isinstance(keys, list):
        raise ValueError('invalid_episode_mapping')
    for key in keys:
        if not isinstance(key, list) or len(key) != 2 or any(type(n) is not int or n < 0 for n in key):
            raise ValueError('invalid_episode_mapping')
    return keys


def record_archive(db, job):
    if job.get('state') != 'published' or not job.get('tvdb_id'):
        return
    keys = episode_keys(job.get('episode_keys'))
    for season, episode in keys:
        db.execute('INSERT OR IGNORE INTO archived VALUES (?,?,?,?,?)',
                   (job['tvdb_id'], season, episode, job['id'], time.time()))


def archived_keys(dbpath, tvdb):
    path = Path(dbpath)
    if not path.exists():
        return set()
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='archived'").fetchone():
            return set()
        return set(db.execute('''SELECT DISTINCT a.season,a.episode FROM archived a
            JOIN jobs j ON j.id=a.job_id WHERE a.tvdb_id=? AND j.state='published' ''', (tvdb,)))


def read_requests(data):
    path = Path(data) / 'watch-requests.json'
    return json.loads(path.read_text()) if path.exists() else {}


def save_requests(data, values):
    private_write(Path(data) / 'watch-requests.json', json.dumps(values, ensure_ascii=False))


def apply_scope(sonarr, series, mode, season, archived, since=None):
    if mode not in {'all', 'future', 'season'}:
        raise ValueError('请选择追更范围。')
    if mode == 'season' and season not in [s['seasonNumber'] for s in series['seasons']]:
        raise ValueError('季数无效。')
    episodes = sonarr.json('GET', '/api/v3/episode?seriesId=' + str(series['id']))
    if not episodes:
        return False
    since = time.time() if since is None else since
    def wanted(e):
        key = (e['seasonNumber'], e['episodeNumber'])
        date = e.get('airDateUtc')
        future = not date or datetime.fromisoformat(date.replace('Z', '+00:00')).timestamp() >= since
        return key not in archived and key[0] > 0 and (mode != 'season' or key[0] == season) and (mode != 'future' or future)
    series = json.loads(json.dumps(series))
    for entry in series['seasons']:
        entry['monitored'] = entry['seasonNumber'] > 0 and (mode != 'season' or entry['seasonNumber'] == season)
    series.update(monitored=False, monitorNewItems='none' if mode == 'season' else 'all')
    sonarr.json('PUT', '/api/v3/series/' + str(series['id']), series)
    for monitored in (True, False):
        ids = [e['id'] for e in episodes if wanted(e) == monitored]
        if ids:
            sonarr.json('PUT', '/api/v3/episode/monitor', {'episodeIds': ids, 'monitored': monitored})
    series['monitored'] = True
    sonarr.json('PUT', '/api/v3/series/' + str(series['id']), series)
    if mode != 'future':
        sonarr.json('POST', '/api/v3/command', {'name': 'MissingEpisodeSearch', 'seriesId': series['id'], 'monitored': True})
    return True


def space_required(size, remaining, backlog, total):
    reserve = max(5 * 1024**3, int(total * .08))
    return size + remaining + max(size, backlog) + reserve
