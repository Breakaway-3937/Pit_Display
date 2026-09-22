"""
The receiving end of Nexus's two push webhooks.

Nexus can POST to a URL the team registers at frc.nexus/api — the whole event
snapshot whenever a match status, break, alliance, announcement or parts
request changes, and a single-match body whenever a match *with our team in
it* moves. Pushes arrive seconds before a poll would notice, which is the
difference between the pit hearing "on deck" and the pit hearing it late.

This is a plain `ThreadingHTTPServer` on a background thread. It does three
things and nothing else:

1. **Checks `Nexus-Token`.** The token from frc.nexus/api is in
   `secrets/nexus_webhook_token`; a body without a matching header is
   answered 403 and dropped. Compared with `hmac.compare_digest`, because the
   port is reachable by whatever put it on the internet.
2. **Works out which webhook it is from the body's shape** (`classify_push`):
   both are registered as URLs, and Nexus does not say which one it is
   sending, so the path is ignored — any `POST` will do.
3. **Hands the parsed object to the service through a Qt signal.** The HTTP
   thread never touches a widget; `received` is emitted from the server
   thread and Qt queues it onto the main thread.

**Always answer 200 to anything that carried the right token.** The spec is
blunt: any other status is an error, is not retried, and a webhook that keeps
failing is disabled by Nexus without telling anyone. So a body we cannot use
— empty, not JSON, a registration ping in a shape the spec does not describe
— is still 200; it is logged and reported to the panel, not refused. A body
that parses but is *older* than what we already hold is 200 too — the service
does the `dataAsOfTime` comparison, and "stale" is not "failed". The one 403
is a wrong or missing token, because that is the only case where answering
200 would mean trusting a stranger.

**Every POST is written to `nexus_webhook.log` in the data directory** —
headers (token redacted), size, outcome, and the body when it was not
usable — because a push that Nexus sent and we did not draw is otherwise
invisible from both ends.

The pit laptop is behind event wifi, so Nexus cannot reach this port unless
somebody has put a tunnel in front of it. That is why it is off by default
and why polling is the primary path; this is the accelerator.

**It listens on loopback**, because the tunnel is a process on this same
machine. See `start()`.
"""

from __future__ import annotations

import hmac
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt6.QtCore import QObject, pyqtSignal

from app import paths
from app.nexus import api

_MAX_BODY = 4 << 20        # 4 MiB — a full-event snapshot is ~100 KB
_LOG_FILE = "nexus_webhook.log"
_LOG_MAX = 1 << 20         # trim the log back to its last half past 1 MiB
_LOG_BODY = 2048           # how much of an unusable body to keep


def log_path():
    return paths.data(_LOG_FILE)


def _log(lines: list[str]) -> None:
    """Append one entry. Never raises — a full disk must not fail a push."""
    try:
        path = log_path()
        try:
            if path.stat().st_size > _LOG_MAX:
                keep = path.read_bytes()[-_LOG_MAX // 2:]
                path.write_bytes(keep)
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    except OSError:
        pass


def _read_body(req: BaseHTTPRequestHandler) -> bytes | None:
    """The request body, honouring chunked encoding; None if oversized."""
    if "chunked" in (req.headers.get("Transfer-Encoding") or "").lower():
        out = bytearray()
        while True:
            size_line = req.rfile.readline().strip().split(b";", 1)[0]
            try:
                size = int(size_line or b"0", 16)
            except ValueError:
                return bytes(out)
            if size == 0:
                req.rfile.readline()          # the trailing CRLF
                return bytes(out)
            out += req.rfile.read(size)
            req.rfile.readline()
            if len(out) > _MAX_BODY:
                return None
    try:
        length = int(req.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length > _MAX_BODY:
        return None
    return req.rfile.read(length) if length > 0 else b""


class WebhookServer(QObject):
    """
    Owns the listening socket. `start()` binds; `stop()` closes and joins.

    `received(kind, payload)` — `kind` is `"event"` or `"match"`, `payload` the
    parsed `EventStatus` / `MatchStatus`. `rejected(reason)` is for the panel's
    log line: a bad token, a body that would not parse.
    """

    received = pyqtSignal(str, object)
    rejected = pyqtSignal(str)
    state_changed = pyqtSignal(bool)        # listening?

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0
        self._count = 0

    @property
    def listening(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._port

    @property
    def count(self) -> int:
        """Bodies accepted since start — the panel's proof it is being called."""
        return self._count

    def start(self, port: int, host: str = "127.0.0.1") -> str:
        """
        Bind and serve. Returns `""` or the reason it could not.

        **Loopback by default.** The tunnel that gives Nexus a route to this
        machine runs *on* this machine and connects to 127.0.0.1, so the
        wildcard bind buys no reachability at all — and on Windows it raises a
        Defender Firewall prompt whose wrong answer blocks the port with no
        error anywhere. `webhook_bind` in `nexus.json` is the override, for a
        real port-forward on a network the team controls.
        """
        if self._server is not None:
            return ""
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "breakaway-pit-display"

            def log_message(self, *_args):          # silence stderr
                pass

            def do_GET(self):
                # Something to look at from a browser when checking a tunnel.
                body = (b"Breakaway Pit Display - Nexus webhook. "
                        b"POST the Nexus push body here.")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                status = owner._handle(self)
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        try:
            self._server = ThreadingHTTPServer((host, port), _Handler)
        except OSError as e:
            self._server = None
            return f"Could not listen on port {port}: {e.strerror or e}"
        self._server.daemon_threads = True
        self._port = port
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="nexus-webhook", daemon=True)
        self._thread.start()
        self.state_changed.emit(True)
        return ""

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.state_changed.emit(False)

    # ── One request ──────────────────────────────────────────────────────

    def _handle(self, req: BaseHTTPRequestHandler) -> int:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        headers = {k: ("<redacted>" if k.lower() == "nexus-token" else v)
                   for k, v in req.headers.items()}
        entry = [f"{stamp}  POST {req.path} from {req.client_address[0]}",
                 "  headers " + json.dumps(headers)]

        def finish(status: int, note: str, body: bytes | None = None) -> int:
            entry.append(f"  -> {status}  {note}")
            if body:
                shown = body[:_LOG_BODY].decode("utf-8", errors="replace")
                entry.append("  body    " + shown
                             + (" …" if len(body) > _LOG_BODY else ""))
            _log(entry)
            return status

        expected = api.webhook_token()
        got = req.headers.get("Nexus-Token", "")
        if not expected:
            self.rejected.emit("Push refused: no webhook token stored on this "
                               "machine, so the caller cannot be verified.")
            return finish(403, "no token stored on this machine")
        if not hmac.compare_digest(got.encode(), expected.encode()):
            self.rejected.emit("Push refused: Nexus-Token did not match.")
            return finish(403, "token " + ("missing" if not got else "did not match"))

        body = _read_body(req)
        if body is None:
            self.rejected.emit("Push ignored: body larger than 4 MB.")
            return finish(200, "ignored: body too large")
        entry.append(f"  body    {len(body)} bytes")
        if not body.strip():
            self.rejected.emit("Push received with an empty body (a test ping?) — "
                               "token accepted, nothing to apply.")
            return finish(200, "empty body — answered 200")
        try:
            raw = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self.rejected.emit(f"Push ignored: body is not JSON ({e}).")
            return finish(200, f"not JSON ({e}) — answered 200 anyway", body)

        kind = api.classify_push(raw)
        if kind is None:
            self.rejected.emit("Push ignored: body is neither an event status "
                               "nor a match status (logged).")
            return finish(200, "neither webhook shape — answered 200 anyway", body)
        try:
            payload = (api.parse_event_status(raw) if kind == "event"
                       else api.parse_match_status(raw))
        except Exception as e:                     # a shape we did not expect
            self.rejected.emit(f"Push ignored: could not parse ({e}).")
            return finish(200, f"parse failed ({e}) — answered 200 anyway", body)

        self._count += 1
        self.received.emit(kind, payload)
        label = (f"{payload.match.label}: {payload.match.status}" if kind == "match"
                 else f"{len(payload.matches)} matches, "
                      f"queuing {payload.now_queuing!r}")
        return finish(200, f"accepted {kind} push — {label}")
