#!/usr/bin/env python3
"""
Automated checks for the pit-LAN webcast (`app/webcast/`).

    QT_QPA_PLATFORM=offscreen PIT_LEDS_FAKE=1 uv run tools/webcast_check.py

Boots the real app offscreen, publishes both overhead screens, and asks the
running servers the questions an operator would: is the page served, does the
socket deliver state, is that state *right*, does it move when the rotation
moves, and does any of it survive the things an operator does mid-event.

**It starts cold and clicks the real control-panel toggle**, because driving
the service directly from an already-enabled state is what hid a publish
ordering bug once. The scratch tree is wiped every run for the same reason: a
left-over `webcast.json` builds the panel's switch already checked, so
`setChecked(True)` emits nothing and the cold-start checks silently test
nothing at all.

Judges mode is not checked; the deck is served as image files and there is
nothing here that could be wrong about it that a 404 would not already say.

Exit status is 0 when every check passed, 1 otherwise, so it can gate a build.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import pathlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PIT_LEDS_FAKE", "1")
os.environ.setdefault("PIT_NEXUS_QUIET", "1")

from app.console import use_utf8  # noqa: E402

PORT = 3993
RESULTS: list[tuple[str, bool, str]] = []
_APP = None

# Every warning Qt emits during the run, captured rather than left to scroll
# past in a terminal. The ones that matter here are the lifetime complaints —
#
#   QObject::disconnect: wildcard call disconnects from destroyed signal of
#   QWebSocketDataProcessor::unnamed
#
# — which mean a QWebSocket was destroyed while its own internals were still
# tearing down. They are harmless-looking, intermittent, and a real symptom:
# `nextPendingConnection()` hands back an *unparented* socket, so anything we
# do not hold a reference to dies whenever a collection happens to run.
QT_WARNINGS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    return bool(ok)


def get(path: str, timeout: float = 5.0):
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError:
        return None, b""


def main() -> int:
    use_utf8()
    scratch = Path(os.environ.get("PIT_WEBCAST_CHECK_DATA")
                   or (Path(__file__).resolve().parent.parent / ".webcast_check"))
    shutil.rmtree(scratch, ignore_errors=True)
    os.environ["PIT_DISPLAY_DATA"] = str(scratch)

    from PyQt6 import QtWebEngineWidgets  # noqa: F401  (before QApplication)
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    from PyQt6.QtWidgets import QApplication

    def _capture(mode, _context, message):
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg,
                    QtMsgType.QtFatalMsg):
            QT_WARNINGS.append(message)

    qInstallMessageHandler(_capture)
    from PyQt6.QtCore import QEventLoop, QTimer, QUrl
    from PyQt6.QtWebSockets import QWebSocket

    import app.db.migrations  # noqa: F401
    from app.config import config, init_config
    from app.db import init_db
    from app.rotation import init_rotation, rotation
    from app.judges_slides import init_judges_slides
    from app.cad_assets import init_cad_assets
    from app.checklist import init_checklist
    from app.leds import init_leds
    from app.music import init_music
    from app.admin import init_admin
    from app.update import init_update
    from app.nexus import init_nexus
    from app.nexus.alerts import init_alerts
    from app.webcast import init_webcast
    from app.webcast import settings as wset

    global _APP
    _APP = QApplication(["pit-display"])
    os.environ.setdefault("PIT_SYNC_QUIET", "1")
    os.environ.setdefault("PIT_AI_QUIET", "1")
    from app.ai.service import init_analysis
    from app.batteries import init_batteries
    from app.db.sync.service import init_sync
    # Every service the control screen subscribes to, in main()'s order.
    for fn in (init_config, init_update, init_db, init_rotation,
               init_judges_slides, init_cad_assets, init_leds, init_batteries,
               init_music, init_admin, init_checklist, init_nexus, init_alerts,
               init_sync, init_analysis):
        fn()

    from app.windows.control_screen import ControlScreen
    from app.windows.presentation_a import PresentationScreenA
    from app.windows.presentation_b import PresentationScreenB

    wset.save(enabled=False, screens=list(wset.PUBLISHABLE),
              port=PORT, bind="127.0.0.1")

    # Before the control screen, exactly as main() does it: the Pit Network
    # panel subscribes to this service while it is being built.
    webcast = init_webcast()

    control = ControlScreen()
    control.set_window_factories({"presentation_a": PresentationScreenA,
                                  "presentation_b": PresentationScreenB})
    webcast.set_window_provider(control.managed_window)
    control.set_webcast(webcast)

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    # ── A tiny websocket client, on the same event loop ──────────────────
    class Listener:
        def __init__(self, screen_id: str):
            self.messages: list[dict] = []
            self.connected = False
            self.socket = QWebSocket()
            self.socket.connected.connect(self._open)
            self.socket.textMessageReceived.connect(self._text)
            self.socket.open(QUrl(
                f"ws://127.0.0.1:{webcast.socket_port}/{screen_id}"))

        def _open(self):
            self.connected = True

        def _text(self, text: str):
            try:
                self.messages.append(json.loads(text))
            except ValueError:
                pass

        def wait(self, count=1, ms=4000) -> bool:
            waited = 0
            while len(self.messages) < count and waited < ms:
                pump(50)
                waited += 50
            return len(self.messages) >= count

        def latest(self, kind="screen"):
            for m in reversed(self.messages):
                if m.get("type") == kind:
                    return m.get("data")
            return None

        def close(self):
            self.socket.close()

    print("\n── Cold start, through the real control-panel toggle ──────", flush=True)
    webcast.start()
    check("off by default: nothing listening, no windows",
          not webcast.listening
          and control.managed_window("presentation_a") is None)

    panel = control._settings_panels["presentation_a"]
    toggle = getattr(panel, "_web_toggle", None)
    if not check("the settings panel has the publish toggle", toggle is not None):
        return 1
    check("toggle starts unchecked on a fresh install", not toggle.isChecked())
    toggle.setChecked(True)
    pump(900)
    check("toggling on starts the page server", webcast.listening,
          f"port {webcast.port}")
    check("toggling on starts the socket server", webcast.socket_port > 0,
          f"port {webcast.socket_port}")
    check("the socket port is the page port plus one",
          webcast.socket_port == webcast.port + 1)
    check("publishing alone does not build a state engine",
          control.managed_window("presentation_a") is None)

    addr = getattr(panel, "_web_address", "")
    check("the panel offers a bare, copyable address",
          addr.startswith("http://") and addr.endswith("/screen/presentation_a")
          and " " not in addr, addr or "no address")

    control._on_power_toggled("presentation_a", True)
    control._on_power_toggled("presentation_b", True)
    pump(1000)
    check("powering on builds the state engine",
          control.managed_window("presentation_a") is not None)

    print("\n── The page ───────────────────────────────────────────────", flush=True)
    status, body = get("/")
    check("index responds", status == 200, f"status {status}")
    check("index lists both screens",
          b"presentation_a" in body and b"presentation_b" in body)
    status, page = get("/screen/presentation_a")
    check("screen page responds", status == 200, f"status {status}")
    check("screen page is told its own id and socket port",
          b'data-screen="presentation_a"' in page
          and f'data-ws-port="{webcast.socket_port}"'.encode() in page)
    check("no unsubstituted template holes left in the page",
          b"{{" not in page)
    # **A display must pick up a changed page, and once it did not.** The
    # stylesheet and script were served `max-age=86400`, so a browser that had
    # fetched them once kept using them for a day — a fresh page against a
    # stale design, with nothing anywhere reporting an error. Their URL now
    # carries a hash of their contents, so a change to either is a new URL.
    import re as _re
    versions = _re.findall(rb"/static/screen\.(?:css|js)\?v=([a-f0-9]+)", page)
    check("the page asks for its assets by content hash", len(versions) == 2,
          f"{len(versions)} versioned references")
    if versions:
        from app.webcast.server import asset_version
        live_version = asset_version()
        check("that hash is the one the server computes now",
              all(v.decode() == live_version for v in versions),
              live_version)
        css_path = pathlib.Path(__file__).resolve().parent.parent / \
            "assets" / "webcast" / "screen.css"
        original = css_path.read_text(encoding="utf-8")
        try:
            css_path.write_text(original + "\n/* check */\n", encoding="utf-8")
            changed = asset_version()
        finally:
            css_path.write_text(original, encoding="utf-8")
        check("editing the stylesheet changes the hash",
              changed != live_version, f"{live_version} -> {changed}")
        check("reverting it changes the hash back",
              asset_version() == live_version)

    for asset, kind in (("/static/screen.css", b"--u:"),
                        ("/static/screen.js", b"WebSocket"),
                        ("/fonts/ChakraPetch-Bold.ttf", b"\x00\x01\x00\x00")):
        status, blob = get(asset)
        check(f"serves {asset}", status == 200 and kind in blob,
              f"status {status}, {len(blob)} bytes")
    status, _ = get("/static/../../secrets/nexus_api_key")
    check("refuses a path-traversal request", status in (404, 400),
          f"status {status}")

    print("\n── The socket ─────────────────────────────────────────────", flush=True)
    listener = Listener("presentation_a")
    check("socket connects and sends state unprompted", listener.wait(1),
          f"{len(listener.messages)} messages")
    first = listener.latest()
    if first is None:
        check("state payload arrived", False, "nothing received")
        return _report()
    check("payload is a full screen state",
          first.get("screen") == "presentation_a" and "palette" in first
          and "team" in first and "dwell" in first)
    check("payload carries no error", not first.get("error"),
          first.get("error") or "")
    check("dwell is sent as a deadline, not a position",
          "deadline_ms" in first["dwell"] and "duration_ms" in first["dwell"])
    check("payload carries the server clock for skew correction",
          isinstance(first.get("server_now_ms"), int))
    size = len(json.dumps(first).encode())
    check("a full state is small", size < 20_000, f"{size:,} bytes")

    print("\n── Faces ──────────────────────────────────────────────────", flush=True)
    rot = first.get("rotation") or {}
    check("rotation face carries a slide and a position",
          "slide" in rot and "index" in rot and rot.get("count", 0) > 0,
          f"{rot.get('index')} of {rot.get('count')}")

    def face_payload(content, key, label):
        before = len(listener.messages)
        config.set("presentation_a", "content", content)
        pump(700)
        got = len(listener.messages) > before
        data = listener.latest() or {}
        ok = got and data.get("face") == content and key in data
        check(f"{label} face pushes a payload", ok,
              "" if ok else f"face={data.get('face')} keys={list(data)[:6]}")
        return data

    face_payload("checklist", "checklist", "Checklist")
    face_payload("diagnostics", "board", "Diagnostics board")
    face_payload("robot_info", "board", "Robot info board")
    nm = face_payload("next_match", "next_match", "Next match")
    check("the Next Match face credits frc.nexus (a condition of the API's use)",
          "FRC.NEXUS" in str(nm.get("ledger_right", "")), str(nm.get("ledger_right")))
    from app.db import db as _db
    _db.execute("INSERT INTO tba_fact (uid, category, team_number, text, sort, data) "
                "VALUES ('check_streak', '3937', '3937', 'Breakaway has brought home an "
                "award 13 seasons in a row.', 1, '{}')")
    fx = face_payload("facts", "facts", "Did you know?")
    check("the facts face carries home's sentence, ours first",
          (fx.get("facts") or {}).get("ours", [""])[0].startswith("Breakaway has brought"),
          str(fx.get("facts"))[:120])
    check("the facts face credits The Blue Alliance (a condition of the data's use)",
          "POWERED BY THE BLUE ALLIANCE" in str(fx.get("ledger_right", "")),
          str(fx.get("ledger_right")))
    config.set("presentation_a", "content", "rotation")
    pump(500)

    before = len(listener.messages)
    config.set_mode("lunch")
    pump(700)
    data = listener.latest() or {}
    check("lunch mode pushes the holding card",
          len(listener.messages) > before and data.get("face") == "lunch"
          and bool((data.get("lunch") or {}).get("headline")),
          (data.get("lunch") or {}).get("headline", ""))
    config.set_mode("standard")
    pump(500)

    print("\n── It is live ─────────────────────────────────────────────", flush=True)
    before = len(listener.messages)
    index_before = (listener.latest() or {}).get("rotation", {}).get("index")
    # What the 45 s timer does: move the shared program (app/rotation.py),
    # which then announces `advance`. Screens don't advance themselves.
    rotation._step()
    pump(900)
    after = listener.latest() or {}
    check("a rotation advance pushes a new state",
          len(listener.messages) > before,
          f"{len(listener.messages) - before} messages")
    # The new position, whatever the program's length (a fresh install's
    # program can be a single stop, which wraps to itself).
    check("the pushed state is the program's NEW position, not the old one",
          after.get("rotation", {}).get("index") == rotation.index
          and (rotation.index != index_before or after["rotation"].get("count") == 1),
          f"{index_before} -> {after.get('rotation', {}).get('index')} "
          f"(program at {rotation.index} of {after.get('rotation', {}).get('count')})")

    before = len(listener.messages)
    config.set_team(16)
    pump(700)
    check("a team change re-brands the page",
          len(listener.messages) > before
          and (listener.latest() or {}).get("team", {}).get("number") == 16)
    config.set_team(3937)
    pump(400)

    print("\n── Operator actions ───────────────────────────────────────", flush=True)
    check("viewers are counted", webcast.viewers >= 1, str(webcast.viewers))
    webcast.set_screen_published("presentation_b", False)
    pump(600)
    status, _ = get("/screen/presentation_b")
    check("an unpublished screen's page is refused", status == 404,
          f"status {status}")
    stray = Listener("presentation_b")
    stray.wait(1, ms=1500)
    refused = stray.latest("refused")
    check("an unpublished screen's socket refuses with a reason",
          refused is not None and "reason" in (refused or {}))
    stray.close()
    webcast.set_screen_published("presentation_b", True)
    pump(600)
    status, _ = get("/screen/presentation_b")
    check("republishing serves it again", status == 200, f"status {status}")

    print("\n── The sidebar switch turns a networked screen off ────────", flush=True)
    # The reported bug: a screen published to the network carried on rotating
    # after its own power switch was turned off, because power is not a
    # `config` key and so emitted nothing for the service to push on.
    before = len(listener.messages)
    control._on_power_toggled("presentation_a", False)
    pump(800)
    off = listener.latest() or {}
    check("turning the switch off pushes to the display",
          len(listener.messages) > before,
          f"{len(listener.messages) - before} messages")
    check("the display is told the screen is off",
          off.get("on") is False and off.get("face") == "off",
          f"on={off.get('on')} face={off.get('face')}")
    check("an off screen carries no face payload",
          "rotation" not in off and "checklist" not in off,
          ", ".join(k for k in ("rotation", "checklist") if k in off))
    check("the state engine is gone while it is off",
          control.managed_window("presentation_a") is None)

    # The page must treat "switched off" exactly like "the pit machine is
    # off" — one overlay, one message. A second, bespoke off design is the
    # thing this check exists to prevent coming back.
    _, off_js = get("/static/screen.js")
    off_src = off_js.decode("utf-8", "replace")
    branch = off_src.split("if (s.on === false) {", 1)
    # Comments stripped before looking: the explanation of *why* this branch
    # exists is longer than the branch, and a window measured in characters
    # would otherwise be measuring the prose.
    body = "\n".join(line for line in branch[1].splitlines()
                     if not line.strip().startswith("//"))[:300] \
        if len(branch) == 2 else ""
    routed = "showTrouble()" in body
    check("an off screen is routed to the same overlay as a lost connection",
          routed and "faceOff" not in off_src,
          "" if routed else "the off branch does not call showTrouble()")

    before = len(listener.messages)
    control._on_power_toggled("presentation_a", True)
    pump(900)
    back = listener.latest() or {}
    check("turning it back on pushes again",
          len(listener.messages) > before)
    check("the display is drawing the rotation again",
          back.get("on") is True and back.get("face") == "rotation"
          and "rotation" in back,
          f"on={back.get('on')} face={back.get('face')}")

    print("\n── What a visitor sees ────────────────────────────────────", flush=True)
    # These panels face a public pit. Nothing drawn on them may name a port,
    # a socket, a setting or the control panel — a stranger cannot act on any
    # of it, and asking them to is worse than saying the team is on it.
    _, js = get("/static/screen.js")
    _, page = get("/screen/presentation_a")
    text = js.decode("utf-8", "replace")
    # Only the *rendered* strings matter; the comments explain the rule.
    rendered = "\n".join(
        line.split("//")[0] for line in text.splitlines()
        if not line.strip().startswith("//"))
    leaks = [w for w in ("control panel", "Not published", "No judges slides",
                         "No match scheduled", "No robot log",
                         "Nothing to show", "Reconnecting to the pit")
             if w in rendered]
    check("no operator language is drawn on an audience screen",
          not leaks, ", ".join(leaks) or "")
    check("the welcome line is the one shown on every failure",
          "Breakaway welcomes you to our pit" in rendered
          and "technical difficulties" in rendered)
    check("the page ships that message even before any script runs",
          b"Breakaway welcomes you to our pit" in page
          and b"technical difficulties" in page)

    _, css = get("/static/screen.css")
    style = css.decode("utf-8", "replace")
    check("the pointer is never hidden on a networked display",
          "cursor: none" not in style and "cursor:none" not in style)
    check("the pointer is stated, not inherited",
          "body.screen * { cursor: default; }" in style)

    print("\n── Telemetry ──────────────────────────────────────────────", flush=True)
    live = webcast.telemetry().get("live", [])
    check("a connected display is reported", len(live) >= 1,
          f"{len(live)} connected")
    if live:
        row = live[0]
        check("telemetry names the screen, the address and the traffic",
              row.get("screen") == "presentation_a"
              and bool(row.get("address"))
              and row.get("messages", 0) > 0,
              f"{row.get('screen')} {row.get('address')} "
              f"{row.get('messages')} updates, {row.get('bytes')} bytes")
    hist = webcast.telemetry().get("recent", [])
    check("a display that was refused is recorded",
          any(r.get("refused") for r in hist),
          f"{len(hist)} recent entries")

    print("\n── Cost ───────────────────────────────────────────────────", flush=True)
    # The whole point of the rewrite. With a viewer attached and a slide
    # dwelling, the pit machine should be doing essentially nothing: no
    # rasterisation, no encoding, and no traffic until the slide changes.
    import resource

    def cpu():
        r = resource.getrusage(resource.RUSAGE_SELF)
        return r.ru_utime + r.ru_stime

    quiet_before = len(listener.messages)
    c0, t0 = cpu(), time.perf_counter()
    pump(6000)
    load = (cpu() - c0) * 100.0 / (time.perf_counter() - t0)
    sent = len(listener.messages) - quiet_before
    check("a dwelling screen sends nothing at all", sent == 0,
          f"{sent} messages in 6s")
    # The budget is deliberately tight. The JPEG build this replaced measured
    # 22% for one viewer; state-pushing plus stopping the repaint timers on a
    # never-displayed window brought a dwelling pair to ~0.1%. Anything that
    # puts periodic work back into this path will show up here immediately
    # rather than as "the pit machine feels slow" three weeks later.
    check("two published screens with a viewer stay under 2% of a core",
          load < 2.0, f"{load:.1f}%")

    print("\n── Socket lifetimes ───────────────────────────────────────", flush=True)
    # A display pointed at a screen that is not published is refused and
    # retries — screen.js reconnects every two seconds. Each attempt used to
    # leave an unparented socket for the garbage collector to destroy at some
    # later moment, mid-handshake.
    before = len(QT_WARNINGS)
    strays = []
    for _ in range(6):
        s2 = Listener("presentation_c")     # never a real screen
        strays.append(s2)
        pump(150)
    for s2 in strays:
        s2.close()
    pump(800)
    check("refusing a display repeatedly emits no Qt lifetime warnings",
          len(QT_WARNINGS) == before,
          "; ".join(QT_WARNINGS[before:])[:160])

    listener.close()
    pump(300)
    webcast.stop()
    pump(300)
    check("servers stop cleanly", not webcast.listening)

    lifetime = [w for w in QT_WARNINGS
                if "QObject::" in w or "QWebSocket" in w or "QTcpSocket" in w
                or "QNativeSocketEngine" in w]
    check("no Qt object-lifetime warnings during the whole run",
          not lifetime, "; ".join(lifetime)[:200])
    if QT_WARNINGS and not lifetime:
        print(f"  (note) {len(QT_WARNINGS)} unrelated Qt warnings: "
              f"{QT_WARNINGS[0][:90]}", flush=True)
    return _report()


def _report() -> int:
    print()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    if failed:
        print("\nFAILED:", flush=True)
        for name in failed:
            print(f"  - {name}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    _status = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_status)
