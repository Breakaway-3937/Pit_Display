"""
The overhead screens, published to the pit LAN as **web pages**.

    ControlScreen ──builds headless──▶ PresentationScreenA/B   (state engine)
                                            │
    config / rotation / checklist  ──signals─┤
                                            ▼
    Pi on the switch ◀── ws:// JSON ── ScreenSocketServer   (:3939)
                     ◀── http ─────── WebcastServer         (:3938)

**What it is for: one Cat6 instead of one HDMI.** The two overhead panels hang
on the far side of the pit; a Raspberry Pi at each one opens a page on the pit
machine and shows that screen full-screen.

**The browser draws. The pit machine sends state.** The first build rasterised
each screen to JPEG several times a second — ~15 ms of full-chassis repaint
plus encode per frame, two screens at a quarter of a core forever, and a soft
picture at the far end. It was redoing expensive work to say that nothing had
changed, because a slide holds for 45 seconds. Now a slide change is one ~1 KB
message and the socket is otherwise silent; the dwell rail is animated by the
browser against a deadline, so it costs the pit machine nothing at all.

The protocol follows **Cheesy Arena**: `{"type": ..., "data": ...}`, full state
on connect, deltas after, and the screen named in the socket path.

| File | Role |
|---|---|
| `settings.py` | `webcast.json` — which screens, the port, the bind |
| `state.py`    | What a screen *is*, as JSON. No widgets, no pixels |
| `sockets.py`  | `QWebSocketServer` on the Qt event loop — no threads |
| `server.py`   | The page, its CSS and JS, the fonts, the judges artwork |
| `service.py`  | `_WebcastService` singleton — subscriptions and lifetime |

The page itself is `assets/webcast/` — edited like the web asset it is, the
same way the CAD viewer lives under `assets/cad_viewer/`.

`uv run tools/webcast_check.py` runs the automated checks against a live
server: cold start through the real control-panel toggle, the socket, every
face's payload, and the cost. Exit 0/1.

It is **off by default** and pit-local by design. See `server.py` for why there
is no authentication and why those ports must never be on event wifi.
"""

from app.webcast.server import lan_address
from app.webcast.service import init_webcast, webcast

__all__ = ["webcast", "init_webcast", "lan_address"]
