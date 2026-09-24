"""
The pit-LAN HTTP server: hands a browser the page, and then gets out of the way.

A Raspberry Pi on the same Ethernet switch opens `http://<pit machine>:3938/`,
picks a screen, and shows it full-screen. From that point everything live
arrives on the websocket (`sockets.py`); this server only ever serves files —
the page, its stylesheet, its script, the bundled fonts and the judges
artwork. **It does no work per frame, because there are no frames.**

That is the difference from the first build, which pushed JPEG over this same
socket several times a second and cost a quarter of a core to say that nothing
had changed. A screen's whole lifetime on this server is now a handful of GETs
at startup.

**It binds the wire, and that is the opposite of the Nexus webhook.** That one
listens on loopback because the thing calling it (a tunnel) runs on the same
machine. This one is called by a *different* machine across the pit, so it has
to be reachable. Two consequences that are not optional:

- **Windows will raise a Defender Firewall prompt** the first time this binds.
  Answering it wrong leaves the port silently unreachable and the panels dark.
  The prompt is per *application*, so it covers the websocket port too.
- **There is no authentication, and that is a deliberate choice for a
  deliberate network.** Everything served is already on two screens facing a
  public pit; nothing is a credential, every route is a GET, and the websocket
  ignores anything a client sends. The protection is the network: a pit switch
  with no uplink. **Do not put these ports on event wifi or a hotspot.**
"""

from __future__ import annotations

import hashlib
import html
import mimetypes
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import paths
from app.config import SCREEN_LABELS
from app.webcast import settings

# Where the page lives in the resource tree. Served from disk rather than
# embedded in Python so it can be edited like the web asset it is — the CAD
# viewer under `assets/cad_viewer/` works exactly the same way.
WEB_ROOT = ("assets", "webcast")

_TEXT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".woff2": "font/woff2",
}


# The page's own assets, and the cache-busting stamp built from them.
_VERSIONED = ("screen.css", "screen.js")
_version_cache: dict[tuple, str] = {}


def asset_version() -> str:
    """
    A short hash of the page's CSS and JS, for the URL they are fetched by.

    **Without this a display never sees a change.** The page itself is
    `no-store`, but its stylesheet and script were served `max-age=86400`, so
    a browser that had fetched them once would not ask again for a day — it
    would keep loading a fresh page against a stale design and stale
    behaviour. On a kiosk nobody ever reloads by hand, "a day" is however long
    until someone notices, and the symptom is the worst kind: the operator
    changes something, the panel does not change, and nothing anywhere reports
    an error.

    Hashing the content rather than stamping a build number means a file
    edited on the pit machine takes effect on the next page load, which is how
    anyone working on this will actually test it. The result is memoised
    against each file's mtime and size, so a page load is not a re-hash.
    """
    try:
        stat_key = []
        for name in _VERSIONED:
            st = paths.resource(*WEB_ROOT, name).stat()
            stat_key.append((name, st.st_mtime_ns, st.st_size))
        key = tuple(stat_key)
    except OSError:
        return "0"
    cached = _version_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    for name in _VERSIONED:
        try:
            digest.update(paths.resource(*WEB_ROOT, name).read_bytes())
        except OSError:
            pass
    version = digest.hexdigest()[:10]
    _version_cache.clear()          # only the current build is worth keeping
    _version_cache[key] = version
    return version


def lan_address() -> str:
    """
    This machine's address on the pit LAN, for the panel to print.

    A UDP socket is "connected" to a host that is never contacted — no packet
    is sent — purely so the OS resolves which interface would carry it and
    names the local end. That is the only reliable way to pick the right
    address on a pit machine with two interfaces up (wifi for Nexus, Ethernet
    for the screens); `gethostbyname(gethostname())` returns whichever one the
    resolver likes, and on Windows that is frequently `127.0.0.1`.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostname()
        except OSError:
            return "this-machine"
    finally:
        probe.close()


def _index_page(port: int) -> str:
    prefs = settings.load()
    rows = []
    for screen_id in settings.PUBLISHABLE:
        label = html.escape(SCREEN_LABELS.get(screen_id, screen_id))
        if screen_id in prefs["screens"]:
            rows.append(f'<a class="screen" href="/screen/{screen_id}">{label}'
                        f'<small>/screen/{screen_id}</small></a>')
        else:
            rows.append(f'<a class="screen off">{label}<small>not published '
                        f'&mdash; turn it on from the control panel</small></a>')
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Breakaway Pit Display</title>
<link rel="stylesheet" href="/static/screen.css?v={asset_version()}">
<body class="index">
<h1>BREAKAWAY <b>3937</b></h1>
<p>Pick the screen this display should show. Put the browser in full screen
   once and it will stay on that screen, reconnecting by itself if the pit
   machine restarts.</p>
{''.join(rows)}
<footer>Pit display &middot; port {port}</footer>
"""


class WebcastServer:
    """Owns the listening socket. `start()` binds; `stop()` closes and joins."""

    def __init__(self, socket_port=None) -> None:
        # A callable, not a number: the websocket port is derived from the
        # page port, and the page has to be told it at request time — after
        # whatever change an operator just made in the panel.
        self._socket_port = socket_port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0
        self._bind = ""

    @property
    def listening(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._port

    @property
    def bind(self) -> str:
        return self._bind

    def start(self, port: int, bind: str) -> str:
        if self._server is not None:
            return ""
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "breakaway-pit-display"

            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner._route(self)

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        try:
            self._server = ThreadingHTTPServer((bind, port), _Handler)
        except OSError as e:
            self._server = None
            return f"Could not listen on {bind}:{port} — {e.strerror or e}"
        self._server.daemon_threads = True
        self._port, self._bind = port, bind
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="webcast-http", daemon=True)
        self._thread.start()
        return ""

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    # ── Routing ──────────────────────────────────────────────────────────

    def _route(self, req: BaseHTTPRequestHandler) -> None:
        path = req.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            return self._send(req, 200, "text/html; charset=utf-8",
                              _index_page(self._port).encode("utf-8"))
        if path == "/health":
            return self._send(req, 200, "text/plain; charset=utf-8", b"ok")
        if path.startswith("/screen/"):
            return self._screen_page(req, path[len("/screen/"):])
        if path.startswith("/static/"):
            return self._static(req, path[len("/static/"):])
        if path.startswith("/fonts/"):
            return self._file(req, paths.resource("assets", "fonts",
                                                  Path(path).name))
        if path.startswith("/judges/"):
            return self._file(req, paths.find("assets", "judges_slides",
                                              Path(path).name))
        self._send(req, 404, "text/plain; charset=utf-8", b"No such page.")

    def _screen_page(self, req, screen_id: str) -> None:
        if not settings.is_published(screen_id):
            return self._send(
                req, 404, "text/html; charset=utf-8",
                (b"<!doctype html><link rel=stylesheet href=/static/screen.css>"
                 b"<body class=index><h1>Not published</h1><p>Turn this screen "
                 b"on from the pit machine: Control, the screen, then "
                 b"&ldquo;Show on the pit network&rdquo;.</p>"))
        page = paths.resource(*WEB_ROOT, "screen.html")
        try:
            body = page.read_text(encoding="utf-8")
        except OSError as e:
            return self._send(req, 500, "text/plain; charset=utf-8",
                              f"screen.html is missing: {e}".encode())
        # The page is told what it is and where to dial. Substituted here
        # rather than worked out in JavaScript: the socket is on a different
        # port from the page, and a page that derives its own endpoint from
        # `location` is a page that breaks the day somebody moves one of them.
        ws_port = self._socket_port() if self._socket_port else self._port + 1
        body = (body.replace("{{SCREEN_ID}}", html.escape(screen_id))
                    .replace("{{SCREEN_LABEL}}",
                             html.escape(SCREEN_LABELS.get(screen_id, screen_id)))
                    .replace("{{WS_PORT}}", str(ws_port))
                    .replace("{{VERSION}}", asset_version()))
        self._send(req, 200, "text/html; charset=utf-8", body.encode("utf-8"))

    def _static(self, req, name: str) -> None:
        # Never join an unsanitised path onto a directory: a request for
        # `../../secrets/nexus_api_key` must not be served by a machine that
        # is, by design, listening on an open port.
        self._file(req, paths.resource(*WEB_ROOT, Path(name).name))

    def _file(self, req, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._send(req, 404, "text/plain; charset=utf-8",
                              b"Not found.")
        kind = (_TEXT_TYPES.get(path.suffix.lower())
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream")
        self._send(req, 200, kind, data, cache=path.suffix.lower() != ".html")

    @staticmethod
    def _send(req, status: int, content_type: str, body: bytes,
              cache: bool = False) -> None:
        try:
            req.send_response(status)
            req.send_header("Content-Type", content_type)
            req.send_header("Content-Length", str(len(body)))
            # Fonts and artwork never change within a run and a Pi on a slow
            # card should not re-fetch them. The CSS and JS may be cached just
            # as hard, but **only because their URL carries a hash of their
            # contents** — change either file and the URL changes with it, so
            # a stale copy can never be served. The page itself is never
            # cached, or a kiosk nobody reloads by hand would keep asking for
            # the previous version's assets.
            req.send_header("Cache-Control",
                            "public, max-age=86400" if cache else "no-store")
            req.end_headers()
            req.wfile.write(body)
        except OSError:
            pass
