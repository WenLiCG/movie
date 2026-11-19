#!/usr/bin/env python3
"""MV-9b movie3 runtime checks on test host A. No credentials are logged.

Optional stdin JSON: {"user":"...","pw":"..."} for the ordinary-user path.
Credentials are never printed, never written to disk on A, and never placed in argv.
Does not modify verify_a.py. Host is movie3.g60.net on 127.0.0.1:18000 (HTTP).
"""
import json, socket, sys, re, urllib.parse, time

HOST_MOVIE3 = "movie3.g60.net"
HOST_MOVIE = "movie2.g60.net"
PORT = 18000
IP = "127.0.0.1"
ADMIN = {
    "X-Emby-Token": "fake-admin-token-for-gateway-test",
    "Authorization": 'MediaBrowser Client="EmbyWeb", Token=fake-admin-token-for-gateway-test',
    "Cookie": "emby-auth=fake-admin-token-for-gateway-test",
}
UID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
IID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VID = "cccccccccccccccccccccccccccccccc"
NO_STORE = "private, no-store"
PUBLIC_IMM = "public, max-age=31536000, immutable"


def redact(s):
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = re.sub(r'(?i)([?&]sign=)[^&\s"\']+', r'\1<redacted>', s)
    s = re.sub(r'(?i)(AccessToken|access_token|UserId)("?\s*[:=]\s*"?)[^"&\s,]+', r'\1\2<redacted>', s)
    return s


def decode_chunked(data):
    out = b""
    i = 0
    while i < len(data):
        nl = data.find(b"\r\n", i)
        if nl < 0:
            break
        try:
            sz = int(data[i:nl], 16)
        except ValueError:
            break
        if sz == 0:
            break
        start = nl + 2
        out += data[start:start + sz]
        i = start + sz + 2
    return out


def request(host, path, method="GET", headers=None, raw_path=None, body=None, timeout=15, port=PORT):
    target = raw_path if raw_path is not None else path
    hdrs = {"Host": "%s:%d" % (host, port), "Accept-Encoding": "identity", "Connection": "close"}
    if headers:
        hdrs.update(headers)
    payload = body or b""
    if not isinstance(payload, bytes):
        payload = payload.encode("utf-8")
    req = "%s %s HTTP/1.1\r\n" % (method, target)
    for k, v in hdrs.items():
        req += "%s: %s\r\n" % (k, v)
    if payload and "content-length" not in {k.lower() for k in hdrs}:
        req += "Content-Length: %d\r\n" % len(payload)
    req += "\r\n"
    sock = socket.create_connection((IP, port), timeout=timeout)
    try:
        sock.sendall(req.encode("latin1") + payload)
        buf = b""
        while True:
            try:
                chunk = sock.recv(16384)
            except (socket.timeout, TimeoutError):
                break
            if not chunk:
                break
            buf += chunk
            if b"\r\n\r\n" in buf:
                head, _, rest = buf.partition(b"\r\n\r\n")
                # 101 Switching Protocols keeps the socket open; headers are enough.
                try:
                    st = int(head.decode("latin1", "replace").split()[1])
                except Exception:
                    st = 0
                if st == 101:
                    break
                clen = None
                chunked = False
                for line in head.decode("latin1", "replace").split("\r\n")[1:]:
                    if line.lower().startswith("content-length:"):
                        clen = int(line.split(":", 1)[1].strip())
                    if line.lower().startswith("transfer-encoding:") and "chunked" in line.lower():
                        chunked = True
                if clen is not None and clen > 65536 and len(rest) >= 4096:
                    break
                if clen is not None and len(rest) >= clen:
                    break
                if chunked and b"0\r\n\r\n" in rest:
                    break
    finally:
        sock.close()
    if not buf:
        raise TimeoutError("empty response")
    head, _, data = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin1", "replace").split("\r\n")
    status = int(lines[0].split()[1])
    pairs = []
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            pairs.append((k.strip(), v.strip()))

    def get_all(name):
        return [v for k, v in pairs if k.lower() == name.lower()]

    te = get_all("Transfer-Encoding")
    if te and "chunked" in te[0].lower():
        data = decode_chunked(data)
    cl = get_all("Content-Length")
    return {
        "status": status,
        "cache_control": get_all("Cache-Control"),
        "referrer_policy": get_all("Referrer-Policy"),
        "vary": get_all("Vary"),
        "location": get_all("Location"),
        "content_type": get_all("Content-Type"),
        "content_range": get_all("Content-Range"),
        "content_length": cl,
        "all_headers": [(k, v) for k, v in pairs],
        "body_len": len(data),
        "body": data,
        "body_head": redact(data[:240].decode("utf-8", "replace")),
    }



def d6_redirect_reasons(r, method):
    """Return reasons the 3xx response still leaks movie2 / openlist/p / absolute URLs, or CL is wrong."""
    reasons = []
    if r["status"] not in (301, 302, 307, 308):
        reasons.append("status %s" % r["status"])
    for k, v in (r.get("all_headers") or []):
        lv = v.lower()
        if "movie2" in lv:
            reasons.append("header %s leaks movie2" % k)
        if "/openlist/p/" in lv:
            reasons.append("header %s still /openlist/p/" % k)
        if re.search(r"(?i)https?://", v):
            reasons.append("header %s has absolute URL" % k)
    body = r.get("body") or b""
    if method == "HEAD" and body:
        reasons.append("HEAD body not empty len=%s" % len(body))
    if b"movie2" in body.lower():
        reasons.append("body leaks movie2")
    if re.search(br"(?i)/openlist/p/", body):
        reasons.append("body still /openlist/p/")
    if re.search(br"(?i)https?://", body):
        reasons.append("body has absolute URL")
    cl = r.get("content_length") or []
    if len(cl) != 1 or int(cl[0]) != r["body_len"]:
        reasons.append("Content-Length %s != body_len %s" % (cl, r["body_len"]))
    loc = r["location"][0] if r.get("location") else ""
    if "movie2" in loc.lower() or "/openlist/p/" in loc.lower():
        reasons.append("location %s" % redact(loc))
    if method != "root" and r["status"] == 307:
        if "/openlist/d/" not in loc.lower() or loc.lower().startswith("http"):
            reasons.append("location not relative /openlist/d/: %s" % redact(loc))
    if r["cache_control"] != [NO_STORE]:
        reasons.append("cc=%s" % r["cache_control"])
    return reasons


def classify(r):
    body = r["body_head"].strip()
    if r["status"] == 403 and (body.startswith("Forbidden") or r["body"][:9] == b"Forbidden" or (r["body_len"] == 0 and r["status"] == 403)):
        # HEAD 403 has empty body — still gateway 403 if we asked HEAD; callers distinguish
        if body.startswith("Forbidden") or r["body"][:9] == b"Forbidden":
            return "gateway-403"
    if r["status"] == 404 and "Not found" in body:
        return "gateway-404"
    return "upstream-or-other"


def check(name, expect, host, path, method="GET", headers=None, raw_path=None, body=None):
    r = request(host, path, method=method, headers=headers, raw_path=raw_path, body=body)
    r.update({"name": name, "path": path, "host": host, "expect": expect, "method": method})
    r["classification"] = classify(r)
    # For HEAD gateway 403/404, body is empty — treat status+content-type as enough when expected
    if method == "HEAD" and expect in ("gateway-403", "gateway-404"):
        want = 403 if expect == "gateway-403" else 404
        if r["status"] == want:
            r["classification"] = expect
    ok, reasons = True, []
    cls = r["classification"]
    if expect == "gateway-403":
        if cls != "gateway-403" and not (method == "HEAD" and r["status"] == 403):
            if not (r["status"] in (400, 403) and "upstream" not in cls):
                ok = False
                reasons.append("want gateway 403, got %s %s" % (r["status"], r["body_head"][:80]))
    elif expect == "gateway-404":
        if cls != "gateway-404" and not (method == "HEAD" and r["status"] == 404):
            ok = False
            reasons.append("want gateway 404, got %s %s" % (r["status"], r["body_head"][:80]))
    elif expect == "allowed":
        if cls in ("gateway-403", "gateway-404"):
            ok = False
            reasons.append("blocked by gateway: %s %s" % (r["status"], r["body_head"][:80]))
    elif expect == "redirect-movie":
        if r["status"] not in (301, 302, 307, 308):
            ok = False
            reasons.append("status %s" % r["status"])
        loc = r["location"][0] if r["location"] else ""
        if not loc.lower().rstrip("/").endswith("/movie") and "/movie/" not in loc.lower():
            ok = False
            reasons.append("location %r" % loc)
        if loc.lower().startswith("http"):
            ok = False
            reasons.append("absolute location %r" % loc)
        if r["body_len"] != 0:
            ok = False
            reasons.append("body_len=%s" % r["body_len"])
    elif expect == "redirect-web":
        if r["status"] not in (301, 302, 307, 308):
            ok = False
            reasons.append("status %s" % r["status"])
        loc = r["location"][0] if r["location"] else ""
        if "/web/" not in loc and "/movie/web/" not in loc:
            ok = False
            reasons.append("location %r" % loc)
    elif expect == "ws-denied":
        if r["status"] == 101 or (cls != "gateway-403" and not (r["status"] == 403)):
            ok = False
            reasons.append("ws status %s cls %s" % (r["status"], cls))
    elif expect == "http-206":
        if r["status"] != 206:
            ok = False
            reasons.append("status %s cr=%s loc=%s" % (r["status"], r["content_range"], redact(r["location"][0] if r["location"] else "")))
    elif expect == "redirect-openlist-d":
        if r["status"] not in (301, 302, 307, 308):
            ok = False
            reasons.append("status %s" % r["status"])
        loc = r["location"][0] if r["location"] else ""
        if "movie2" in loc.lower() or HOST_MOVIE in loc.lower():
            ok = False
            reasons.append("location still mentions movie2: %s" % redact(loc))
        if "/openlist/d/" not in loc.lower():
            ok = False
            reasons.append("location not /openlist/d/: %s" % redact(loc))
        if "/openlist/p/" in loc.lower():
            ok = False
            reasons.append("location still /openlist/p/: %s" % redact(loc))
    if r["referrer_policy"] != ["no-referrer"] and r["status"] != 400:
        # 206 from OpenList: site deferred Referrer-Policy should still apply
        if r["referrer_policy"] != ["no-referrer"]:
            ok = False
            reasons.append("rp=%s" % r["referrer_policy"])
    if expect in ("gateway-403", "gateway-404", "redirect-web", "redirect-movie", "ws-denied", "http-206", "redirect-openlist-d"):
        if r["status"] != 400 and r["cache_control"] != [NO_STORE]:
            ok = False
            reasons.append("cc=%s" % r["cache_control"])
    # D1/D2: never public / short-cache on errors
    if expect in ("gateway-403", "gateway-404", "ws-denied"):
        joined = ",".join(r["cache_control"]).lower()
        if "public" in joined or "max-age=31536000" in joined or "max-age=300" in joined:
            ok = False
            reasons.append("error cc must not be public/short: %s" % r["cache_control"])
    r["ok"] = ok
    r["reasons"] = reasons
    # drop raw body from report (may be large / binary)
    r.pop("body", None)
    return r


def slim(c):
    return {
        "name": c.get("name"),
        "path": redact(c.get("path") or ""),
        "host": c.get("host"),
        "method": c.get("method"),
        "expect": c.get("expect"),
        "ok": c.get("ok"),
        "status": c.get("status"),
        "cls": c.get("classification"),
        "cc": c.get("cache_control"),
        "rp": c.get("referrer_policy"),
        "loc": redact(c["location"][0]) if c.get("location") else "",
        "cr": c.get("content_range"),
        "reasons": c.get("reasons"),
        "body_head": c.get("body_head", "")[:120],
    }


def rec(cases, **kwargs):
    c = dict(kwargs)
    c.setdefault("cache_control", [])
    c.setdefault("referrer_policy", [])
    c.setdefault("location", [])
    c.setdefault("content_range", [])
    c.setdefault("reasons", [])
    c.setdefault("body_head", "")
    c.setdefault("classification", "")
    cases.append(c)
    return c


def main():
    creds = None
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if raw.strip():
        try:
            creds = json.loads(raw)
        except Exception:
            creds = None
    user = (creds or {}).get("user") or ""
    pw = (creds or {}).get("pw") or ""
    if creds is not None:
        creds["pw"] = ""
    cases = []
    ADMIN = {
        "X-Emby-Token": "fake-admin-token-for-gateway-test",
        "Authorization": 'MediaBrowser Client="EmbyWeb", Token=fake-admin-token-for-gateway-test',
        "Cookie": "cinema=forged-admin-session; emby-auth=fake",
    }
    UID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    IID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    VID = "cccccccccccccccccccccccccccccccc"
    WS = {
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
    }

    # ---- movie2 regression (unchanged site) ----
    cases.append(check("movie2 discover", "allowed", HOST_MOVIE, "/discover/"))
    cases.append(check("movie2 ge2o 404", "gateway-404", HOST_MOVIE, "/ge2o"))
    cases.append(check("movie2 Plugins reaches relay", "allowed", HOST_MOVIE, "/Plugins"))
    cases.append(check("movie2 web", "allowed", HOST_MOVIE, "/web/index.html"))
    r_m2 = request(HOST_MOVIE, "/openlist/p/aliyun/Movies/MV4-Test-Long/MV4-Test-Long.mkv?sign=test")
    ok_m2 = r_m2["status"] in (302, 307, 308) and (r_m2["location"] and "/discover" in r_m2["location"][0])
    rec(cases, name="movie2 openlist still forward_auth", path="/openlist/p/…",
        host=HOST_MOVIE, method="GET", expect="302 /discover/ (cinema_admin)",
        status=r_m2["status"], location=r_m2["location"],
        cache_control=r_m2["cache_control"], referrer_policy=r_m2["referrer_policy"],
        classification="movie2-auth-redirect", ok=ok_m2,
        reasons=[] if ok_m2 else ["status=%s loc=%s" % (r_m2["status"], r_m2["location"])],
        body_head=r_m2["body_head"][:80], content_range=r_m2["content_range"])

    # ---- prefix boundaries ----
    for path in ("/movies", "/movies/", "/movies/web/index.html",
                 "/movie2", "/movie2/", "/movieX", "/movieX/web/index.html"):
        cases.append(check("lookalike %s" % path, "gateway-403", HOST_MOVIE3, path))
    raws = [
        "/movie/%2e%2e/discover/",
        "/movie/%252e%252e/",
        "/movie%2fweb/index.html",
        "//movie/",
        "/movie//web/index.html",
        "/emby/%252e%252e/Plugins",
        "/movie/web/%252e%252e/Plugins",
        "/web/%252e%252e/Plugins",
    ]
    for path in raws:
        cases.append(check("D5 raw %s" % path, "gateway-403", HOST_MOVIE3, path, raw_path=path))

    # literal /movie/../discover/ == /discover/ (movie3 rules)
    d_direct = request(HOST_MOVIE3, "/discover/")
    d_dot = request(HOST_MOVIE3, "/movie/../discover/")
    same = d_direct["status"] == d_dot["status"] == 200
    rec(cases, name="literal /movie/../discover/ same as /discover/",
        path="/movie/../discover/", host=HOST_MOVIE3, method="GET",
        expect="same 200 as /discover/", status=d_dot["status"],
        cache_control=d_dot["cache_control"], referrer_policy=d_dot["referrer_policy"],
        location=d_dot["location"],
        ok=same, reasons=[] if same else [
            "direct=%s dot=%s" % (d_direct["status"], d_dot["status"])],
        body_head="direct=%s dot=%s" % (d_direct["status"], d_dot["status"]),
        classification="prefix-boundary")

    # ---- redirects ----
    cases.append(check("root / -> /movie/", "redirect-movie", HOST_MOVIE3, "/"))
    cases.append(check("/movie -> /movie/", "redirect-movie", HOST_MOVIE3, "/movie"))
    r_ms = request(HOST_MOVIE3, "/movie/")
    loc = r_ms["location"][0] if r_ms["location"] else ""
    ok_ms = (r_ms["status"] in (301, 302, 307, 308)
             and loc.lower() == "/movie/web/index.html"
             and r_ms["body_len"] == 0
             and r_ms["content_length"] == ["0"]
             and "movie2" not in loc.lower())
    rec(cases, name="/movie/ -> /movie/web/index.html", path="/movie/",
        host=HOST_MOVIE3, method="GET", expect="302 /movie/web/index.html empty",
        status=r_ms["status"], location=r_ms["location"],
        cache_control=r_ms["cache_control"], referrer_policy=r_ms["referrer_policy"],
        content_range=r_ms["content_range"],
        ok=ok_ms, reasons=[] if ok_ms else ["status=%s loc=%s cl=%s bl=%s" % (
            r_ms["status"], loc, r_ms["content_length"], r_ms["body_len"])],
        body_head="cl=%s bl=%s" % (r_ms["content_length"], r_ms["body_len"]),
        classification="redirect")
    for path in ("/web/", "/web/index.html", "/web/main.js?v=1"):
        r = request(HOST_MOVIE3, path)
        loc = r["location"][0] if r["location"] else ""
        want = "/movie" + path
        ok = (r["status"] in (301, 302, 307, 308) and loc == want
              and r["body_len"] == 0 and not loc.lower().startswith("http"))
        rec(cases, name="root %s bounce" % path, path=path, host=HOST_MOVIE3,
            method="GET", expect="308 %s" % want, status=r["status"],
            location=r["location"], cache_control=r["cache_control"],
            referrer_policy=r["referrer_policy"], content_range=r["content_range"],
            ok=ok, reasons=[] if ok else ["status=%s loc=%s" % (r["status"], loc)],
            body_head="bl=%s" % r["body_len"], classification="redirect")

    # D6 cleanliness of root and /movie redirects
    for path, method, expect_loc in (("/", "GET", r"(?i)^/movie/?$"),
                                     ("/", "HEAD", r"(?i)^/movie/?$"),
                                     ("/movie", "GET", r"(?i)^/movie/$")):
        rr = request(HOST_MOVIE3, path, method=method)
        reasons = d6_redirect_reasons(rr, "root")
        loc = rr["location"][0] if rr["location"] else ""
        if not re.search(expect_loc, loc):
            reasons = list(reasons) + ["location %s" % loc]
        rec(cases, name="D6 %s %s clean" % (method, path), path=path,
            host=HOST_MOVIE3, method=method, expect="3xx clean " + expect_loc,
            status=rr["status"], location=rr["location"],
            cache_control=rr["cache_control"], referrer_policy=rr["referrer_policy"],
            content_range=rr["content_range"],
            ok=not reasons, reasons=reasons,
            body_head=json.dumps({"bl": rr["body_len"], "cl": rr.get("content_length")}),
            classification="d6")

    # ---- management 404 / discover 403 ----
    for path in ("/search", "/search/", "/sonarr", "/qb", "/openlist",
                 "/openlist/", "/openlist/api", "/ge2o",
                 "/movie/search/", "/movie/web/dashboard",
                 "/emby/web/dashboard", "/emby/search/"):
        cases.append(check("mgmt 404 %s" % path, "gateway-404", HOST_MOVIE3, path, headers=ADMIN))

    for method, path in (
            ("POST", "/discover/api/login"),
            ("GET", "/discover/api/session"),
            ("GET", "/discover/api/admin-check"),
            ("GET", "/discover/api/recommendations"),
            ("GET", "/discover/api/status"),
            ("POST", "/discover/api/watch-session"),
            ("POST", "/discover/api/configure"),
    ):
        h = dict(ADMIN)
        h["Content-Type"] = "application/json"
        h["Origin"] = "http://movie3.g60.net:18000"
        body = b'{"x":1}' if method == "POST" else None
        r = request(HOST_MOVIE3, path, method=method, headers=h, body=body)
        set_c = [v for k, v in (r.get("all_headers") or []) if k.lower() == "set-cookie"]
        leaked = any(v.lower().lstrip().startswith("cinema=") for v in set_c)
        ok = r["status"] == 403 and r["cache_control"] == [NO_STORE] and not leaked
        rec(cases, name="discover api 403 %s %s" % (method, path), path=path,
            host=HOST_MOVIE3, method=method, expect="403 no cinema Set-Cookie",
            status=r["status"], cache_control=r["cache_control"],
            referrer_policy=r["referrer_policy"], location=r["location"],
            content_range=r["content_range"],
            ok=ok, reasons=[] if ok else [
                "status=%s cc=%s set-cookie=%s" % (r["status"], r["cache_control"],
                                                   ["cinema" if v.lower().lstrip().startswith("cinema=") else "other" for v in set_c])],
            body_head="set-cookie-count=%s leaked=%s" % (len(set_c), leaked),
            classification="discover-403")

    rds = request(HOST_MOVIE3, "/discover/")
    ok_ds = rds["status"] == 200
    rec(cases, name="discover static allowed", path="/discover/", host=HOST_MOVIE3,
        method="GET", expect="200", status=rds["status"],
        cache_control=rds["cache_control"], referrer_policy=rds["referrer_policy"],
        location=rds["location"], content_range=rds["content_range"],
        ok=ok_ds, reasons=[] if ok_ds else ["status=%s" % rds["status"]],
        body_head="len=%s" % rds["body_len"], classification="discover-static")

    # ---- D1-D6 style checks on BOTH /movie and /emby prefixes ----
    allow = [
        ("GET", "/Users/Me"),
        ("GET", "/System/Info/Public"),
        ("GET", "/System/Ping"),
        ("GET", "/web/index.html"),
        ("POST", "/Users/AuthenticateByName"),
    ]
    for prefix in ("/movie", "/emby"):
        for method, path in allow:
            full = prefix + path
            if method == "POST":
                r = request(HOST_MOVIE3, full, method="POST",
                            headers={"Content-Type": "application/json",
                                     "X-Emby-Authorization": 'MediaBrowser Client="MV9b", Device="box", DeviceId="mv9b", Version="1"'},
                            body=b'{"Username":"no-such-user","Pw":"x"}')
                # 200 or 401 from upstream is fine; gateway 403 is not
                ok = r["status"] != 403 and r["status"] != 404 and classify(r) != "gateway-403"
                rec(cases, name="allow %s %s" % (method, full), path=full,
                    host=HOST_MOVIE3, method=method, expect="reaches upstream",
                    status=r["status"], cache_control=r["cache_control"],
                    referrer_policy=r["referrer_policy"], location=r["location"],
                    content_range=r["content_range"], ok=ok,
                    reasons=[] if ok else ["status=%s cls=%s" % (r["status"], classify(r))],
                    body_head="status=%s" % r["status"], classification="allow")
            else:
                cases.append(check("allow %s" % full, "allowed", HOST_MOVIE3, full, method=method))

        cases.append(check("deny Plugins %s" % prefix, "gateway-403", HOST_MOVIE3,
                           prefix + "/Plugins", headers=ADMIN))
        cases.append(check("deny Users list %s" % prefix, "gateway-403", HOST_MOVIE3,
                           prefix + "/Users", headers=ADMIN))
        cases.append(check("deny System/Info unauth %s" % prefix, "gateway-403", HOST_MOVIE3,
                           prefix + "/System/Info"))
        cases.append(check("allow System/Info token %s" % prefix, "allowed", HOST_MOVIE3,
                           prefix + "/System/Info", headers=ADMIN))
        cases.append(check("deny System/Logs %s" % prefix, "gateway-403", HOST_MOVIE3,
                           prefix + "/System/Logs", headers=ADMIN))
        cases.append(check("allow Users/Public %s" % prefix, "allowed", HOST_MOVIE3,
                           prefix + "/Users/Public"))
        pr = request(HOST_MOVIE3, prefix + "/Users/Public")
        body = (pr.get("body") or b"").strip()
        empty_ok = pr["status"] == 200 and body == b"[]"
        rec(cases, name="Users/Public empty %s" % prefix, path=prefix + "/Users/Public",
            host=HOST_MOVIE3, method="GET", expect="200 []",
            status=pr["status"], cache_control=pr["cache_control"],
            referrer_policy=pr["referrer_policy"], location=pr["location"],
            content_range=pr["content_range"],
            ok=empty_ok, reasons=[] if empty_ok else [
                "status=%s body_len=%s" % (pr["status"], pr.get("body_len"))],
            body_head="len=%s empty=%s" % (pr.get("body_len"), body == b"[]"),
            classification="users-public-empty")
        cases.append(check("deny write DELETE %s" % prefix, "gateway-403", HOST_MOVIE3,
                           prefix + "/Users/" + UID, method="DELETE", headers=ADMIN))
        cases.append(check("D1 // versioned js %s" % prefix, "gateway-403", HOST_MOVIE3,
                           prefix + "/web//apploader.js?v=4.10.2.0",
                           raw_path=prefix + "/web//apploader.js?v=4.10.2.0"))
        cases.append(check("WS on image denied %s" % prefix, "ws-denied", HOST_MOVIE3,
                           prefix + "/Items/" + IID + "/Images/Primary", headers=WS))

    # websocket allowed on /embywebsocket
    for path in ("/embywebsocket", "/movie/embywebsocket", "/emby/embywebsocket"):
        r = request(HOST_MOVIE3, path, headers=WS, timeout=3)
        # 101 = websocket connected; 200 = stub/HTTP fallback. Gateway 403 is failure.
        ok = r["status"] in (101, 200, 400, 426) and classify(r) != "gateway-403"
        rec(cases, name="WS allowed %s" % path, path=path, host=HOST_MOVIE3,
            method="GET", expect="101 or upstream (not gateway 403)", status=r["status"],
            cache_control=r["cache_control"], referrer_policy=r["referrer_policy"],
            location=r["location"], content_range=r["content_range"],
            ok=ok, reasons=[] if ok else ["status=%s cls=%s" % (r["status"], classify(r))],
            body_head="status=%s" % r["status"], classification="websocket")

    # unsigned openlist /d/ is 403; UI is 404
    cases.append(check("openlist d no sign", "gateway-403", HOST_MOVIE3,
                       "/openlist/d/aliyun/Movies/x.mkv"))
    cases.append(check("openlist d empty sign", "gateway-403", HOST_MOVIE3,
                       "/openlist/d/aliyun/Movies/x.mkv?sign="))
    cases.append(check("openlist UI 404", "gateway-404", HOST_MOVIE3, "/openlist/"))

    # ---- ordinary user path on /movie ----
    user_notes = {"user_provided": bool(user and pw)}
    if user and pw:
        auth_h = {
            "X-Emby-Authorization": 'MediaBrowser Client="MV9b", Device="box", DeviceId="mv9b-verify", Version="1.0.0"',
            "Content-Type": "application/json",
        }
        login_body = json.dumps({"Username": user, "Pw": pw})
        # Try /movie first, also record /emby
        lr = request(HOST_MOVIE3, "/movie/Users/AuthenticateByName", method="POST",
                     headers=auth_h, body=login_body)
        try:
            js = json.loads(lr["body"].decode("utf-8"))
        except Exception:
            js = {}
        token = js.get("AccessToken")
        ui = js.get("User") or {}
        uid = ui.get("Id")
        is_admin = (ui.get("Policy") or {}).get("IsAdministrator")
        name_ok = ui.get("Name") == "mv4viewer"
        login_ok = lr["status"] == 200 and bool(token) and is_admin is False and name_ok
        rec(cases, name="U login /movie/Users/AuthenticateByName",
            path="/movie/Users/AuthenticateByName", host=HOST_MOVIE3, method="POST",
            expect="200 non-admin token", status=lr["status"],
            cache_control=lr["cache_control"], referrer_policy=lr["referrer_policy"],
            location=lr["location"], content_range=lr["content_range"],
            ok=login_ok and lr["cache_control"] == [NO_STORE],
            reasons=[] if login_ok else ["status=%s token=%s admin=%s name_ok=%s" % (
                lr["status"], bool(token), is_admin, name_ok)],
            body_head="token_len=%s uid_len=%s is_admin=%s" % (
                len(token or ""), len(uid or ""), is_admin),
            classification="login")
        user = pw = login_body = ""
        if login_ok:
            th = {
                "X-Emby-Token": token,
                "X-Emby-Authorization": 'MediaBrowser Client="MV9b", Device="box", DeviceId="mv9b-verify", Version="1.0.0", Token="%s"' % token,
            }
            # Emby 4.10 treats /Users/Me as a Guid ("Unrecognized Guid format");
            # the working equivalent is /Users/{id} on both prefixes.
            for prefix in ("/movie", "/emby"):
                mr = request(HOST_MOVIE3, prefix + "/Users/" + uid, headers=th)
                rec(cases, name="U Me %s" % prefix, path=prefix + "/Users/{id}",
                    host=HOST_MOVIE3, method="GET", expect="200",
                    status=mr["status"], cache_control=mr["cache_control"],
                    referrer_policy=mr["referrer_policy"], location=mr["location"],
                    content_range=mr["content_range"],
                    ok=mr["status"] == 200 and mr["cache_control"] == [NO_STORE],
                    reasons=[] if mr["status"] == 200 else ["status=%s" % mr["status"]],
                    body_head="len=%s" % mr["body_len"], classification="user")
            vr = request(HOST_MOVIE3, "/movie/Users/%s/Views" % uid, headers=th)
            rec(cases, name="U Views /movie", path="/movie/Users/{id}/Views",
                host=HOST_MOVIE3, method="GET", expect="200", status=vr["status"],
                cache_control=vr["cache_control"], referrer_policy=vr["referrer_policy"],
                location=vr["location"], content_range=vr["content_range"],
                ok=vr["status"] == 200, reasons=[] if vr["status"] == 200 else ["status=%s" % vr["status"]],
                body_head="len=%s" % vr["body_len"], classification="user")
            ir = request(HOST_MOVIE3, "/movie/Users/%s/Items?IncludeItemTypes=Movie&Recursive=true" % uid, headers=th)
            item_id = None
            n_items = 0
            try:
                ijs = json.loads(ir["body"].decode("utf-8"))
                arr = ijs.get("Items") or []
                n_items = len(arr)
                for it in arr:
                    if "MV4-Test-Long" in (it.get("Name") or ""):
                        item_id = it.get("Id")
                        break
                if not item_id and arr:
                    item_id = arr[0].get("Id")
            except Exception:
                arr = []
            items_ok = ir["status"] == 200 and n_items >= 1 and bool(item_id)
            rec(cases, name="U Items movie wall /movie", path="/movie/Users/{id}/Items",
                host=HOST_MOVIE3, method="GET", expect="200 with test movie",
                status=ir["status"], cache_control=ir["cache_control"],
                referrer_policy=ir["referrer_policy"], location=ir["location"],
                content_range=ir["content_range"], ok=items_ok,
                reasons=[] if items_ok else ["status=%s count=%s" % (ir["status"], n_items)],
                body_head="count=%s id_len=%s" % (n_items, len(item_id or "")),
                classification="user")
            # details
            if item_id:
                # This Emby build 404s on GET /Items/{id}; details are under /Users/{id}/Items/{id}.
                dr = request(HOST_MOVIE3, "/movie/Users/%s/Items/%s" % (uid, item_id), headers=th)
                rec(cases, name="U item details /movie", path="/movie/Users/{id}/Items/{id}",
                    host=HOST_MOVIE3, method="GET", expect="200", status=dr["status"],
                    cache_control=dr["cache_control"], referrer_policy=dr["referrer_policy"],
                    location=dr["location"], content_range=dr["content_range"],
                    ok=dr["status"] == 200,
                    reasons=[] if dr["status"] == 200 else ["status=%s" % dr["status"]],
                    body_head="len=%s" % dr["body_len"], classification="user")
            pb_path = "/movie/Items/%s/PlaybackInfo?UserId=%s&IsPlayback=true" % (item_id, uid)
            pbr = request(HOST_MOVIE3, pb_path, method="POST",
                          headers={**th, "Content-Type": "application/json"},
                          body=json.dumps({"DeviceProfile": {}}))
            ms_id = None
            srcs = []
            try:
                pjs = json.loads(pbr["body"].decode("utf-8"))
                srcs = pjs.get("MediaSources") or []
                if srcs:
                    ms_id = srcs[0].get("Id")
            except Exception:
                pass
            pb_ok = pbr["status"] == 200 and bool(ms_id)
            rec(cases, name="U PlaybackInfo POST /movie", path="/movie/Items/{id}/PlaybackInfo",
                host=HOST_MOVIE3, method="POST", expect="200 MediaSource",
                status=pbr["status"], cache_control=pbr["cache_control"],
                referrer_policy=pbr["referrer_policy"], location=pbr["location"],
                content_range=pbr["content_range"],
                ok=pb_ok and pbr["cache_control"] == [NO_STORE],
                reasons=[] if pb_ok else ["status=%s ms=%s" % (pbr["status"], bool(ms_id))],
                body_head="sources=%s ms_id_len=%s" % (len(srcs), len(ms_id or "")),
                classification="user")
            # Stream on BOTH prefixes
            for prefix in ("/movie", "/emby"):
                stream = prefix + "/Videos/%s/stream?static=true&MediaSourceId=%s" % (
                    item_id, urllib.parse.quote(ms_id or ""))
                sr = request(HOST_MOVIE3, stream, headers={**th, "Range": "bytes=0-1023"})
                loc = sr["location"][0] if sr["location"] else ""
                loc_ok = (sr["status"] == 307
                          and "movie2" not in loc.lower()
                          and HOST_MOVIE not in loc.lower()
                          and "/openlist/d/" in loc.lower()
                          and "/openlist/p/" not in loc.lower()
                          and "sign=" in loc
                          and not loc.lower().startswith("http")
                          and sr["cache_control"] == [NO_STORE])
                d6 = d6_redirect_reasons(sr, "GET")
                rec(cases, name="U stream 307 %s" % prefix, path=prefix + "/Videos/{id}/stream",
                    host=HOST_MOVIE3, method="GET", expect="307 /openlist/d no movie2",
                    status=sr["status"], cache_control=sr["cache_control"],
                    referrer_policy=sr["referrer_policy"],
                    location=[redact(loc)], content_range=sr["content_range"],
                    ok=loc_ok and not d6,
                    reasons=([] if loc_ok else ["status=%s loc=%s" % (sr["status"], redact(loc))]) + d6,
                    body_head=json.dumps({"has_sign": "sign=" in loc, "has_d": "/openlist/d/" in loc.lower(),
                                          "has_movie2": "movie2" in loc.lower(), "bl": sr["body_len"],
                                          "cl": sr.get("content_length")}),
                    classification="user-stream")
                if loc and loc_ok:
                    follow_path = loc
                    fr = request(HOST_MOVIE3, follow_path, headers={"Range": "bytes=0-1023"})
                    follow_ok = fr["status"] == 206 and fr["content_range"] and fr["cache_control"] == [NO_STORE]
                    rec(cases, name="U follow 307 206 %s" % prefix, path="/openlist/d/…",
                        host=HOST_MOVIE3, method="GET", expect="206",
                        status=fr["status"], cache_control=fr["cache_control"],
                        referrer_policy=fr["referrer_policy"],
                        location=[redact(x) for x in (fr["location"] or [])],
                        content_range=fr["content_range"], ok=follow_ok,
                        reasons=[] if follow_ok else ["status=%s cr=%s cc=%s" % (
                            fr["status"], fr["content_range"], fr["cache_control"])],
                        body_head="cr=%s len=%s" % (fr["content_range"], fr["body_len"]),
                        classification="user-follow")
                    fr2 = request(HOST_MOVIE3, follow_path, headers={"Range": "bytes=1000000-1001023"})
                    seek_ok = fr2["status"] == 206 and fr2["content_range"] and fr2["cache_control"] == [NO_STORE]
                    rec(cases, name="U seek 206 %s" % prefix, path="/openlist/d/…",
                        host=HOST_MOVIE3, method="GET", expect="206",
                        status=fr2["status"], cache_control=fr2["cache_control"],
                        referrer_policy=fr2["referrer_policy"],
                        location=[redact(x) for x in (fr2["location"] or [])],
                        content_range=fr2["content_range"], ok=seek_ok,
                        reasons=[] if seek_ok else ["status=%s cr=%s" % (fr2["status"], fr2["content_range"])],
                        body_head="cr=%s" % fr2["content_range"],
                        classification="user-follow")
                    follow_path = ""
            # Playing*
            play_body = json.dumps({"ItemId": item_id, "PlaySessionId": "abcdabcdabcdabcdabcdabcdabcdabcd", "PositionTicks": 0})
            for pth, pname in (("/movie/Sessions/Playing", "Playing"),
                               ("/movie/Sessions/Playing/Progress", "Progress"),
                               ("/movie/Sessions/Playing/Ping", "Ping"),
                               ("/movie/Sessions/Playing/Stopped", "Stopped"),
                               ("/emby/Sessions/Playing", "Playing-emby")):
                pr = request(HOST_MOVIE3, pth, method="POST",
                             headers={**th, "Content-Type": "application/json"}, body=play_body)
                pok = pr["status"] in (200, 204) and classify(pr) != "gateway-403"
                rec(cases, name="U POST %s" % pname, path=pth, host=HOST_MOVIE3,
                    method="POST", expect="upstream 200/204", status=pr["status"],
                    cache_control=pr["cache_control"], referrer_policy=pr["referrer_policy"],
                    location=pr["location"], content_range=pr["content_range"],
                    ok=pok, reasons=[] if pok else ["status=%s" % pr["status"]],
                    body_head="status=%s" % pr["status"], classification="user-post")
            # web entry loads
            wr = request(HOST_MOVIE3, "/movie/web/index.html")
            rec(cases, name="U web index /movie", path="/movie/web/index.html",
                host=HOST_MOVIE3, method="GET", expect="200", status=wr["status"],
                cache_control=wr["cache_control"], referrer_policy=wr["referrer_policy"],
                location=wr["location"], content_range=wr["content_range"],
                ok=wr["status"] == 200,
                reasons=[] if wr["status"] == 200 else ["status=%s" % wr["status"]],
                body_head="len=%s" % wr["body_len"], classification="user")
            token = th = ""
    else:
        rec(cases, name="U user path skipped (no credentials on stdin)",
            path="", host=HOST_MOVIE3, method="", expect="stdin creds",
            status=0, ok=False, reasons=["no ordinary-user credentials provided"],
            classification="skipped-user")

    failed = [c for c in cases if not c["ok"]]
    report = {
        "total": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "user_notes": user_notes,
        "failed_names": [slim(c) for c in failed],
        "cases": [slim(c) for c in cases],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("SUMMARY total=%d passed=%d failed=%d" % (report["total"], report["passed"], report["failed"]))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
