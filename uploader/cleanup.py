"""Conservative, resumable cleanup of cloud-published torrent payloads via native APIs."""
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode

from uploader import Failure, VIDEO, SUBTITLES, DOWNLOADS, local_path, relative, verify, log, sample, disc, matches_payload, torrent_paths
from archive import watch_lock, task_actions, torrent_instance


# Media the uploader never archives (other video containers, separate audio tracks, bitmap or
# other subtitle formats). Deleting a torrent with deleteFiles would lose these for good.
UNARCHIVED_MEDIA = {'.rmvb', '.rm', '.wmv', '.asf', '.mpg', '.mpeg', '.mpe', '.vob', '.flv', '.f4v', '.3gp',
                    '.divx', '.ogm', '.ogv', '.mts', '.m2t', '.iso', '.mka', '.flac', '.dts', '.ac3', '.eac3',
                    '.thd', '.truehd', '.aac', '.mp3', '.m4a', '.opus', '.wav', '.sup', '.idx', '.sub', '.vtt',
                    '.smi', '.sami', '.usf', '.pgs', '.mks', '.ttml'}
# Real samples are small; a "sample"-named file above this is treated as a main feature.
SAMPLE_LIMIT = 1024 ** 3


def verify_cleanup_media(file):
    suffix = Path(file['name']).suffix.lower()
    if suffix in UNARCHIVED_MEDIA:
        raise Failure('cleanup_unarchived_media')
    if suffix in VIDEO and sample(file['name']):
        size = file['size']
        if type(size) is not int or size < 0:
            raise Failure('cleanup_check_failed')
        if size > SAMPLE_LIMIT:
            raise Failure('cleanup_not_archived')


class Cleaner:
    def __init__(self, uploader, state, settings_path=None):
        self.uploader, self.db, self.state = uploader, uploader.db, Path(state)
        self.settings_path = settings_path
        self.db.execute("""CREATE TABLE IF NOT EXISTS cleanups (
            hash TEXT PRIMARY KEY, plan TEXT NOT NULL, state TEXT NOT NULL, finished_at REAL)""")
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(cleanups)')}
        for name, definition in [('reason','TEXT'), ('retry_at','REAL NOT NULL DEFAULT 0'),
                                 ('failures','INTEGER NOT NULL DEFAULT 0'), ('label',"TEXT NOT NULL DEFAULT ''")]:
            if name not in columns:
                self.db.execute('ALTER TABLE cleanups ADD COLUMN ' + name + ' ' + definition)
        self.db.commit()

    def form(self, sources, action, values):
        sources.qb.request("POST", "/api/v2/torrents/" + action, urlencode(values).encode(),
                           {"Content-Type": "application/x-www-form-urlencoded"})

    def plan(self, sources, torrent, days, now):
        category = torrent.get("category")
        if (category not in DOWNLOADS or torrent.get("progress") != 1
                or torrent.get("amount_left") != 0 or torrent.get("completion_on", 0) <= 0
                or torrent["completion_on"] > now - days * 86400
                or type(torrent.get('added_on')) is not int or torrent['added_on'] <= 0
                or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", torrent.get("hash", ""))
                or torrent.get("state", "").lower().startswith(("checking", "moving", "error", "missing"))):
            return None
        root = sources.media / "downloads" / DOWNLOADS[category][0]
        save = Path(torrent["save_path"])
        if save != root:
            local_path(save, root)
        files = sources.qb.json("GET", "/api/v2/torrents/files", query={"hash": torrent["hash"]})
        paths, jobs, episodes = [], [], []
        published = [dict(r) for r in self.db.execute("SELECT * FROM jobs WHERE state='published' AND hidden=0")]
        for file in files:
            rel = relative(file["name"])
            path = save.joinpath(*rel.parts)
            # Unselected files cannot be proven archived; never delete their payload incidentally.
            if file.get("priority", 0) <= 0:
                if path.exists() or file.get("progress", 0) > 0:
                    return None
                continue
            path = local_path(path, root)
            if file.get("progress") != 1:
                return None
            paths.append(str(path))
            verify_cleanup_media(file)
            if sample(file['name']):
                continue
            if disc(file['name']):
                raise Failure('cleanup_unsupported_disc')
            if path.suffix.lower() not in VIDEO | SUBTITLES:
                continue
            matches = [j for j in published if Path(j['source']).exists() and (os.path.samefile(j['source'], path)
                       or (path.suffix.lower() in SUBTITLES and j.get('download_source') == str(path) and j.get('torrent_hash') == torrent['hash']))]
            if len(matches) != 1:
                raise Failure('cleanup_not_archived')
            job = matches[0]
            if category == "sonarr-tv" and path.suffix.lower() in VIDEO:
                if not re.fullmatch(r"tv:[0-9]+", job.get("identity") or ""):
                    return None
                file_id = int(job["identity"].split(":")[1])
                current = sources.sonarr.json("GET", "/api/v3/episodefile/" + str(file_id))
                if current.get("path") != job["source"] or current.get("size") != job["size"]:
                    return None
                local_path(current["path"], sources.media / "library/tv")
                episodes.append({"file_id": file_id, "series_id": current["seriesId"], "path": job["source"]})
            elif not job["dest"].startswith("/aliyun/" + DOWNLOADS[category][1] + "/"):
                return None
            jobs.append({**job, 'cleanup_source':str(path)})
        job_ids = {j['id'] for j in jobs}
        if any(j.get('parent_id') and j['parent_id'] not in job_ids for j in jobs):
            raise Failure('cleanup_subtitle_mapping_missing')
        if not jobs:
            return None
        return {"hash": torrent["hash"], "category": category, "save_path": str(save), "files": files,
                "added_on":torrent["added_on"], "download_path":torrent.get('download_path'),
                "completion_on": torrent["completion_on"], "paths": paths, "jobs": jobs, "episodes": episodes}

    def execute(self, sources, plan, days, now):
        torrents = sources.qb.json("GET", "/api/v2/torrents/info")
        current = next((t for t in torrents if t["hash"] == plan["hash"]), None)
        if (not plan.get('added_on') or self.retained(plan, task_actions(self.state/'uploads.sqlite3'))
                or current is None and not plan.get('qb_remove_requested')
                or current is not None and not self.same_download(plan, current)):
            # A different torrent instance needs a new retention plan, never a resumed delete.
            with self.db:
                self.db.execute('DELETE FROM cleanups WHERE hash=?', (plan['hash'],))
            return
        if not days or plan['completion_on'] > now-days*86400:
            return
        if current is not None:
            files = sources.qb.json("GET", "/api/v2/torrents/files", query={"hash": plan["hash"]})
            def fields(values):
                return [(f["name"], f["size"], f.get("priority")) for f in values]
            if fields(files) != fields(plan["files"]):
                raise Failure("cleanup_files_changed")
            root = sources.media / "downloads" / DOWNLOADS[plan["category"]][0]
            interim = current.get('download_path')
            if interim and Path(interim) != sources.media/'incomplete':
                local_path(interim, sources.media/'incomplete', missing_ok=True)
            unselected = [f for f in files if f.get('priority', 0) <= 0]
            if any(path.exists() or path.is_symlink() for path in torrent_paths(current, unselected)):
                raise Failure('cleanup_files_changed')
            # A pending plan may predate the media checks applied to newly planned deletes.
            for file in files:
                if file.get('priority', 0) > 0:
                    verify_cleanup_media(file)
            for path in plan["paths"]:
                local_path(path, root)
            selected = {j['id'] for j in plan['jobs']}
            owned = set(torrent_paths(current, files)) | {Path(j['source']) for j in plan['jobs']}
            if owned & self.uploader.protected_paths(sources, torrents, {plan['hash']}, selected):
                raise Failure('cleanup_shared_file')
            self.form(sources, "stop", {"hashes": plan["hash"]})
            stopped = sources.qb.json("GET", "/api/v2/torrents/info", query={"hashes": plan["hash"]})
            if not stopped or not self.same_download(plan, stopped[0]) or stopped[0].get("state") not in {"stoppedUP", "pausedUP"}:
                raise Failure("cleanup_waiting_for_stop")

            for job in plan['jobs']:
                verify(self.uploader.remote.find(job['dest']), job)
                raw, source = Path(job['cleanup_source']), Path(job['source'])
                if not matches_payload(raw, root, job):
                    raise Failure('cleanup_source_changed')
                if source != raw and source.exists():
                    local_path(source, sources.media/'library/tv')
                    if not source.samefile(raw) and not matches_payload(source, sources.media/'library/tv', job):
                        raise Failure('cleanup_source_changed')

            self.remove_episodes(sources, plan)
            latest = sources.qb.json("GET", "/api/v2/torrents/info", query={"hashes": plan["hash"]})
            if not latest or not self.same_download(plan, latest[0]) or latest[0].get('state') not in {'stoppedUP','pausedUP'}:
                raise Failure('cleanup_torrent_changed')
            plan['qb_remove_requested'] = True
            with self.db:
                self.db.execute('UPDATE cleanups SET plan=? WHERE hash=?', (json.dumps(plan), plan['hash']))
            self.form(sources, "delete", {"hashes": plan["hash"], "deleteFiles": "true"})
            if sources.qb.json("GET", "/api/v2/torrents/info", query={"hashes": plan["hash"]}):
                raise Failure("cleanup_waiting_for_removal")

        extras = [j['source'] for j in plan['jobs'] if Path(j['source']).suffix.lower() in SUBTITLES]
        if any(Path(p).exists() for p in plan["paths"] + [i["path"] for i in plan["episodes"]] + extras):
            raise Failure("cleanup_waiting_for_files")
        with self.db:
            self.db.execute("UPDATE cleanups SET state='done',finished_at=?,reason=NULL,retry_at=0 WHERE hash=?", (time.time(), plan["hash"]))
        log("cleanup", "local_payload_removed_cloud_retained")

    @staticmethod
    def same_download(plan, torrent):
        return (torrent_instance(plan) == torrent_instance(torrent)
                and plan['completion_on'] == torrent.get('completion_on')
                and (plan.get('download_path') or '') == (torrent.get('download_path') or '')
                and torrent.get('progress') == 1 and torrent.get('amount_left') == 0)

    @staticmethod
    def retained(torrent, actions):
        return any(a['action'] in {'cancel', 'delete'}
                   and any(torrent_instance(t) == torrent_instance(torrent) for t in a['payload'].get('torrents', []))
                   for a in actions)

    def remove_episodes(self, sources, plan):
        for item in plan['episodes']:
            # IDs are validated against the stored path before deleting; retries tolerate already-removed files.
            listing = sources.sonarr.json("GET", "/api/v3/episodefile", query={"seriesId": item["series_id"]})
            file = next((f for f in listing if f["id"] == item["file_id"]), None)
            if file:
                job = next(j for j in plan['jobs'] if j['identity'] == 'tv:' + str(item['file_id']))
                if (file.get("path") != item["path"] or file.get('size') != job['size']
                        or file.get('seriesId') != item['series_id']):
                    raise Failure("cleanup_episode_changed")
                local_path(file["path"], sources.media / "library/tv")
                episodes = sources.sonarr.json("GET", "/api/v3/episode", query={"seriesId": item["series_id"]})
                ids = [e["id"] for e in episodes if e.get("episodeFileId") == item["file_id"]]
                if not ids:
                    raise Failure("cleanup_episode_mapping_missing")
                series = sources.sonarr.json('GET', '/api/v3/series/' + str(item['series_id']))
                if not series.get('tvdbId'):
                    raise Failure('cleanup_episode_mapping_missing')
                keys = [(e['seasonNumber'],e['episodeNumber']) for e in episodes if e['id'] in ids]
                self.uploader.update(job, tvdb_id=series['tvdbId'], episode_keys=json.dumps(keys), state='published')
                sources.sonarr.json("PUT", "/api/v3/episode/monitor", {"episodeIds": ids, "monitored": False})
                sources.sonarr.request("DELETE", "/api/v3/episodefile/" + str(item["file_id"]))

    def run(self, sources, days=7, now=None):
        def retention():
            value = days
            if self.settings_path is not None and self.settings_path.exists():
                value = json.loads(self.settings_path.read_text()).get('retention_days', days)
            if type(value) is not int or not 0 <= value <= 3650:
                raise Failure('invalid_retention_days')
            return value

        configured_days = retention()
        if configured_days == 0:
            return
        if not sources.qb.headers.get('Cookie'):
            sources.login_qb()
        now = time.time() if now is None else now
        torrents = sources.qb.json("GET", "/api/v2/torrents/info")
        published_count = self.db.execute("SELECT count(*) FROM jobs WHERE state='published'").fetchone()[0]
        actions = task_actions(self.state/'uploads.sqlite3')
        for torrent in torrents:
            previous = self.db.execute('SELECT * FROM cleanups WHERE hash=?', (torrent['hash'],)).fetchone()
            if self.retained(torrent, actions):
                with self.db:
                    self.db.execute("DELETE FROM cleanups WHERE hash=? AND state!='done'", (torrent['hash'],))
                continue
            if previous:
                old = json.loads(previous['plan'])
                if previous['state'] == 'pending' and self.same_download(old, torrent):
                    continue
                if previous['state'] == 'done' and self.same_download(old, torrent):
                    continue
                if (previous['state'] == 'deferred' and previous['retry_at'] > now
                        and old.get('published_count') == published_count and old.get('retention_days') == configured_days
                        and self.same_download(old, torrent)):
                    continue
                with self.db:
                    self.db.execute('DELETE FROM cleanups WHERE hash=?', (torrent['hash'],))
            try:
                plan = self.plan(sources, torrent, configured_days, now)
                if plan:
                    with self.db:
                        self.db.execute("INSERT INTO cleanups(hash,plan,state,label) VALUES (?,?,'pending',?)", (torrent['hash'],json.dumps(plan),torrent.get('name','下载任务')))
            except (Failure,OSError,KeyError,ValueError,TypeError) as error:
                reason = str(error) if isinstance(error,Failure) else 'cleanup_check_failed'
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO cleanups(hash,plan,state,reason,retry_at,label) VALUES (?,?,'deferred',?,?,?)",
                        (torrent['hash'],json.dumps({k:torrent.get(k) for k in ('hash','category','save_path','download_path','added_on','completion_on')}
                         | {'published_count':published_count,'retention_days':configured_days}),reason,
                         now + (86400 if reason in {'cleanup_unsupported_disc','cleanup_subtitle_mapping_missing','cleanup_unarchived_media'} else 300),torrent.get('name','下载任务')))
                log('cleanup',reason)
        for row in self.db.execute("SELECT * FROM cleanups WHERE state='pending' AND retry_at<=?", (now,)).fetchall():
            try:
                plan=json.loads(row['plan'])
                with watch_lock(self.state), watch_lock(self.state/'task-controls'):
                    self.execute(sources, plan, retention(), now)
            except (Failure,OSError,KeyError,ValueError,TypeError) as error:
                reason=str(error) if isinstance(error,Failure) else 'cleanup_check_failed'
                delay=30 if reason in {'cleanup_waiting_for_stop','cleanup_waiting_for_removal','cleanup_waiting_for_files'} else min(3600,60*2**min(row['failures'],6))
                with self.db:
                    self.db.execute('UPDATE cleanups SET reason=?,retry_at=?,failures=failures+1 WHERE hash=?',(reason,now+delay,row['hash']))
                log('cleanup',reason)
