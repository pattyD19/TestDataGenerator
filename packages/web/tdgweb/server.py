"""The control plane's HTTP surface.

stdlib only. The routes fall into two halves that matter for different reasons:

  * the **browser API** — create a job, watch it build, list what exists
  * the **pack API** — the manifest and the media bytes, which is what a
    loader (CLI today, the Android and iOS apps later) actually consumes

The pack half is the one that has to behave under load: it serves multi-gigabyte
files to a phone on the LAN, so it supports Range requests and streams rather
than reading a file into memory.
"""
import json
import mimetypes
import os
import posixpath
import re
import socket
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import presets
from .runner import GENERATOR, sizing            # noqa: F401
from .store import Store
from .runner import Runner

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def lan_address(port):
    """The address a phone on the same network should use.

    Connecting a UDP socket to an off-net address makes the OS pick the
    outbound interface without sending anything, which is the least-bad way to
    find the LAN IP without shelling out to ifconfig.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 53))          # TEST-NET-1, never routed
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return f"http://{ip}:{port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "tdg-control-plane"
    protocol_version = "HTTP/1.1"

    # -- plumbing -----------------------------------------------------------

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, indent=2), "application/json")

    def _fail(self, code, message):
        self._json({"error": message}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return None

    # -- routing ------------------------------------------------------------

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        parts = [p for p in path.split("/") if p]
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if parts[:1] == ["static"] and len(parts) == 2:
                return self._static(parts[1])
            if path == "/api/options":
                return self._json({
                    "presets": presets.PRESETS,
                    "profiles": presets.profiles(),
                    "lan_url": self.server.lan_url,
                })
            if path == "/api/jobs":
                return self._json([self._public(j) for j in self.server.store.list()])
            if parts[:2] == ["api", "pair"] and len(parts) == 3:
                return self._pair(parts[2])
            if parts[:2] == ["api", "jobs"] and len(parts) == 3:
                return self._one(parts[2])
            if parts[:2] == ["api", "jobs"] and len(parts) == 4:
                jid, tail = parts[2], parts[3]
                if tail == "events":
                    return self._events(jid)
                if tail == "manifest":
                    return self._manifest(jid, query)
                if tail == "log":
                    return self._json({"log": []})
            if parts[:2] == ["api", "jobs"] and len(parts) >= 5 and parts[3] == "files":
                return self._file(parts[2], "/".join(parts[4:]), query)
            return self._fail(404, "no such route")
        except BrokenPipeError:
            pass                              # the browser navigated away

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        if parts == ["api", "jobs"]:
            return self._create()
        if parts[:2] == ["api", "jobs"] and len(parts) == 4:
            jid, action = parts[2], parts[3]
            if action == "cancel":
                return self._cancel(jid)
            if action == "resume":
                return self._resume(jid)
        return self._fail(404, "no such route")

    # -- handlers -----------------------------------------------------------

    def _static(self, name):
        if not SAFE_NAME.match(name):
            return self._fail(400, "bad asset name")
        path = os.path.join(STATIC, name)
        if not os.path.isfile(path):
            return self._fail(404, "not found")
        ctype = mimetypes.guess_type(path)[0] or "text/plain"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _public(self, job):
        pct = 0.0
        if job["target_bytes"]:
            pct = min(100.0, 100.0 * job["done_bytes"] / job["target_bytes"])
        return {
            "id": job["id"], "job_id": job["job_id"], "token": job["token"],
            "status": job["status"], "params": job["params"],
            "target_bytes": job["target_bytes"], "done_bytes": job["done_bytes"],
            "file_count": job["file_count"], "percent": round(pct, 2),
            "message": job["message"], "created_at": job["created_at"],
            "finished_at": job["finished_at"],
            "manifest_url": f"/api/jobs/{job['id']}/manifest?token={job['token']}",
        }

    def _one(self, jid):
        job = self.server.store.get(jid)
        if job is None:
            return self._fail(404, "no such job")
        return self._json(self._public(job))

    def _pair(self, code):
        """Turn a six-digit code into everything a loader needs.

        A phone has a keypad and no way to know a twelve-hex-digit job id, so
        the code has to be sufficient on its own. It resolves to the manifest
        URL and the totals a device checks against its own free space before
        accepting the job.
        """
        job = self.server.store.by_token(code)
        if job is None:
            return self._fail(404, "no finished pack has that code")
        pub = self._public(job)
        return self._json({
            "id": job["id"],
            "job_id": job["job_id"],
            "label": job["params"].get("label") or "",
            "profile": job["params"].get("profile"),
            "file_count": job["file_count"],
            "total_bytes": job["done_bytes"],
            "manifest_url": pub["manifest_url"],
            "token": job["token"],
        })

    def _create(self):
        body = self._body()
        if body is None:
            return self._fail(400, "body must be JSON")
        try:
            params, target = presets.normalise(body)
        except ValueError as exc:
            return self._fail(400, str(exc))

        job_id = presets.job_slug()
        pack_dir = os.path.join(self.server.packs_dir, job_id)
        job = self.server.store.create(job_id, params, target, pack_dir)
        self.server.runner.start(job["id"])
        return self._json(self._public(self.server.store.get(job["id"])), 201)

    def _cancel(self, jid):
        if self.server.store.get(jid) is None:
            return self._fail(404, "no such job")
        ok = self.server.runner.cancel(jid)
        return self._json({"cancelled": ok})

    def _resume(self, jid):
        job = self.server.store.get(jid)
        if job is None:
            return self._fail(404, "no such job")
        if job["status"] == "done":
            return self._fail(409, "already finished")
        if self.server.runner.is_running(jid):
            return self._fail(409, "already running")
        self.server.runner.start(jid)
        return self._json(self._public(self.server.store.get(jid)))

    # -- the pack half ------------------------------------------------------

    def _authorised(self, job, query):
        """Pack bytes need the job's pairing token.

        This is a lab tool on a trusted LAN, not a public service, so the token
        is a speed bump rather than a security boundary — enough that a browser
        on the same network cannot stumble into someone else's 64 GB pack.
        """
        supplied = (query.get("token") or [None])[0] \
            or self.headers.get("X-TDG-Token")
        return supplied == job["token"]

    def _manifest(self, jid, query):
        job = self.server.store.get(jid)
        if job is None:
            return self._fail(404, "no such job")
        if not self._authorised(job, query):
            return self._fail(403, "bad or missing token")
        path = os.path.join(job["pack_dir"], "manifest.json")
        if not os.path.exists(path):
            return self._fail(409, f"pack is not built yet (status {job['status']})")
        with open(path) as fh:
            doc = json.load(fh)
        # Loaders should not have to know how the server lays out URLs.
        base = f"/api/jobs/{jid}/files"
        for item in doc["items"]:
            item["url"] = f"{base}/{item['name']}?token={job['token']}"
        doc["pack_base_url"] = base
        return self._json(doc)

    def _file(self, jid, name, query):
        job = self.server.store.get(jid)
        if job is None:
            return self._fail(404, "no such job")
        if not self._authorised(job, query):
            return self._fail(403, "bad or missing token")
        name = posixpath.normpath(name)
        if not SAFE_NAME.match(name):
            return self._fail(400, "bad file name")
        path = os.path.join(job["pack_dir"], name)
        if not os.path.isfile(path):
            return self._fail(404, "not in this pack")

        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = HTTPStatus.OK
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return self._send(416, b"", "text/plain",
                                  {"Content-Range": f"bytes */{size}"})
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:                              # suffix form: bytes=-500
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self._send(416, b"", "text/plain",
                                  {"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        # Streamed in chunks: a 4 GB clip must not become 4 GB of RSS.
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1 << 16, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # -- progress stream ----------------------------------------------------

    def _events(self, jid):
        job = self.server.store.get(jid)
        if job is None:
            return self._fail(404, "no such job")
        q = self.server.runner.subscribe(jid)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self._sse(self._public(job))
            while True:
                try:
                    event = q.get(timeout=15)
                except Exception:
                    self.wfile.write(b": keepalive\n\n")   # keeps proxies honest
                    self.wfile.flush()
                    continue
                self._sse(event)
                if event.get("type") in ("done", "failed", "cancelled"):
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.runner.unsubscribe(jid, q)

    def _sse(self, obj):
        self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
        self.wfile.flush()


class ControlPlane(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        # Accept IPv4 on the same socket. Without clearing V6ONLY a dual-stack
        # bind would reach ::1 but not 127.0.0.1, trading one half of the
        # problem for the other.
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        super().server_bind()

    def __init__(self, addr, packs_dir, seeds_dir, db_path, verbose=False):
        # Bind dual-stack when asked for "any". `localhost` resolves to ::1
        # first on macOS and most Linux, so an IPv4-only bind makes the very
        # URL printed at startup fail for anything that does not fall back.
        host = addr[0]
        if host in ("0.0.0.0", "::", ""):
            self.address_family = socket.AF_INET6
            addr = ("::", addr[1])
        super().__init__(addr, Handler)
        self.packs_dir = os.path.abspath(packs_dir)
        os.makedirs(self.packs_dir, exist_ok=True)
        self.store = Store(db_path)
        self.store.reap_running()
        self.runner = Runner(self.store, os.path.abspath(seeds_dir))
        self.verbose = verbose
        self.lan_url = lan_address(addr[1])


def serve(host="0.0.0.0", port=8722, packs_dir="./packs", seeds_dir=None,
          db_path=None, verbose=False, block=True):
    seeds_dir = seeds_dir or os.path.join(
        os.path.abspath(os.path.join(HERE, "..", "..", "..")), "seed-pool")
    db_path = db_path or os.path.join(packs_dir, "jobs.sqlite3")
    httpd = ControlPlane((host, port), packs_dir, seeds_dir, db_path, verbose)
    if not block:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    print(f"tdg control plane on http://localhost:{httpd.server_address[1]}")
    print(f"  on the LAN:  {httpd.lan_url}")
    print(f"  packs:       {httpd.packs_dir}")
    print(f"  seed pool:   {seeds_dir}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    return httpd
