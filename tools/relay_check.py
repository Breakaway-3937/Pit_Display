"""
Check the Nexus relay end to end — the Worker, its Durable Object, and this
app's own client against it. Exit 0/1.

    uv run tools/relay_check.py                          the deployed relay, read-only
    uv run tools/relay_check.py --webhook-token <T>      …and the webhook write path
    uv run tools/relay_check.py --local                  against `npm run dev` (see below)

**Read-only by default.** Without `--webhook-token` it checks what any pit
machine can: health, the portal, that the API refuses a missing or wrong
token, that the mirrored endpoints answer, and that the WebSocket connects,
answers a ping and hands over a snapshot. With the token it also posts
webhooks into a throwaway event room (`--event`, default `relaycheck`) and
checks ordering, the match-push merge, the refused counter, and that a push
reaches a subscribed socket — which is the whole point of the relay.

**`--local`** runs a fake frc.nexus on :8790 (the spec's bundled example
payloads) and points at `wrangler dev` on :8787, which must be started with a
`.dev.vars` like this:

    CLIENT_TOKEN=dev-client-token
    NEXUS_WEBHOOK_TOKEN=dev-webhook-token
    NEXUS_API_KEY=fake
    NEXUS_API=http://127.0.0.1:8790/api/v1

Every request goes through the same `RelayClient` and `RelayLink` the app
uses, so a pass here is a pass for the pit display too — not for a copy of it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.console import use_utf8  # noqa: E402

use_utf8()

from PyQt6.QtCore import QCoreApplication, QUrl  # noqa: E402
from PyQt6.QtNetwork import QNetworkRequest  # noqa: E402
from PyQt6.QtWebSockets import QWebSocket  # noqa: E402

from app.nexus import api  # noqa: E402
from app.nexus.api import NexusError  # noqa: E402
from app.nexus import settings as nexus_settings  # noqa: E402

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        _failures.append(name)
    return ok


def http(method: str, url: str, headers: dict | None = None,
         body: dict | None = None) -> tuple[int, dict, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": "breakaway-relay-check",
        **({"Content-Type": "application/json"} if data else {}),
        **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()
    except urllib.error.URLError as e:
        # No route at all — DNS, TLS, a refused connection. A failed check,
        # not a traceback: "cannot reach it" is the answer being asked for.
        return 0, {}, f"unreachable: {e.reason}"


def pump(app: QCoreApplication, until, timeout_s: float) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


# ── A fake frc.nexus for --local ─────────────────────────────────────────────

def serve_fake_upstream(port: int) -> ThreadingHTTPServer:
    fx = api.load_fixtures()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def _send(self, code: int, body) -> None:
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.headers.get("Nexus-Api-Key"):
                return self._send(401, {"error": "no key"})
            parts = self.path.split("?")[0].strip("/").split("/")
            if parts[:2] != ["api", "v1"]:
                return self._send(404, {})
            rest = parts[2:]
            if rest == ["events"]:
                return self._send(200, fx["events"])
            if len(rest) == 2 and rest[0] == "event":
                snap = dict(fx["event_status"][2])
                snap["eventKey"] = rest[1]
                # Old on purpose: a pull must never beat a webhook the check posted.
                snap["dataAsOfTime"] = 1_000
                return self._send(200, snap)
            if len(rest) == 3 and rest[0] == "event":
                table = {"pits": fx["pits"], "map": fx["map_simple"],
                         "inspection": fx["inspection"], "teams": fx["teams"],
                         "alliances": fx["alliances"]}
                if rest[2] in table:
                    return self._send(200, table[rest[2]])
            return self._send(404, {})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ── The checks ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="")
    ap.add_argument("--token", default="", help="the relay's CLIENT_TOKEN")
    ap.add_argument("--webhook-token", default=os.environ.get("RELAY_WEBHOOK_TOKEN", ""))
    ap.add_argument("--event", default="relaycheck")
    ap.add_argument("--local", action="store_true")
    args = ap.parse_args()

    if args.local:
        serve_fake_upstream(8790)
        url = args.url or "http://127.0.0.1:8787"
        token = args.token or "dev-client-token"
        hook_token = args.webhook_token or "dev-webhook-token"
    else:
        url = args.url or nexus_settings.load()["relay_url"]
        token = args.token or api.relay_token()
        hook_token = args.webhook_token
    url = url.rstrip("/")
    key = args.event.lower()
    auth = {"Authorization": f"Bearer {token}"}
    app = QCoreApplication(sys.argv)

    print(f"Relay: {url}   event room: {key}")
    if not token:
        print("No relay token — pass --token or put it in secrets/nexus_relay_token.")
        return 1

    # ── Public surface
    print("\nPublic")
    code, _, body = http("GET", f"{url}/healthz")
    if code == 0:
        check("the relay is reachable from this machine", False, body)
        print("\nNothing else can be checked. If the name does not resolve "
              "yet, a DNS cache\nis still holding an old answer — see "
              "nexus-relay/README.md, 'Initial DNS lookups'.")
        return 1
    health = json.loads(body) if code == 200 else {}
    check("GET /healthz answers ok", code == 200 and health.get("ok") is True, f"HTTP {code} {body[:120]}")
    check("the relay holds a Nexus API key", bool(health.get("apiKey")),
          "run `npx wrangler secret put NEXUS_API_KEY`")
    check("the relay holds a webhook token", bool(health.get("webhookToken")),
          "run `npx wrangler secret put NEXUS_WEBHOOK_TOKEN`")
    code, _, body = http("GET", f"{url}/")
    check("the portal page names the webhook URL", code == 200 and "/nexus/webhook" in body)

    # ── Auth
    print("\nAuth")
    code, _, _ = http("GET", f"{url}/api/v1/event/{key}")
    check("no token → 401", code == 401, f"HTTP {code}")
    code, _, _ = http("GET", f"{url}/api/v1/event/{key}", {"Authorization": "Bearer wrong"})
    check("wrong token → 401", code == 401, f"HTTP {code}")
    code, _, body = http("GET", f"{url}/api/v1/event/{key}/relay", auth)
    check("right token → the room's counters", code == 200 and '"eventKey"' in body,
          f"HTTP {code} {body[:160]}")

    # ── The mirrored API, through the app's own client
    print("\nMirrored API (app.nexus.api.RelayClient)")
    client = api.RelayClient(url, token)
    try:
        events = client.events()
        check("GET /events", True)
        print(f"      {len(events)} active events on Nexus")
    except NexusError as e:
        check("GET /events", False, str(e))
    for name, fn in (("teams", client.teams), ("pits", client.pit_addresses)):
        try:
            fn(key)
            check(f"GET /event/{{key}}/{name}", True)
        except NexusError as e:
            # A 404 is Nexus's own answer for an event with none — passed through.
            check(f"GET /event/{{key}}/{name} (404 passed through)", e.code == 404, str(e))
    # A room nobody has ever pushed to: the relay must go and ask Nexus itself.
    fresh = f"{key}-pull-{int(time.time())}"
    code, _, body = http("GET", f"{url}/api/v1/event/{fresh}", auth)
    stats = client.relay_stats(fresh)
    check("an event with no webhooks is pulled from Nexus by the relay",
          stats.get("pulls", 0) >= 1 and code in (200, 404),
          f"HTTP {code}, pulls={stats.get('pulls')} {stats.get('lastPullResult')}")
    if args.local:
        check("…and served", code == 200 and '"matches"' in body, f"HTTP {code}")
    code, headers, _ = http("GET", f"{url}/api/v1/event/{key}/teams", auth)
    cache = {k.lower(): v for k, v in headers.items()}.get("x-relay-cache", "")
    check("a repeat is served from the edge cache", cache == "hit",
          f"x-relay-cache: {cache or 'absent'} (HTTP {code})")

    # ── Webhook write path
    now = int(time.time() * 1000)
    if hook_token:
        print("\nWebhooks")
        hook = f"{url}/nexus/webhook"
        before = client.relay_stats(key)
        snap = {"eventKey": key, "dataAsOfTime": now, "nowQueuing": "Qualification 1",
                "matches": [{"label": "Qualification 1", "status": "Now queuing",
                             "redTeams": ["3937", "1", "2"], "blueTeams": ["3", "4", "5"],
                             "times": {}, "breakAfter": None, "replayOf": None},
                            {"label": "Qualification 2", "status": "Queuing soon",
                             "redTeams": ["6", "7", "8"], "blueTeams": ["9", "10", "11"],
                             "times": {}, "breakAfter": None, "replayOf": None}],
                "announcements": [], "partsRequests": []}
        code, _, body = http("POST", hook, {"Nexus-Token": "wrong"}, snap)
        check("wrong Nexus-Token → still 200 (Nexus disables hooks that fail)",
              code == 200 and '"ok":false' in body.replace(" ", ""), f"HTTP {code} {body}")
        code, _, body = http("POST", hook, {"Nexus-Token": hook_token}, snap)
        check("right token → accepted", code == 200 and '"ok":true' in body.replace(" ", ""),
              f"HTTP {code} {body}")
        after = client.relay_stats(key)
        check("…refused counted", after.get("refused", 0) == before.get("refused", 0) + 1)
        check("…accepted counted", after.get("accepted", 0) == before.get("accepted", 0) + 1)
        held = client.event_status(key)
        check("GET returns the pushed snapshot", held.data_as_of == now and held.now_queuing == "Qualification 1",
              f"data_as_of={held.data_as_of}")

        http("POST", hook, {"Nexus-Token": hook_token}, {**snap, "dataAsOfTime": now - 5_000,
                                                         "nowQueuing": "STALE"})
        held = client.event_status(key)
        check("an older snapshot is refused", held.now_queuing == "Qualification 1")
        check("…and counted stale", client.relay_stats(key).get("stale", 0) == after.get("stale", 0) + 1)

        push = {"eventKey": key, "dataAsOfTime": now + 1_000,
                "match": {**snap["matches"][1], "status": "On deck"}}
        http("POST", hook, {"Nexus-Token": hook_token}, push)
        held = client.event_status(key)
        m2 = held.match("Qualification 2")
        check("a match push is merged, not stored whole",
              len(held.matches) == 2 and m2 is not None and m2.status == "On deck"
              and held.data_as_of == now + 1_000,
              f"{len(held.matches)} matches, Q2={m2.status if m2 else None}")

    # ── WebSocket, raw
    print("\nWebSocket")
    ws_url = url.replace("https://", "wss://").replace("http://", "ws://") + f"/api/v1/event/{key}/ws"
    got: list[str] = []
    ws = QWebSocket()
    ws.textMessageReceived.connect(got.append)
    req = QNetworkRequest(QUrl(ws_url))
    req.setRawHeader(b"Authorization", f"Bearer {token}".encode())
    ws.open(req)
    first = pump(app, lambda: bool(got), 15)
    check("connects and is handed the held state at once", first, ws.errorString())
    if first:
        msg = json.loads(got[0])
        check("…as a status or relay envelope", msg.get("type") in ("status", "relay"))
        n = len(got)
        ws.sendTextMessage("ping")
        check("ping → pong (runtime auto-response)",
              pump(app, lambda: "pong" in got[n:], 10))
        if hook_token:
            n = len(got)
            http("POST", f"{url}/nexus/webhook", {"Nexus-Token": hook_token},
                 {**snap, "dataAsOfTime": now + 2_000, "nowQueuing": "Qualification 2"})

            def pushed():
                for raw in got[n:]:
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    if m.get("type") == "status" and m["data"].get("dataAsOfTime") == now + 2_000:
                        return True
                return False
            check("a webhook reaches the subscribed socket", pump(app, pushed, 10))
    ws.close()
    pump(app, lambda: False, 0.5)

    bad = QWebSocket()
    bad_req = QNetworkRequest(QUrl(ws_url))
    bad_req.setRawHeader(b"Authorization", b"Bearer wrong")
    opened: list[bool] = []
    bad.connected.connect(lambda: opened.append(True))
    bad.open(bad_req)
    pump(app, lambda: bool(opened) or bool(bad.errorString()), 10)
    check("a wrong token cannot open the socket", not opened)
    bad.abort()

    # ── The app's own link
    print("\nRelayLink (app.nexus.relay)")
    from app.nexus.relay import RelayLink
    link = RelayLink()
    statuses: list = []
    link.status_received.connect(statuses.append)
    link.start(url, key, token)
    check("connects", pump(app, lambda: link.connected, 15), link.telemetry()["last_error"])
    check("receives a parsed EventStatus", pump(app, lambda: bool(statuses), 10))
    if hook_token and statuses:
        n = len(statuses)
        http("POST", f"{url}/nexus/webhook", {"Nexus-Token": hook_token},
             {**snap, "dataAsOfTime": now + 3_000, "nowQueuing": "Qualification 3"})
        check("…and the next push, parsed",
              pump(app, lambda: any(s.now_queuing == "Qualification 3" for s in statuses[n:]), 10))
    check("carries the relay's counters", pump(app, lambda: bool(link.stats), 5))
    link.stop()
    check("stop() means stopped", pump(app, lambda: link.state == "off", 3)
          and not pump(app, lambda: link.state != "off", 2))

    print()
    if _failures:
        print(f"{len(_failures)} failed: " + "; ".join(_failures))
        return 1
    print("All relay checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
