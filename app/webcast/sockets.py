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
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QHostAddress
from PyQt6.QtWebSockets import QWebSocketServer

from app.webcast import settings, state


class ScreenSocketServer(QObject):
    """Accepts page connections and fans state out to them."""

    # screen id, number of viewers on it — the control panel's proof it works
    viewers_changed = pyqtSignal(str, int)
    log = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._server: QWebSocketServer | None = None
        self._clients: dict[str, set] = {s: set() for s in settings.PUBLISHABLE}
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
        for screen_id, clients in self._clients.items():
            for socket in list(clients):
                socket.close()
            clients.clear()
            self.viewers_changed.emit(screen_id, 0)
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None

    # ── Connections ──────────────────────────────────────────────────────

    def _on_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            screen_id = socket.requestUrl().path().strip("/")
            if screen_id not in self._clients or not settings.is_published(screen_id):
                # Say why before closing, so a page can show something useful
                # rather than reconnecting forever against a screen that is
                # not published.
                self._send(socket, "refused",
                           {"reason": f"{screen_id or 'that screen'} is not "
                                      "being published from this machine."})
                socket.close()
                continue

            self._clients[screen_id].add(socket)
            socket.disconnected.connect(
                lambda s=socket, i=screen_id: self._drop(i, s))
            # Cheesy Arena's rule: the full current state on connect, deltas
            # after. A page never has to ask for anything.
            self._send(socket, "screen", state.screen_state(screen_id))
            self.viewers_changed.emit(screen_id, len(self._clients[screen_id]))

    def _drop(self, screen_id: str, socket) -> None:
        self._clients.get(screen_id, set()).discard(socket)
        socket.deleteLater()
        self.viewers_changed.emit(screen_id, self.viewers(screen_id))

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
        self.broadcast(screen_id, "screen", state.screen_state(screen_id))

    def push_all(self) -> None:
        for screen_id in settings.PUBLISHABLE:
            self.push_state(screen_id)

    @staticmethod
    def _send(socket, kind: str, data: Any) -> None:
        try:
            socket.sendTextMessage(json.dumps({"type": kind, "data": data}))
        except (TypeError, ValueError, RuntimeError):
            # A payload that will not serialise, or a socket Qt already tore
            # down. Neither may take the pit display with it.
            pass
