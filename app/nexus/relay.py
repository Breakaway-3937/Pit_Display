"""
`RelayLink` — the pit display's WebSocket to the team's Nexus relay.

    nexus-relay (Cloudflare) ──wss://…/api/v1/event/{key}/ws──▶ RelayLink ──▶ _NexusService._offer_status()

The relay (`nexus-relay/`, `https://nexus.bh-stack.com`) receives Nexus's
webhooks and pushes every newer snapshot down this socket the moment it has
one; on connect it sends what it holds, so a reconnecting pit is never blank.
Every message is JSON:

    {"type": "status", "data": <EventStatus>, "relay": <stats>}
    {"type": "relay",  "relay": <stats>}          counters moved, no new data

**Qt's own socket, on the Qt event loop.** `QtWebSockets` ships in the
standard PyQt6 wheel, so there is no thread and no dependency — the same
reasoning as `app/webcast/sockets.py`, from the other end of a socket.

Three timers, and each is load bearing:

- **Ping every 30s.** Cloudflare drops a WebSocket idle for ~100s. The relay
  answers `"ping"` with `"pong"` from the runtime without waking anything, so
  the heartbeat costs nothing at the far end.
- **A 75s watchdog, restarted by any traffic.** Venue wifi loses a socket in
  a way the OS does not notice for minutes: no FIN, no RST, just silence. Two
  missed pongs is a dead link; abort it and dial again.
- **Backoff from 1s to 30s.** A relay that is down is not helped by being
  asked forty times a minute by every pit machine.

**`stop()` means stopped.** The reconnect path checks `_wanted`, so an
intentional close — the event key changed, the relay was switched off — never
schedules a redial.

Everything it does is counted in `telemetry()`, which is what
Control → Pit Systems → Telemetry draws.
"""

from __future__ import annotations

import json
import time
from typing import Any

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket, QNetworkRequest
from PyQt6.QtWebSockets import QWebSocket

from app.nexus.api import EventStatus, parse_event_status

_HISTORY = 8


class RelayLink(QObject):

    status_received = pyqtSignal(object)    # EventStatus
    stats_received = pyqtSignal(dict)       # the relay's own counters
    connection_changed = pyqtSignal(bool)
    telemetry_changed = pyqtSignal()

    PING_MS = 30_000
    STALE_MS = 75_000
    MAX_BACKOFF_MS = 30_000

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._url = QUrl()
        self._token = b""
        self._event_key = ""
        self._wanted = False
        self._backoff_ms = 1_000

        # Telemetry. Wall-clock seconds, so the panel can say "12s ago".
        self._state = "off"          # off · connecting · live · reconnecting
        self._connected_at = 0.0
        self._last_message_at = 0.0
        self._last_status_at = 0.0
        self._messages = 0
        self._bytes = 0
        self._snapshots = 0
        self._attempts = 0
        self._reconnects = 0
        self._last_error = ""
        self._stats: dict[str, Any] = {}
        self._stats_at = 0.0
        self._history: list[dict[str, Any]] = []

        self._ws = QWebSocket(parent=self)
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_text)
        # Some failures (DNS, TLS, a refused upgrade) emit errorOccurred
        # without disconnected, so it can schedule the redial too.
        self._ws.errorOccurred.connect(self._on_error)

        self._ping = QTimer(self)
        self._ping.setInterval(self.PING_MS)
        self._ping.timeout.connect(self._send_ping)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(self.STALE_MS)
        self._watchdog.timeout.connect(self._on_stale)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._dial)

    # ── Control ──────────────────────────────────────────────────────────

    def start(self, relay_url: str, event_key: str, token: str) -> None:
        """Connect to `event_key`'s room, replacing any current connection."""
        ws_base = relay_url.rstrip("/")
        if ws_base.startswith("https://"):
            ws_base = "wss://" + ws_base[len("https://"):]
        elif ws_base.startswith("http://"):
            ws_base = "ws://" + ws_base[len("http://"):]
        key = event_key.strip().lower()
        self.stop()
        self._url = QUrl(f"{ws_base}/api/v1/event/{key}/ws")
        self._token = token.encode()
        self._event_key = key
        self._wanted = True
        self._backoff_ms = 1_000
        self._stats = {}
        self._stats_at = 0.0
        self._set_state("connecting")
        self._dial()

    def stop(self) -> None:
        was = self._state == "live"
        self._wanted = False
        self._retry.stop()
        self._ping.stop()
        self._watchdog.stop()
        # State first: abort() can emit `disconnected` synchronously, and that
        # handler must see an intentional close, not a drop to record and
        # announce a second time.
        self._set_state("off")
        if self._ws.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._ws.abort()
        if was:
            self.connection_changed.emit(False)

    def refresh(self) -> bool:
        """Ask the relay to pull Nexus now. False when not connected."""
        if not self.connected:
            return False
        self._ws.sendTextMessage("refresh")
        return True

    # ── Read-only ────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._state == "live"

    @property
    def state(self) -> str:
        return self._state

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self._stats)

    def telemetry(self) -> dict[str, Any]:
        now = time.time()
        return {
            "state": self._state,
            "url": self._url.toString(),
            "event_key": self._event_key,
            "uptime_s": now - self._connected_at if self._state == "live" else 0.0,
            "last_message_s": (now - self._last_message_at
                               if self._last_message_at else None),
            "last_status_s": (now - self._last_status_at
                              if self._last_status_at else None),
            "messages": self._messages,
            "bytes": self._bytes,
            "snapshots": self._snapshots,
            "attempts": self._attempts,
            "reconnects": self._reconnects,
            "retry_in_ms": (self._retry.remainingTime()
                            if self._retry.isActive() else None),
            "last_error": self._last_error,
            "relay": dict(self._stats),
            "relay_age_s": now - self._stats_at if self._stats_at else None,
            "history": list(self._history),
        }

    # ── Socket ───────────────────────────────────────────────────────────

    def _dial(self) -> None:
        if not self._wanted:
            return
        self._attempts += 1
        req = QNetworkRequest(self._url)
        # open(QNetworkRequest) is how a header gets onto the upgrade request.
        req.setRawHeader(b"Authorization", b"Bearer " + self._token)
        self._ws.open(req)

    def _send_ping(self) -> None:
        self._ws.sendTextMessage("ping")

    def _on_connected(self) -> None:
        self._backoff_ms = 1_000
        self._connected_at = time.time()
        self._last_error = ""
        self._ping.start()
        self._watchdog.start()
        self._set_state("live")
        self.connection_changed.emit(True)

    def _on_text(self, message: str) -> None:
        self._watchdog.start()          # any traffic, pong included, is life
        self._messages += 1
        self._bytes += len(message)
        self._last_message_at = time.time()
        if message == "pong":
            self.telemetry_changed.emit()
            return
        try:
            body = json.loads(message)
        except ValueError:
            return
        if not isinstance(body, dict):
            return
        stats = body.get("relay")
        if isinstance(stats, dict):
            self._stats = stats
            self._stats_at = time.time()
            self.stats_received.emit(dict(stats))
        if body.get("type") == "status" and isinstance(body.get("data"), dict):
            try:
                status: EventStatus = parse_event_status(body["data"])
            except Exception as exc:        # a shape we do not know is news
                self._last_error = f"unreadable snapshot: {type(exc).__name__}: {exc}"
            else:
                self._snapshots += 1
                self._last_status_at = time.time()
                self.status_received.emit(status)
        self.telemetry_changed.emit()

    def _on_stale(self) -> None:
        self._last_error = (f"no traffic for {self.STALE_MS // 1000}s — "
                            "the link was dead without saying so")
        self._ws.abort()                  # → disconnected → redial

    def _on_disconnected(self) -> None:
        self._ping.stop()
        self._watchdog.stop()
        was_live = self._state == "live"
        if was_live:
            self._history.insert(0, {
                "ended_at": time.time(),
                "uptime_s": time.time() - self._connected_at,
                "reason": self._last_error or self._close_reason(),
            })
            del self._history[_HISTORY:]
            self.connection_changed.emit(False)
        if self._wanted:
            self._schedule_reconnect()
        else:
            self._set_state("off")

    def _on_error(self, _err: QAbstractSocket.SocketError) -> None:
        self._last_error = self._ws.errorString()
        if (self._wanted
                and self._ws.state() == QAbstractSocket.SocketState.UnconnectedState):
            self._schedule_reconnect()
        self.telemetry_changed.emit()

    def _close_reason(self) -> str:
        code = self._ws.closeCode()
        reason = self._ws.closeReason()
        return f"closed by the relay ({code.value if hasattr(code, 'value') else code}" \
               + (f", {reason})" if reason else ")")

    def _schedule_reconnect(self) -> None:
        if self._retry.isActive() or not self._wanted:
            return
        self._reconnects += 1
        self._set_state("reconnecting")
        self._retry.start(self._backoff_ms)
        self._backoff_ms = min(self._backoff_ms * 2, self.MAX_BACKOFF_MS)

    def _set_state(self, state: str) -> None:
        self._state = state
        self.telemetry_changed.emit()
