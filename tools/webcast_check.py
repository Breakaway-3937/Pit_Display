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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PIT_LEDS_FAKE", "1")
os.environ.setdefault("PIT_NEXUS_QUIET", "1")

from app.console import use_utf8  # noqa: E402

PORT = 3993
RESULTS: list[tuple[str, bool, str]] = []
_APP = None


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
    from PyQt6.QtWidgets import QApplication
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
    for fn in (init_config, init_update, init_db, init_rotation,
               init_judges_slides, init_cad_assets, init_leds, init_music,
               init_admin, init_checklist, init_nexus, init_alerts):
        fn()

    from app.windows.control_screen import ControlScreen
    from app.windows.presentation_a import PresentationScreenA
    from app.windows.presentation_b import PresentationScreenB

    wset.save(enabled=False, screens=list(wset.PUBLISHABLE),
              port=PORT, bind="127.0.0.1")

    control = ControlScreen()
    control.set_window_factories({"presentation_a": PresentationScreenA,
                                  "presentation_b": PresentationScreenB})
    webcast = init_webcast()
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
    face_payload("next_match", "next_match", "Next match")
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
    rotation.advance.emit()
    pump(900)
    after = listener.latest() or {}
    check("a rotation advance pushes a new state",
          len(listener.messages) > before,
          f"{len(listener.messages) - before} messages")
    check("the pushed state is the NEW slide, not the old one",
          after.get("rotation", {}).get("index") != index_before,
          f"{index_before} -> {after.get('rotation', {}).get('index')}")

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

    listener.close()
    pump(200)
    webcast.stop()
    check("servers stop cleanly", not webcast.listening)
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
