#!/usr/bin/env python3
"""MV-9b follow-up runtime checks on test host A. No credentials are logged.

HOST_EMBY is movie3.g60.net (the public wall). Historic bare Emby paths are
mapped onto /emby or /movie. A separate negative check confirms the old
emby.g60.net site does not return an Emby public payload. An empty 200 is
not evidence that the host has stopped serving or refused the connection.

Optional stdin JSON: {"user":"...","pw":"..."} for the ordinary-user path
(login, wall, PlaybackInfo, follow 307 to 206, sign rejection). Credentials
are never printed, never written to disk on A, and never placed in argv.
"""
import json, socket, sys, re, urllib.parse, time

HOST_EMBY = "movie3.g60.net"
HOST_EMBY_LEGACY = "emby.g60.net"
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

_KEEP_PREFIXES = (
    "/emby", "/mediabrowser", "/movie", "/openlist", "/discover",
    "/search", "/sonarr", "/qb", "/ge2o", "/embywebsocket",
)


def wall_path(path):
    """Map historic emby-root paths onto movie3 /emby or /movie."""
    if path is None or path == "" or path == "/":
        return path
    base, sep, query = path.partition("?")
    lower = base.lower()

    def is_under(prefix):
        return lower == prefix or lower.startswith(prefix + "/")

    for prefix in _KEEP_PREFIXES:
        if is_under(prefix):
            return path
    if lower == "/web" or lower.startswith("/web/"):
        mapped = "/movie" + base
    else:
        mapped = "/emby" + base
    return mapped + (sep + query if sep else "")


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
    if host == HOST_EMBY:
        path = wall_path(path)
        if raw_path is not None:
            raw_path = wall_path(raw_path)
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


_INTERNAL_ADDR_MARKERS = (b"LocalAddress", b"WanAddress", b"ProgramDataPath")
_PLACEHOLDER_TOKENS = frozenset({
    "",
    "admin",
    "ADMIN",
    "fake-admin-token-for-gateway-test",
})


def _raw_body(r):
    raw = r.get("body") or b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    return raw


def has_internal_addresses(r):
    """True when a response body exposes Emby internal service fields."""
    blob = _raw_body(r) + b"\n" + (r.get("body_head") or "").encode("utf-8", "replace")
    return any(marker in blob for marker in _INTERNAL_ADDR_MARKERS)


def _token_values(headers, path):
    values = []
    headers = headers or {}
    if "X-Emby-Token" in headers:
        values.append(str(headers.get("X-Emby-Token") or "").strip().strip('"'))
    for key in ("Authorization", "X-Emby-Authorization"):
        if key not in headers:
            continue
        raw = str(headers.get(key) or "")
        for m in re.finditer(r'(?i)\bToken=("?)([^,"\s]*)\1', raw):
            values.append(m.group(2))
        if re.search(r'(?i)\bToken=', raw) and not re.search(r'(?i)\bToken=("?)([^,"\s]+)\1', raw):
            values.append("")
    query = urllib.parse.urlsplit(path or "").query
    qs = urllib.parse.parse_qs(query, keep_blank_values=True)
    for key in ("api_key", "X-Emby-Token", "ApiKey"):
        if key in qs:
            values.extend(qs[key])
    return values


def is_empty_or_placeholder_auth(headers, path):
    """True when no token is presented, or every presented token is empty/placeholder."""
    values = _token_values(headers, path)
    if not values:
        return True
    return all((v or "") in _PLACEHOLDER_TOKENS or (v or "").lower() == "admin" for v in values)


def classify(r):
    body = r["body_head"].strip()
    raw = r.get("body") or b""
    if r["status"] == 403:
        # A 403 whose body still carries internal addresses is not a clean gateway 403.
        if has_internal_addresses(r):
            return "upstream-or-other"
        if body.startswith("Forbidden") or raw[:9] == b"Forbidden":
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
        if has_internal_addresses(r):
            ok = False
            reasons.append("gateway 403 body leaked internal addresses: %s" % r["body_head"][:80])
        elif cls != "gateway-403" and not (method == "HEAD" and r["status"] == 403):
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
        if has_internal_addresses(r) and is_empty_or_placeholder_auth(headers, path):
            ok = False
            reasons.append(
                "empty or placeholder token must not receive internal addresses (status=%s)"
                % r["status"])
    elif expect == "redirect-web":
        if r["status"] not in (301, 302, 307, 308):
            ok = False
            reasons.append("status %s" % r["status"])
        loc = r["location"][0] if r["location"] else ""
        if "/web/" not in loc and "/movie/" not in loc and not loc.rstrip("/").endswith("/movie"):
            ok = False
            reasons.append("location %r" % loc)
    elif expect == "redirect-movie":
        if r["status"] not in (301, 302, 307, 308):
            ok = False
            reasons.append("status %s" % r["status"])
        loc = r["location"][0] if r["location"] else ""
        if not loc.lower().rstrip("/").endswith("/movie") and "/movie/" not in loc.lower():
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


def main():
    creds = None
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if raw.strip():
        try:
            creds = json.loads(raw)
        except Exception:
            creds = None
        raw = ""
    user = (creds or {}).get("user") or ""
    pw = (creds or {}).get("pw") or ""
    if creds:
        creds["pw"] = ""
        creds = None

    cases = []
    allow = [
        ("root redirect", "redirect-movie", "GET", "/"),
        ("login AuthenticateByName", "allowed", "POST", "/Users/AuthenticateByName"),
        ("Users/Me", "allowed", "GET", "/Users/Me"),
        ("Users/id", "allowed", "GET", "/Users/" + UID),
        ("Users/Me/Items", "allowed", "GET", "/Users/Me/Items"),
        ("Users/id/Items", "allowed", "GET", "/Users/" + UID + "/Items"),
        ("Users/Me/Views", "allowed", "GET", "/Users/Me/Views"),
        ("Users/Me/Items/Latest", "allowed", "GET", "/Users/Me/Items/Latest"),
        ("Users/Me/Items/Resume", "allowed", "GET", "/Users/Me/Items/Resume"),
        ("Items/id", "allowed", "GET", "/Items/" + IID),
        ("Items images", "allowed", "GET", "/Items/" + IID + "/Images/Primary"),
        ("PlaybackInfo", "allowed", "POST", "/Items/" + IID + "/PlaybackInfo"),
        ("video stream", "allowed", "GET", "/Videos/" + VID + "/stream"),
        ("hls master", "allowed", "GET", "/Videos/" + VID + "/master.m3u8"),
        ("Sessions/Playing", "allowed", "POST", "/Sessions/Playing"),
        ("Sessions/Playing/Progress", "allowed", "POST", "/Sessions/Playing/Progress"),
        ("Sessions/Playing/Stopped", "allowed", "POST", "/Sessions/Playing/Stopped"),
        ("Sessions/Playing/Ping", "allowed", "POST", "/Sessions/Playing/Ping"),
        ("System/Info/Public", "allowed", "GET", "/System/Info/Public"),
        ("Users/Public", "allowed", "GET", "/Users/Public"),
        ("System/Ping", "allowed", "GET", "/System/Ping"),
        ("Shows/Episodes", "allowed", "GET", "/Shows/" + IID + "/Episodes"),
        ("web index", "allowed", "GET", "/web/index.html"),
        ("web slash", "allowed", "GET", "/web/"),
        ("prefix emby Users/Me", "allowed", "GET", "/emby/Users/Me"),
        ("prefix mediabrowser Info/Public", "allowed", "GET", "/mediabrowser/System/Info/Public"),
        ("prefix emby Ping", "allowed", "GET", "/emby/System/Ping"),
        ("case users/me", "allowed", "GET", "/users/me"),
        ("encoded letter U", "allowed", "GET", "/%55sers/Me"),
        ("web versioned js", "allowed", "GET", "/web/main.js?v=4.10.2.0"),
    ]
    for name, expect, method, path in allow:
        cases.append(check(name, expect, HOST_EMBY, path, method=method))

    # Authenticate (non ByName) and Logout are no longer allowed
    cases.append(check("Users/Authenticate denied", "gateway-403", HOST_EMBY, "/Users/Authenticate", method="POST"))
    cases.append(check("Sessions/Logout denied", "gateway-403", HOST_EMBY, "/Sessions/Logout", method="POST"))

    deny403 = [
        ("list Users", "/Users"),
        ("Users/New", "/Users/New"),
        ("Users/id/Policy", "/Users/" + UID + "/Policy"),
        ("Users/Me/Policy", "/Users/Me/Policy"),
        ("System/Info no token", "/System/Info"),
        ("System/Configuration", "/System/Configuration"),
        ("Plugins", "/Plugins"),
        ("ScheduledTasks", "/ScheduledTasks"),
        ("Library/Refresh", "/Library/Refresh"),
        ("Library/VirtualFolders", "/Library/VirtualFolders"),
        ("Startup", "/Startup/Wizard/Complete"),
        ("Auth/Keys", "/Auth/Keys"),
        ("Auth/Providers", "/Auth/Providers"),
        ("Devices", "/Devices"),
        ("Environment", "/Environment"),
        ("Packages", "/Packages"),
        ("Encoding", "/Encoding"),
        ("Sessions list", "/Sessions"),
        ("Sessions/Capabilities", "/Sessions/Capabilities"),
        ("case USERS/NEW", "/USERS/NEW"),
        ("prefix emby Users", "/emby/Users"),
        ("prefix mediabrowser Plugins", "/mediabrowser/Plugins"),
        ("Plugins api_key", "/Plugins?api_key=ADMIN"),
        ("Users api_key", "/Users?api_key=ADMIN"),
        ("dot-dot to System/Info", "/System/Info/Public/../Info"),
        ("dot-dot to Plugins", "/web/../Plugins"),
        ("Items Download", "/Items/" + IID + "/Download"),
        ("Items/Delete GET", "/Items/Delete"),
    ]
    for name, path in deny403:
        cases.append(check(name, "gateway-403", HOST_EMBY, path))

    cases.append(check("System/Info with token header", "allowed", HOST_EMBY, "/System/Info", headers=ADMIN))
    cases.append(check("System/Info with api_key", "allowed", HOST_EMBY, "/System/Info?api_key=ADMIN"))
    cases.append(check("System/Info cookie only", "gateway-403", HOST_EMBY, "/System/Info", headers={"Cookie": "sid=1"}))
    cases.append(check("System/Info empty api_key", "gateway-403", HOST_EMBY, "/System/Info?api_key="))
    cases.append(check(
        "System/Info empty Authorization Token=",
        "gateway-403", HOST_EMBY, "/System/Info",
        headers={"Authorization": 'MediaBrowser Client="EmbyWeb", Token='}))
    cases.append(check(
        "System/Info empty X-Emby-Authorization Token=",
        "gateway-403", HOST_EMBY, "/System/Info",
        headers={"X-Emby-Authorization": 'MediaBrowser Client="EmbyWeb", Token='}))

    for path in ("/Users", "/Users/New", "/Plugins", "/ScheduledTasks",
                 "/Library/Refresh", "/Devices", "/Auth/Keys", "/Sessions", "/emby/Plugins"):
        cases.append(check("admin-token " + path, "gateway-403", HOST_EMBY, path, headers=ADMIN))
        cases.append(check("admin-token+api_key " + path, "gateway-403", HOST_EMBY, path + "?api_key=ADMIN", headers=ADMIN))

    for path in ("/search", "/search/",
                 "/sonarr", "/qb", "/ge2o", "/ge2o/", "/ge2o/custom.js",
                 "/web/dashboard", "/web/dashboard.html", "/web/configurationpage",
                 "/emby/discover", "/emby/web/dashboard", "/WEB/DASHBOARD"):
        cases.append(check("404 " + path, "gateway-404", HOST_EMBY, path))
        cases.append(check("404 admin " + path, "gateway-404", HOST_EMBY, path, headers=ADMIN))

    for path in ("/Users%2FMe", "/Users%2fNew", "/System/Info%2FPublic",
                 "/emby/%2e%2e/Users/Me", "/foo/%2e%2e/Plugins",
                 "/Users/Me;", "/Users/Me;foo=bar", "/System/Info/Public;",
                 "/emby//Users/Me", "/Users//Me", "//Users/Me",
                 "/Plugins%2fFoo", "/Users/New%2e", "/%2e%2e/Users/Me"):
        r = check("dangerous " + path, "gateway-403", HOST_EMBY, path, raw_path=path)
        if not r["ok"] and r["status"] in (400, 403):
            r["ok"] = True
            r["reasons"] = []
            r["note"] = "rejected with HTTP %s" % r["status"]
        cases.append(r)

    # D5: double / multiple encoding
    for path in ("/web/%252e%252e/Plugins", "/web/%252E%252E/Plugins",
                 "/web/%252e%252e%252fPlugins", "/emby/%252e%252e/Plugins",
                 "/Users/Me%252fPolicy", "/web/%25%32%65%25%32%65/Plugins",
                 "/web/%252f%252e%252e/x", "/web/%252E%252e/%252FPlugins"):
        cases.append(check("D5 " + path, "gateway-403", HOST_EMBY, path, raw_path=path))

    ws = {
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
    }
    for path in ("/Users/Me", "/socket"):
        cases.append(check("ws " + path, "ws-denied", HOST_EMBY, path, headers=ws))
    for path in ("/embywebsocket", "/movie/embywebsocket", "/emby/embywebsocket"):
        wr = request(HOST_EMBY, path, headers=ws, timeout=3)
        wok = wr["status"] in (101, 200, 400, 426) and classify(wr) != "gateway-403"
        cases.append({
            "name": "ws allowed " + path,
            "path": path, "host": HOST_EMBY, "method": "GET",
            "expect": "101 or upstream (not gateway 403)",
            "status": wr["status"], "cache_control": wr["cache_control"],
            "referrer_policy": wr["referrer_policy"], "location": wr["location"],
            "content_range": wr["content_range"],
            "classification": "websocket",
            "ok": wok,
            "reasons": [] if wok else ["status=%s cls=%s" % (wr["status"], classify(wr))],
            "body_head": "status=%s" % wr["status"],
        })

    # D1: 403 must not be public immutable
    cases.append(check("D1 double-slash versioned js", "gateway-403", HOST_EMBY,
                       "/web//apploader.js?v=4.10.2.0", raw_path="/web//apploader.js?v=4.10.2.0"))
    cases.append(check("D1 websocket versioned js", "ws-denied", HOST_EMBY,
                       "/web/apploader.js?v=4.10.2.0", headers=ws))
    # D2: image 403 no short cache
    cases.append(check("D2 websocket image", "ws-denied", HOST_EMBY,
                       "/Items/" + IID + "/Images/Primary", headers=ws))

    # D3: write methods
    writes = [
        ("D3 DELETE /Users/id", "DELETE", "/Users/" + UID),
        ("D3 POST /Users/id", "POST", "/Users/" + UID),
        ("D3 DELETE /Items/id", "DELETE", "/Items/" + IID),
        ("D3 POST /Items/id", "POST", "/Items/" + IID),
        ("D3 POST image", "POST", "/Items/" + IID + "/Images/Primary"),
        ("D3 DELETE image", "DELETE", "/Items/" + IID + "/Images/Primary"),
        ("D3 POST /Items/Delete", "POST", "/Items/Delete"),
        ("D3 POST /Videos/id/Delete", "POST", "/Videos/" + VID + "/Delete"),
        ("D3 DELETE /Videos/id", "DELETE", "/Videos/" + VID),
        ("D3 PUT /Users/id", "PUT", "/Users/" + UID),
        ("D3 PATCH /Items/id", "PATCH", "/Items/" + IID),
        ("D3 GET /Videos/id/Delete", "GET", "/Videos/" + VID + "/Delete"),
    ]
    for name, method, path in writes:
        cases.append(check(name, "gateway-403", HOST_EMBY, path, method=method))
        cases.append(check(name + " admin-token", "gateway-403", HOST_EMBY, path, method=method, headers=ADMIN))

    # D4 unauthenticated openlist
    cases.append(check("D4 openlist root", "gateway-404", HOST_EMBY, "/openlist"))
    cases.append(check("D4 openlist slash", "gateway-404", HOST_EMBY, "/openlist/"))
    cases.append(check("D4 openlist api", "gateway-404", HOST_EMBY, "/openlist/api"))
    cases.append(check("D4 openlist p with sign", "gateway-404", HOST_EMBY,
                       "/openlist/p/aliyun/Movies/x.mkv?sign=test"))
    cases.append(check("D4 openlist d no sign", "gateway-403", HOST_EMBY,
                       "/openlist/d/aliyun/Movies/x.mkv"))
    cases.append(check("D4 openlist d empty sign", "gateway-403", HOST_EMBY,
                       "/openlist/d/aliyun/Movies/x.mkv?sign="))
    cases.append(check("D4 openlist d POST", "gateway-403", HOST_EMBY,
                       "/openlist/d/aliyun/Movies/x.mkv?sign=test", method="POST"))
    # a made-up sign should reach OpenList and come back as gateway 403 (401 converted)
    cases.append(check("D4 openlist d wrong sign", "gateway-403", HOST_EMBY,
                       "/openlist/d/aliyun/Movies/MV4-Test-Long/MV4-Test-Long.mkv?sign=deadbeef:1794551446"))

    cases.append(check("cache js+cookie", "allowed", HOST_EMBY, "/web/main.js?v=4.10.2.0", headers={"Cookie": "sid=1"}))
    cases.append(check("cache html versioned", "allowed", HOST_EMBY, "/web/index.html?v=4.10.2.0"))
    cases.append(check("cache image api_key", "allowed", HOST_EMBY, "/Items/" + IID + "/Images/Primary?api_key=x"))

    # movie2 regression
    cases.append(check("movie2 discover", "allowed", HOST_MOVIE, "/discover/"))
    cases.append(check("movie2 ge2o 404", "gateway-404", HOST_MOVIE, "/ge2o"))
    cases.append(check("movie2 Plugins reaches relay", "allowed", HOST_MOVIE, "/Plugins"))
    cases.append(check("movie2 web", "allowed", HOST_MOVIE, "/web/index.html"))
    # movie2 /openlist still has cinema_admin (unauth → 302 /discover), not rewritten by emby rules
    r_m2 = request(HOST_MOVIE, "/openlist/p/aliyun/Movies/MV4-Test-Long/MV4-Test-Long.mkv?sign=test")
    ok_m2 = r_m2["status"] in (302, 307, 308) and (r_m2["location"] and "/discover" in r_m2["location"][0])
    cases.append({
        "name": "movie2 openlist still forward_auth",
        "path": "/openlist/p/…",
        "host": HOST_MOVIE,
        "method": "GET",
        "expect": "302 /discover/ (cinema_admin)",
        "status": r_m2["status"],
        "location": r_m2["location"],
        "cache_control": r_m2["cache_control"],
        "referrer_policy": r_m2["referrer_policy"],
        "classification": "movie2-auth-redirect",
        "ok": ok_m2,
        "reasons": [] if ok_m2 else ["status=%s loc=%s" % (r_m2["status"], r_m2["location"])],
        "body_head": r_m2["body_head"][:80],
        "content_range": r_m2["content_range"],
    })

    # ---- Users/Public must be an empty list (hidden login users) ----
    for pub in ("/Users/Public", "/emby/Users/Public", "/movie/Users/Public"):
        pr = request(HOST_EMBY, pub)
        body = (pr.get("body") or b"").strip()
        empty_ok = pr["status"] == 200 and body == b"[]"
        cases.append({
            "name": "Users/Public empty " + pub,
            "path": pub, "host": HOST_EMBY, "method": "GET",
            "expect": "200 []",
            "status": pr["status"], "cache_control": pr["cache_control"],
            "referrer_policy": pr["referrer_policy"], "location": pr["location"],
            "content_range": pr["content_range"],
            "classification": "users-public-empty",
            "ok": empty_ok and pr["cache_control"] == [NO_STORE],
            "reasons": [] if empty_ok else ["status=%s body_len=%s" % (pr["status"], pr.get("body_len"))],
            "body_head": "len=%s empty=%s" % (pr.get("body_len"), body == b"[]"),
        })

    # Unauthenticated complete System/Info must not return full service info.
    for si in ("/System/Info", "/emby/System/Info", "/movie/System/Info"):
        sr = request(HOST_EMBY, si)
        raw = sr.get("body") or b""
        leaked = any(k in raw for k in (b"LocalAddress", b"WanAddress", b"OperatingSystem", b"ProgramDataPath"))
        blocked = sr["status"] == 403 and classify(sr) == "gateway-403"
        cases.append({
            "name": "System/Info unauth no full info " + si,
            "path": si, "host": HOST_EMBY, "method": "GET",
            "expect": "gateway-403, no full service info",
            "status": sr["status"], "cache_control": sr["cache_control"],
            "referrer_policy": sr["referrer_policy"], "location": sr["location"],
            "content_range": sr["content_range"],
            "classification": classify(sr),
            "ok": blocked and not leaked,
            "reasons": [] if (blocked and not leaked) else [
                "status=%s leaked=%s body=%s" % (sr["status"], leaked, sr.get("body_head", "")[:80])],
            "body_head": "status=%s leaked=%s" % (sr["status"], leaked),
        })

    # ---- negative: old emby.g60.net must not still be serving ----
    def _looks_like_emby_public_payload(r):
        raw = r.get("body") or b""
        head = (r.get("body_head") or "")
        if r["status"] not in (200, 204, 206):
            return False
        if raw.strip().startswith(b"[") or raw.strip().startswith(b"{"):
            if b"ServerName" in raw or b"Version" in raw or b"Id" in raw or b"Name" in raw:
                return True
        if b"<html" in raw.lower() or "emby" in head.lower():
            return True
        return False

    legacy_probes = [
        ("GET", "/"),
        ("GET", "/System/Info/Public"),
        ("GET", "/emby/System/Info/Public"),
        ("GET", "/Users/Public"),
        ("GET", "/web/index.html"),
        ("POST", "/Users/AuthenticateByName"),
    ]
    for method, path in legacy_probes:
        try:
            lr = request(HOST_EMBY_LEGACY, path, method=method, timeout=8)
            err = None
        except Exception as exc:
            lr = {"status": 0, "body": b"", "body_head": "", "body_len": 0,
                  "cache_control": [], "referrer_policy": [], "location": [],
                  "content_range": [], "all_headers": []}
            err = type(exc).__name__
        serving = False if err else _looks_like_emby_public_payload(lr)
        # A 3xx to /movie/ would mean this Host is still the movie3 wall.
        loc = (lr.get("location") or [""])[0] if not err else ""
        if loc and ("/movie" in loc.lower() or "/web/" in loc.lower()):
            serving = True
        if err:
            classification = "legacy-error"
        elif not serving:
            classification = "legacy-not-emby-payload"
        else:
            classification = classify(lr) if lr.get("status") else "error"
        cases.append({
            "name": "legacy emby.g60.net not an Emby public payload %s %s" % (method, path),
            "path": path, "host": HOST_EMBY_LEGACY, "method": method,
            "expect": "not an Emby public payload (empty 200 is not stopped or refused)",
            "status": lr["status"], "cache_control": lr.get("cache_control") or [],
            "referrer_policy": lr.get("referrer_policy") or [],
            "location": lr.get("location") or [],
            "content_range": lr.get("content_range") or [],
            "classification": classification,
            "ok": (err is not None) or (not serving),
            "reasons": [] if ((err is not None) or (not serving)) else [
                "still an Emby public payload status=%s loc=%s body=%s" % (
                    lr["status"], loc, (lr.get("body_head") or "")[:80])],
            "body_head": ("error=%s" % err) if err else "status=%s emby_payload=%s" % (lr["status"], serving),
        })

    # ---- ordinary user path (mv4viewer) ----
    user_notes = {"user_provided": bool(user and pw)}
    if user and pw:
        auth_h = {
            "X-Emby-Authorization": 'MediaBrowser Client="MV9Fix1", Device="box", DeviceId="mv9-fix1-verify", Version="1.0.0"',
            "Content-Type": "application/json",
        }
        login_body = json.dumps({"Username": user, "Pw": pw})
        lr = request(HOST_EMBY, "/Users/AuthenticateByName", method="POST", headers=auth_h, body=login_body)
        token = uid = None
        is_admin = None
        try:
            js = json.loads(lr["body"].decode("utf-8") if lr.get("body") is not None else "{}")
        except Exception:
            # body was popped? request() still has body in return before check()
            try:
                js = json.loads(request(HOST_EMBY, "/Users/AuthenticateByName", method="POST",
                                        headers=auth_h, body=login_body)["body"].decode("utf-8"))
            except Exception:
                js = {}
        # re-fetch because we need raw body — lr still has body
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
        cases.append({
            "name": "U login AuthenticateByName",
            "path": "/Users/AuthenticateByName",
            "host": HOST_EMBY,
            "method": "POST",
            "expect": "200 non-admin token",
            "status": lr["status"],
            "cache_control": lr["cache_control"],
            "referrer_policy": lr["referrer_policy"],
            "ok": login_ok and lr["cache_control"] == [NO_STORE] and lr["referrer_policy"] == ["no-referrer"],
            "reasons": [] if login_ok else ["status=%s token=%s admin=%s name_ok=%s" % (
                lr["status"], bool(token), is_admin, name_ok)],
            "body_head": "token_len=%s uid_len=%s is_admin=%s" % (len(token or ""), len(uid or ""), is_admin),
            "classification": "login",
            "location": lr["location"],
            "content_range": lr["content_range"],
        })
        user = pw = login_body = ""  # drop secrets
        if login_ok:
            th = {
                "X-Emby-Token": token,
                "X-Emby-Authorization": 'MediaBrowser Client="MV9Fix1", Device="box", DeviceId="mv9-fix1-verify", Version="1.0.0", Token="%s"' % token,
            }
            info = request(HOST_EMBY, "/System/Info", headers=th)
            info_ok = info["status"] == 200
            # Do not record body; only status and whether a JSON object came back.
            try:
                info_obj = json.loads((info.get("body") or b"{}").decode("utf-8"))
            except Exception:
                info_obj = {}
            has_id = bool(info_obj.get("Id") or info_obj.get("ServerName"))
            cases.append({
                "name": "U System/Info with session token",
                "path": "/System/Info", "host": HOST_EMBY, "method": "GET",
                "expect": "200 full info with token",
                "status": info["status"], "cache_control": info["cache_control"],
                "referrer_policy": info["referrer_policy"], "location": info["location"],
                "content_range": info["content_range"],
                "ok": info_ok and has_id and info["cache_control"] == [NO_STORE],
                "reasons": [] if (info_ok and has_id) else ["status=%s has_id=%s" % (info["status"], has_id)],
                "body_head": "status=%s keys=%s" % (info["status"], len(info_obj) if isinstance(info_obj, dict) else 0),
                "classification": "user-system-info",
            })
            # Views / Items
            vr = request(HOST_EMBY, "/Users/%s/Views" % uid, headers=th)
            cases.append({
                "name": "U Views", "path": "/Users/{id}/Views", "host": HOST_EMBY, "method": "GET",
                "expect": "200", "status": vr["status"], "cache_control": vr["cache_control"],
                "referrer_policy": vr["referrer_policy"], "location": vr["location"],
                "content_range": vr["content_range"],
                "ok": vr["status"] == 200 and vr["cache_control"] == [NO_STORE],
                "reasons": [] if vr["status"] == 200 else ["status=%s" % vr["status"]],
                "body_head": "len=%s" % vr["body_len"], "classification": "user",
            })
            ir = request(HOST_EMBY, "/Users/%s/Items?IncludeItemTypes=Movie&Recursive=true" % uid, headers=th)
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
            except Exception as e:
                ijs = None
            items_ok = ir["status"] == 200 and n_items >= 1 and bool(item_id)
            cases.append({
                "name": "U Items movie wall", "path": "/Users/{id}/Items", "host": HOST_EMBY, "method": "GET",
                "expect": "200 with test movie", "status": ir["status"], "cache_control": ir["cache_control"],
                "referrer_policy": ir["referrer_policy"], "location": ir["location"],
                "content_range": ir["content_range"],
                "ok": items_ok,
                "reasons": [] if items_ok else ["status=%s count=%s" % (ir["status"], n_items)],
                "body_head": "count=%s id_len=%s" % (n_items, len(item_id or "")),
                "classification": "user",
            })
            # PlaybackInfo
            pb_path = "/Items/%s/PlaybackInfo?UserId=%s&IsPlayback=true" % (item_id, uid)
            pbr = request(HOST_EMBY, pb_path, method="POST",
                          headers={**th, "Content-Type": "application/json"},
                          body=json.dumps({"DeviceProfile": {}}))
            ms_id = None
            try:
                pjs = json.loads(pbr["body"].decode("utf-8"))
                srcs = pjs.get("MediaSources") or []
                if srcs:
                    ms_id = srcs[0].get("Id")
            except Exception:
                srcs = []
            pb_ok = pbr["status"] == 200 and bool(ms_id)
            cases.append({
                "name": "U PlaybackInfo POST", "path": "/Items/{id}/PlaybackInfo", "host": HOST_EMBY,
                "method": "POST", "expect": "200 MediaSource", "status": pbr["status"],
                "cache_control": pbr["cache_control"], "referrer_policy": pbr["referrer_policy"],
                "location": pbr["location"], "content_range": pbr["content_range"],
                "ok": pb_ok and pbr["cache_control"] == [NO_STORE],
                "reasons": [] if pb_ok else ["status=%s ms=%s" % (pbr["status"], bool(ms_id))],
                "body_head": "sources=%s ms_id_len=%s" % (len(srcs) if srcs is not None else 0, len(ms_id or "")),
                "classification": "user",
            })
            # Stream 307 rewritten
            stream = "/Videos/%s/stream?static=true&MediaSourceId=%s" % (
                item_id, urllib.parse.quote(ms_id or ""))
            sr = request(HOST_EMBY, stream, headers={**th, "Range": "bytes=0-1023"})
            loc = sr["location"][0] if sr["location"] else ""
            loc_ok = (sr["status"] == 307
                      and "movie2" not in loc.lower()
                      and HOST_MOVIE not in loc.lower()
                      and "/openlist/d/" in loc.lower()
                      and "/openlist/p/" not in loc.lower()
                      and "sign=" in loc
                      and sr["cache_control"] == [NO_STORE])
            cases.append({
                "name": "U stream 307 Location is emby /openlist/d",
                "path": "/Videos/{id}/stream", "host": HOST_EMBY, "method": "GET",
                "expect": "307 /openlist/d no movie2", "status": sr["status"],
                "cache_control": sr["cache_control"], "referrer_policy": sr["referrer_policy"],
                "location": [redact(loc)], "content_range": sr["content_range"],
                "ok": loc_ok,
                "reasons": [] if loc_ok else ["status=%s loc=%s cc=%s" % (sr["status"], redact(loc), sr["cache_control"])],
                "body_head": json.dumps({"has_sign": "sign=" in loc, "has_d": "/openlist/d/" in loc.lower(),
                                         "has_movie2": "movie2" in loc.lower()}, ensure_ascii=False),
                "classification": "user-stream",
            })
            # D6: GET 307 body and all headers (reuse sr)
            d6_get = d6_redirect_reasons(sr, "GET")
            cases.append({
                "name": "D6 GET 307 headers and body have no movie2 or absolute URL",
                "path": "/Videos/{id}/stream", "host": HOST_EMBY, "method": "GET",
                "expect": "307 clean body+headers CL==len", "status": sr["status"],
                "cache_control": sr["cache_control"], "referrer_policy": sr["referrer_policy"],
                "location": [redact(loc)], "content_range": sr["content_range"],
                "ok": not d6_get,
                "reasons": d6_get,
                "body_head": json.dumps({"body_len": sr["body_len"], "cl": sr.get("content_length"),
                                         "has_movie2_body": b"movie2" in (sr.get("body") or b"").lower()},
                                        ensure_ascii=False),
                "classification": "d6",
            })
            # D6: HEAD on the same stream URL. The live relay answers 200 (no
            # Location) for HEAD, while GET is 307; the unit tests cover HEAD
            # 307 via a stub that emits the same HTML as GET. If we do get a
            # 3xx, apply the full D6 checks. In all cases, headers and body
            # must not leak movie2 / /openlist/p/ / absolute URLs.
            hr = request(HOST_EMBY, stream, method="HEAD", headers={**th, "Range": "bytes=0-1023"})
            hloc = hr["location"][0] if hr["location"] else ""
            if hr["status"] in (301, 302, 307, 308):
                d6_head = d6_redirect_reasons(hr, "HEAD")
            else:
                d6_head = []
                for k, v in (hr.get("all_headers") or []):
                    if "movie2" in v.lower() or re.search(r"(?i)/openlist/p/", v) or re.search(r"(?i)https?://", v):
                        d6_head.append("header %s leaks %r" % (k, redact(v)))
                body = hr.get("body") or b""
                if b"movie2" in body.lower() or re.search(br"(?i)/openlist/p/|https?://", body):
                    d6_head.append("body leaks")
                if body:
                    d6_head.append("HEAD body not empty len=%s" % len(body))
            cases.append({
                "name": "D6 HEAD stream headers and body have no movie2 or absolute URL",
                "path": "/Videos/{id}/stream", "host": HOST_EMBY, "method": "HEAD",
                "expect": "3xx fully clean, or non-3xx with no leak",
                "status": hr["status"],
                "cache_control": hr["cache_control"], "referrer_policy": hr["referrer_policy"],
                "location": [redact(hloc)], "content_range": hr["content_range"],
                "ok": not d6_head,
                "reasons": d6_head,
                "body_head": json.dumps({"body_len": hr["body_len"], "cl": hr.get("content_length"),
                                         "status": hr["status"],
                                         "loc_d": "/openlist/d/" in hloc.lower(),
                                         "loc_movie2": "movie2" in hloc.lower()},
                                        ensure_ascii=False),
                "classification": "d6",
            })
            # D6: gateway-generated root 302, GET and HEAD (a real 3xx HEAD).
            for root_m in ("GET", "HEAD"):
                rr = request(HOST_EMBY, "/", method=root_m)
                rreasons = d6_redirect_reasons(rr, root_m)
                rloc = rr["location"][0] if rr["location"] else ""
                if not rloc.lower().rstrip("/").endswith("/movie") and "/movie/" not in rloc.lower():
                    rreasons = list(rreasons) + ["location not /movie/: %s" % rloc]
                cases.append({
                    "name": "D6 root %s 302 is clean" % root_m,
                    "path": "/", "host": HOST_EMBY, "method": root_m,
                    "expect": "302 /movie/ clean CL==len",
                    "status": rr["status"],
                    "cache_control": rr["cache_control"], "referrer_policy": rr["referrer_policy"],
                    "location": [rloc], "content_range": rr["content_range"],
                    "ok": not rreasons and rr["status"] in (301, 302, 307, 308),
                    "reasons": rreasons if rreasons else ([] if rr["status"] in (301, 302, 307, 308) else ["status %s" % rr["status"]]),
                    "body_head": json.dumps({"body_len": rr["body_len"], "cl": rr.get("content_length")},
                                            ensure_ascii=False),
                    "classification": "d6",
                })
            # Follow 307 on movie3 (HOST_EMBY)
            if loc and loc_ok:
                # Location is relative /openlist/d/…?sign= or absolute same host
                if loc.startswith("http"):
                    u = urllib.parse.urlparse(loc)
                    follow_path = u.path + (("?" + u.query) if u.query else "")
                    follow_host = u.hostname or HOST_EMBY
                else:
                    follow_path = loc
                    follow_host = HOST_EMBY
                # never log follow_path (has sign); only status/cr
                fr = request(follow_host, follow_path, headers={"Range": "bytes=0-1023"})
                follow_ok = fr["status"] == 206 and fr["content_range"] and fr["cache_control"] == [NO_STORE]
                cases.append({
                    "name": "U follow 307 Range 0-1023 → 206",
                    "path": "/openlist/d/…", "host": follow_host, "method": "GET",
                    "expect": "206", "status": fr["status"],
                    "cache_control": fr["cache_control"], "referrer_policy": fr["referrer_policy"],
                    "location": [redact(x) for x in (fr["location"] or [])],
                    "content_range": fr["content_range"],
                    "ok": follow_ok,
                    "reasons": [] if follow_ok else ["status=%s cr=%s cc=%s loc=%s" % (
                        fr["status"], fr["content_range"], fr["cache_control"],
                        [redact(x) for x in (fr["location"] or [])])],
                    "body_head": "cr=%s ctype=%s len=%s" % (fr["content_range"], fr["content_type"], fr["body_len"]),
                    "classification": "user-follow",
                })
                # seek
                fr2 = request(follow_host, follow_path, headers={"Range": "bytes=1000000-1001023"})
                seek_ok = fr2["status"] == 206 and fr2["content_range"]
                cases.append({
                    "name": "U follow 307 seek Range 1000000-1001023 → 206",
                    "path": "/openlist/d/…", "host": follow_host, "method": "GET",
                    "expect": "206", "status": fr2["status"],
                    "cache_control": fr2["cache_control"], "referrer_policy": fr2["referrer_policy"],
                    "location": [redact(x) for x in (fr2["location"] or [])],
                    "content_range": fr2["content_range"],
                    "ok": seek_ok and fr2["cache_control"] == [NO_STORE],
                    "reasons": [] if seek_ok else ["status=%s cr=%s" % (fr2["status"], fr2["content_range"])],
                    "body_head": "cr=%s" % fr2["content_range"],
                    "classification": "user-follow",
                })
                # tamper / drop sign on the same path
                u2 = urllib.parse.urlparse(follow_path if follow_path.startswith("http") else "http://x" + follow_path)
                q = urllib.parse.parse_qs(u2.query, keep_blank_values=True)
                sign = (q.get("sign") or [""])[0]
                media_path = u2.path
                # no sign
                cases.append(check("U follow path no sign", "gateway-403", HOST_EMBY, media_path))
                # empty
                cases.append(check("U follow path empty sign", "gateway-403", HOST_EMBY, media_path + "?sign="))
                # tampered
                if sign:
                    hp, _, exp = sign.partition(":")
                    chars = list(hp) if hp else ["x"]
                    chars[-1] = "A" if chars[-1] != "A" else "B"
                    tampered = "".join(chars) + ((":" + exp) if exp else "")
                    cases.append(check("U follow path tampered sign", "gateway-403", HOST_EMBY,
                                       media_path + "?sign=" + urllib.parse.quote(tampered, safe=":")))
                    other = "/openlist/d/aliyun/Movies/NOT-THE-SAME-FILE.mkv?sign=" + urllib.parse.quote(sign, safe=":")
                    cases.append(check("U reused sign other path", "gateway-403", HOST_EMBY, other))
                sign = tampered = hp = exp = follow_path = ""
            # Playing* evidence: POST should reach upstream (204 typical)
            play_body = json.dumps({"ItemId": item_id, "PlaySessionId": "abcdabcdabcdabcdabcdabcdabcdabcd", "PositionTicks": 0})
            for pth, pname in (("/Sessions/Playing", "Playing"),
                               ("/Sessions/Playing/Progress", "Progress"),
                               ("/Sessions/Playing/Ping", "Ping"),
                               ("/Sessions/Playing/Stopped", "Stopped")):
                pr = request(HOST_EMBY, pth, method="POST",
                             headers={**th, "Content-Type": "application/json"}, body=play_body)
                pok = pr["status"] in (200, 204) and classify(pr) != "gateway-403"
                cases.append({
                    "name": "U POST /Sessions/%s evidence" % pname,
                    "path": pth, "host": HOST_EMBY, "method": "POST",
                    "expect": "upstream 200/204", "status": pr["status"],
                    "cache_control": pr["cache_control"], "referrer_policy": pr["referrer_policy"],
                    "location": pr["location"], "content_range": pr["content_range"],
                    "ok": pok, "reasons": [] if pok else ["status=%s body=%s" % (pr["status"], pr["body_head"][:60])],
                    "body_head": "status=%s" % pr["status"], "classification": "user-post",
                })
            token = th = ""
    else:
        cases.append({
            "name": "U user path skipped (no credentials on stdin)",
            "path": "", "host": HOST_EMBY, "method": "",
            "expect": "stdin creds", "status": 0, "ok": False,
            "reasons": ["no ordinary-user credentials provided"],
            "cache_control": [], "referrer_policy": [], "location": [],
            "content_range": [], "body_head": "", "classification": "skipped-user",
        })

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
