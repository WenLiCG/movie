"""Conservative, resumable cleanup of cloud-published torrent payloads via native APIs."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import urlencode

from uploader import Failure, VIDEO, SUBTITLES, DOWNLOADS, local_path, open_source, relative, verify, fingerprint, log, sample, disc
from archive import watch_lock


class Cleaner:
    def __init__(self, uploader, state):
        self.uploader, self.db, self.state = uploader, uploader.db, Path(state)
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

    def plan(self, sources, torrent, all_torrents, days, now):
        category = torrent.get("category")
        if (category not in DOWNLOADS or torrent.get("progress") != 1
                or torrent.get("amount_left") != 0 or torrent.get("completion_on", 0) <= 0
                or torrent["completion_on"] > now - days * 86400
                or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", torrent.get("hash", ""))
                or torrent.get("state", "").lower().startswith(("checking", "moving", "error", "missing"))):
            return None
        root = sources.media / "downloads" / DOWNLOADS[category][0]
        save = Path(torrent["save_path"])
        if save != root:
            local_path(save, root)
        files = sources.qb.json("GET", "/api/v2/torrents/files", query={"hash": torrent["hash"]})
        paths, jobs, episodes = [], [], []
        published = [dict(r) for r in self.db.execute("SELECT * FROM jobs WHERE state='published'")]
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
            with open_source(path, root) as handle:
                before = fingerprint(handle)
                digest = hashlib.file_digest(handle, "sha1").hexdigest()
                if fingerprint(handle) != before or job["sha1"] != digest or job["size"] != before[2]:
                    return None
            verify(self.uploader.remote.find(job["dest"]), job)
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
            jobs.append(job)
        if any(j.get('parent_id') and j['parent_id'] not in {v['id'] for v in jobs} for j in jobs):
            raise Failure('cleanup_subtitle_mapping_missing')
        if not jobs:
            return None
        # qB deletes files by torrent; refuse any shared file with another task, even outside our categories.
        owned = set(paths)
        for other in all_torrents:
            if other["hash"] == torrent["hash"]:
                continue
            for file in sources.qb.json("GET", "/api/v2/torrents/files", query={"hash": other["hash"]}):
                rel = relative(file["name"])
                if str(Path(other["save_path"]).joinpath(*rel.parts)) in owned:
                    return None
        return {"hash": torrent["hash"], "category": category, "save_path": str(save), "files": files,
                "completion_on": torrent["completion_on"], "paths": paths, "jobs": jobs, "episodes": episodes}

    def execute(self, sources, plan):
        torrents = sources.qb.json("GET", "/api/v2/torrents/info")
        current = next((t for t in torrents if t["hash"] == plan["hash"]), None)
        for job in plan["jobs"]:
            verify(self.uploader.remote.find(job["dest"]), job)
        if current:
            if current["category"] != plan["category"] or current["save_path"] != plan["save_path"]:
                raise Failure("cleanup_torrent_changed")
            files = sources.qb.json("GET", "/api/v2/torrents/files", query={"hash": plan["hash"]})
            fields = lambda values: [(f["name"], f["size"], f.get("priority")) for f in values]
            if fields(files) != fields(plan["files"]):
                raise Failure("cleanup_files_changed")
            root = sources.media / "downloads" / DOWNLOADS[plan["category"]][0]
            for path in plan["paths"]:
                local_path(path, root)
            # Revalidate persisted plans on every retry, including changed source contents and shared files.
            for job in plan["jobs"]:
                source = Path(job["source"])
                if not source.exists():
                    if plan["category"] != "sonarr-tv":
                        raise Failure("cleanup_source_missing")
                    source = next((Path(p) for p in plan["paths"] if Path(p).suffix.lower() in VIDEO | SUBTITLES
                                   and Path(p).stat().st_size == job["size"] and self.matches(p, root, job)), None)
                    if source is None:
                        raise Failure("cleanup_source_changed")
                source_root = root if source.is_relative_to(root) else sources.media / "library/tv"
                if not self.matches(source, source_root, job):
                    raise Failure("cleanup_source_changed")
            for other in torrents:
                if other["hash"] != plan["hash"]:
                    for file in sources.qb.json("GET", "/api/v2/torrents/files", query={"hash": other["hash"]}):
                        rel = relative(file["name"])
                        if str(Path(other["save_path"]).joinpath(*rel.parts)) in plan["paths"]:
                            raise Failure("cleanup_shared_file")
            self.form(sources, "stop", {"hashes": plan["hash"]})
            stopped = sources.qb.json("GET", "/api/v2/torrents/info", query={"hashes": plan["hash"]})
            if not stopped or stopped[0].get("state") not in {"stoppedUP", "pausedUP"}:
                raise Failure("cleanup_waiting_for_stop")
        with watch_lock(self.state):
            self.remove_episodes(sources, plan)
        if current:
            self.form(sources, "delete", {"hashes": plan["hash"], "deleteFiles": "true"})
            if sources.qb.json("GET", "/api/v2/torrents/info", query={"hashes": plan["hash"]}):
                raise Failure("cleanup_waiting_for_removal")
        extras = [j['source'] for j in plan['jobs'] if Path(j['source']).suffix.lower() in SUBTITLES]
        if any(Path(p).exists() for p in plan["paths"] + [i["path"] for i in plan["episodes"]] + extras):
            raise Failure("cleanup_waiting_for_files")
        with self.db:
            self.db.execute("UPDATE cleanups SET state='done',finished_at=?,reason=NULL,retry_at=0 WHERE hash=?", (time.time(), plan["hash"]))
        log("cleanup", "local_payload_removed_cloud_retained")

    def remove_episodes(self, sources, plan):
        for item in plan['episodes']:
            # IDs are validated against the stored path before deleting; retries tolerate already-removed files.
            listing = sources.sonarr.json("GET", "/api/v3/episodefile", query={"seriesId": item["series_id"]})
            file = next((f for f in listing if f["id"] == item["file_id"]), None)
            if file:
                if file.get("path") != item["path"]:
                    raise Failure("cleanup_episode_changed")
                local_path(file["path"], sources.media / "library/tv")
                episodes = sources.sonarr.json("GET", "/api/v3/episode", query={"seriesId": item["series_id"]})
                ids = [e["id"] for e in episodes if e.get("episodeFileId") == item["file_id"]]
                if not ids:
                    raise Failure("cleanup_episode_mapping_missing")
                series = sources.sonarr.json('GET', '/api/v3/series/' + str(item['series_id']))
                if not series.get('tvdbId'):
                    raise Failure('cleanup_episode_mapping_missing')
                job = next(j for j in plan['jobs'] if j['identity'] == 'tv:' + str(item['file_id']))
                keys = [(e['seasonNumber'],e['episodeNumber']) for e in episodes if e['id'] in ids]
                self.uploader.update(job, tvdb_id=series['tvdbId'], episode_keys=json.dumps(keys), state='published')
                sources.sonarr.json("PUT", "/api/v3/episode/monitor", {"episodeIds": ids, "monitored": False})
                sources.sonarr.request("DELETE", "/api/v3/episodefile/" + str(item["file_id"]))

    @staticmethod
    def matches(path, root, job):
        with open_source(path, root) as handle:
            before = fingerprint(handle)
            digest = hashlib.file_digest(handle, "sha1").hexdigest()
            return before[2] == job["size"] and digest == job["sha1"] and fingerprint(handle) == before

    def run(self, sources, days, now=None):
        if type(days) is not int or not 0 <= days <= 3650:
            raise Failure("invalid_retention_days")
        if days == 0:
            return
        # The source discovery authenticates qB each round; cleanup authenticates independently on discovery failure.
        sources.downloads()
        now = time.time() if now is None else now
        torrents = sources.qb.json("GET", "/api/v2/torrents/info")
        published_count = self.db.execute("SELECT count(*) FROM jobs WHERE state='published'").fetchone()[0]
        for torrent in torrents:
            previous = self.db.execute('SELECT * FROM cleanups WHERE hash=?', (torrent['hash'],)).fetchone()
            if previous:
                old = json.loads(previous['plan'])
                if previous['state'] == 'pending':
                    continue
                if previous['state'] == 'done' and old['completion_on'] == torrent.get('completion_on'):
                    continue
                if previous['state'] == 'deferred' and previous['retry_at'] > now and old.get('published_count') == published_count and old.get('retention_days') == days and old.get('completion_on') == torrent.get('completion_on'):
                    continue
                with self.db:
                    self.db.execute('DELETE FROM cleanups WHERE hash=?', (torrent['hash'],))
            try:
                plan = self.plan(sources, torrent, torrents, days, now)
                if plan:
                    with self.db:
                        self.db.execute("INSERT INTO cleanups(hash,plan,state,label) VALUES (?,?,'pending',?)", (torrent['hash'],json.dumps(plan),torrent.get('name','下载任务')))
            except (Failure,OSError,KeyError,ValueError,TypeError) as error:
                reason = str(error) if isinstance(error,Failure) else 'cleanup_check_failed'
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO cleanups(hash,plan,state,reason,retry_at,label) VALUES (?,?,'deferred',?,?,?)",
                        (torrent['hash'],json.dumps({'completion_on':torrent.get('completion_on'), 'published_count':published_count,'retention_days':days}),reason,
                         now + (86400 if reason in {'cleanup_unsupported_disc','cleanup_subtitle_mapping_missing'} else 300),torrent.get('name','下载任务')))
                log('cleanup',reason)
        for row in self.db.execute("SELECT * FROM cleanups WHERE state='pending' AND retry_at<=?", (now,)).fetchall():
            try:
                plan=json.loads(row['plan'])
                if plan['completion_on'] <= now-days*86400:
                    self.execute(sources,plan)
            except (Failure,OSError,KeyError,ValueError,TypeError) as error:
                reason=str(error) if isinstance(error,Failure) else 'cleanup_check_failed'
                delay=30 if reason in {'cleanup_waiting_for_stop','cleanup_waiting_for_removal','cleanup_waiting_for_files'} else min(3600,60*2**min(row['failures'],6))
                with self.db:
                    self.db.execute('UPDATE cleanups SET reason=?,retry_at=?,failures=failures+1 WHERE hash=?',(reason,now+delay,row['hash']))
                log('cleanup',reason)
