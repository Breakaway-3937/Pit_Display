"""
The websocket the pages listen on. `QWebSocketServer`, on the Qt event loop.

    ws://<pit machine>:3939/presentation_a

**No threads, and that is the point.** `QtWebSockets` ships in the standard
PyQt6 wheel, so this costs no dependency, and because it runs on the same
event loop as everything else it can read `config`, `rotation` and the
checklist directly — no locks, no queues, and none of the GUI-thread
marshalling the pixel path needed to render a frame for an HTTP worker.

An idle connection costs nothing at all. Between slides there is literally no
traffic: the dwell rail is animated by the browser against a deadline, so the
server is silent for 45 seconds at a time.

**The shape is Cheesy Arena's.** Every message is `{"type": ..., "data": ...}`.
A page names the screen it wants in the socket path, gets the whole current
state the moment it connects, and deltas after that. A client that misses a
message is not a problem worth solving with sequence numbers: every `screen`
message is a complete state, so the next one repairs whatever was missed.

Nothing a client sends is acted on. These two screens are output-only and the
control panel is the only thing that drives them; a page that could change the
pit by sending a frame on an unauthenticated socket would be a much worse idea
than a page that cannot.
"""

from __future__ import annotations

import json
import time
from typing import Any

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, pyqtSignal
from PyQt6.QtNetwork import QHostAddress
from PyQt6.QtWebSockets import QWebSocketServer

from app.webcast import settings, state


class ScreenSocketServer(QObject):
    """Accepts page connections and fans state out to them."""

    # screen id, number of viewers on it — the control panel's proof it works
    viewers_changed = pyqtSignal(str, int)
    # A display connected, dropped, or was refused. The telemetry panel
    # redraws on this rather than polling on a timer.
    telemetry_changed = pyqtSignal()
    log = pyqtSignal(str)

    def __init__(self, parent=None, is_on=None) -> None:
        super().__init__(parent)
        # Whether a screen's sidebar power switch is on. A callable, because
        # power lives with the window the control screen owns and is not a
        # `config` key this module could read for itself.
        self._is_on = is_on or (lambda _screen_id: True)
        self._server: QWebSocketServer | None = None
        self._clients: dict[str, set] = {s: set() for s in settings.PUBLISHABLE}
        # Per-connection counters, keyed by the socket. **Kept here and not on
        # the socket object**: PyQt does not let you hang attributes on a
        # QWebSocket reliably once C++ owns it, and a dict the server clears
        # on disconnect cannot outlive the thing it describes.
        self._stats: dict[Any, dict] = {}
        # The `disconnected` connection for each socket, so it can be undone
        # by handle. See `_retire()` for why a wildcard disconnect is not an
        # option here.
        self._conns: dict[Any, Any] = {}
        # A short history of connections that have ended, so an operator
        # looking at a panel after a display dropped can still see that it was
        # ever there. Without it a flapping Pi is invisible: it reconnects
        # before anybody looks, and the panel says everything is fine.
        self._recent: list[dict] = []
        self._port = 0

    # ── Lifecycle ────────────────────────────────────────────────────────

    @property
    def listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    @property
    def port(self) -> int:
        return self._port

    def viewers(self, screen_id: str | None = None) -> int:
        if screen_id is None:
            return sum(len(c) for c in self._clients.values())
        return len(self._clients.get(screen_id, ()))

    def start(self, port: int, bind: str) -> str:
        """Bind and listen. Returns `""`, or the reason it could not."""
        if self.listening:
            return ""
        server = QWebSocketServer("breakaway-pit-display",
                                  QWebSocketServer.SslMode.NonSecureMode, self)
        address = (QHostAddress.SpecialAddress.LocalHost
                   if bind == "127.0.0.1" else QHostAddress.SpecialAddress.Any)
        if not server.listen(QHostAddress(address), port):
            reason = server.errorString() or "unknown error"
            server.deleteLater()
            return f"Could not open the screen socket on port {port}: {reason}"
        server.newConnection.connect(self._on_connection)
        self._server = server
        self._port = port
        return ""

    def stop(self) -> None:
        """
        Close every display's socket and stop listening — deterministically.

        `abort()` rather than `close()`: a graceful close sends a frame and
        waits for the peer's reply, and on shutdown the event loop is about to
        stop, so that reply never arrives and the socket is destroyed
        mid-handshake. Aborting drops it now, which is what a display sees as
        a disconnect either way — and it reconnects by itself.

        The deferred deletes are then drained by hand. `deleteLater()` is only
        honoured while an event loop is running; at shutdown there may not be
        another pass, and the objects would instead be destroyed later by
        QApplication's teardown, in an order Qt warns about.
        """
        for screen_id, clients in self._clients.items():
            for socket in list(clients):
                self._retire(socket)
                socket.abort()
            clients.clear()
            self.viewers_changed.emit(screen_id, 0)
        self._stats.clear()
        self._conns.clear()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.telemetry_changed.emit()

    # ── Connections ──────────────────────────────────────────────────────

    def _on_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            # **Take ownership immediately.** `nextPendingConnection()` hands
            # back an *unparented* QWebSocket in PyQt, so Python owns it — and
            # a socket nothing holds a reference to is collected whenever the
            # interpreter feels like it, which destroys the C++ object while
            # its close handshake is still in flight. That is where
            #
            #   QObject::disconnect: wildcard call disconnects from destroyed
            #   signal of QWebSocketDataProcessor::unnamed
            #
            # comes from, along with the QTcpSocket and QNativeSocketEngine
            # lines beside it: they are QWebSocket's own internals being torn
            # down out from under it. Giving it a C++ parent puts its lifetime
            # back under our control, so it ends when we say and not when a
            # collection happens to run.
            socket.setParent(self)

            screen_id = socket.requestUrl().path().strip("/")
            if screen_id not in self._clients or not settings.is_published(screen_id):
                # Say why before closing, so a page can show something useful
                # rather than reconnecting forever against a screen that is
                # not published. The close is asynchronous — the frame still
                # has to go out — so the socket is retired when the peer
                # actually goes away, never here.
                self._send(socket, "refused",
                           {"reason": f"{screen_id or 'that screen'} is not "
                                      "being published from this machine."})
                self.note_refused(screen_id,
                                  socket.peerAddress().toString(),
                                  "screen is not published")
                self._conns[socket] = socket.disconnected.connect(
                    lambda s=socket: self._retire(s))
                socket.close()
                continue

            self._clients[screen_id].add(socket)
            self._stats[socket] = {
                "screen": screen_id,
                "address": socket.peerAddress().toString(),
                "connected_at": time.time(),
                "messages": 0,
                "bytes": 0,
                "last_ms": 0.0,
            }
            self._conns[socket] = socket.disconnected.connect(
                lambda s=socket, i=screen_id: self._drop(i, s))
            # Cheesy Arena's rule: the full current state on connect, deltas
            # after. A page never has to ask for anything.
            self._send(socket, "screen",
                       state.screen_state(screen_id, self._is_on(screen_id)))
            self.viewers_changed.emit(screen_id, len(self._clients[screen_id]))
            self.telemetry_changed.emit()

    def _drop(self, screen_id: str, socket) -> None:
        self._clients.get(screen_id, set()).discard(socket)
        record = self._stats.pop(socket, None)
        if record is not None:
            record["ended_at"] = time.time()
            self._recent.append(record)
            del self._recent[:-8]
        self._retire(socket)
        self.viewers_changed.emit(screen_id, self.viewers(screen_id))
        self.telemetry_changed.emit()

    def _retire(self, socket) -> None:
        """
        Let one socket go, in the one order that is safe.

        Two rules, and both were measured rather than assumed:

        * **Disconnect by handle, never `disconnect()` with no arguments.** A
          wildcard disconnect on an object *from inside that object's own
          signal emission* is precisely what produces

              QObject::disconnect: wildcard call disconnects from destroyed
              signal of QWebSocket::unnamed

          — reproduced four times out of four against plain Qt. This runs
          inside `disconnected`, so it is that situation exactly. Undoing the
          one connection we made cannot touch anything Qt is still using.
        * **`deleteLater()`, never a direct delete.** Destroying the socket
          inside its own signal takes the process down rather than warning.
        """
        conn = self._conns.pop(socket, None)
        if conn is not None:
            try:
                socket.disconnected.disconnect(conn)
            except (TypeError, RuntimeError):
                pass      # already undone, or the C++ side has gone
        socket.deleteLater()

    def note_refused(self, screen_id: str, address: str, reason: str) -> None:
        """Record a display that asked for a screen it could not have."""
        self._recent.append({
            "screen": screen_id or "unknown", "address": address,
            "connected_at": time.time(), "ended_at": time.time(),
            "messages": 0, "bytes": 0, "last_ms": 0.0, "refused": reason,
        })
        del self._recent[:-8]
        self.telemetry_changed.emit()

    # ── Telemetry ────────────────────────────────────────────────────────

    def telemetry(self) -> dict:
        """
        What the control panel draws: who is connected, and how it is going.

        Read straight out of the live counters rather than sampled on a timer,
        because the only thing that changes them is a message actually being
        sent — and between slides there are none.
        """
        now = time.time()
        live = []
        for socket, record in self._stats.items():
            live.append({
                "screen": record["screen"],
                "address": record["address"],
                "uptime_s": now - record["connected_at"],
                "messages": record["messages"],
                "bytes": record["bytes"],
                "last_ms": record["last_ms"],
                "open": True,
                "valid": socket.isValid(),
            })
        live.sort(key=lambda r: (r["screen"], r["address"]))
        past = [{**r, "uptime_s": r["ended_at"] - r["connected_at"],
                 "open": False} for r in reversed(self._recent)]
        return {"live": live, "recent": past}

    # ── Sending ──────────────────────────────────────────────────────────

    def broadcast(self, screen_id: str, kind: str, data: Any) -> None:
        clients = self._clients.get(screen_id)
        if not clients:
            return            # nobody watching: building the payload is waste
        for socket in list(clients):
            self._send(socket, kind, data)

    def push_state(self, screen_id: str) -> None:
        """The whole current state, if anybody is looking at this screen."""
        if not self._clients.get(screen_id):
            return
        self.broadcast(screen_id, "screen",
                       state.screen_state(screen_id, self._is_on(screen_id)))

    def push_all(self) -> None:
        for screen_id in settings.PUBLISHABLE:
            self.push_state(screen_id)

    def _send(self, socket, kind: str, data: Any) -> None:
        started = time.perf_counter()
        try:
            payload = json.dumps({"type": kind, "data": data})
            socket.sendTextMessage(payload)
        except (TypeError, ValueError, RuntimeError):
            # A payload that will not serialise, or a socket Qt already tore
            # down. Neither may take the pit display with it.
            return
        record = self._stats.get(socket)
        if record is not None:
            record["messages"] += 1
            record["bytes"] += len(payload.encode("utf-8"))
            record["last_ms"] = (time.perf_counter() - started) * 1000.0
