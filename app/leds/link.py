"""
Serial link to the LED controller — discovery, connection, and the I/O loop.

Everything here runs on a QThread. The GUI never blocks on a serial write: the
UI thread drops a frame on a queue and returns immediately.

Reconnection is the normal case, not the error case. Cables get kicked, boards
get replugged, and the port name changes when they do — so the link discovers
the board by USB VID/PID and confirms with a HELLO handshake rather than
trusting a remembered port. If it loses the device it backs off and keeps
looking, forever, without any user action.
"""

import os
import queue
import time

from PyQt6.QtCore import QThread, pyqtSignal

from app.leds import protocol as proto
from app.leds.protocol import DeviceInfo, Op

try:
    import serial
    from serial.tools import list_ports
    HAVE_SERIAL = True
except ImportError:                                    # pragma: no cover
    serial = None
    list_ports = None
    HAVE_SERIAL = False


# USB IDs for the boards that actually turn up on an FRC team's bench.
# (vid, pid) → human label. Anything matching gets a HELLO; only a board that
# answers correctly is accepted, so a random USB-serial adapter is never
# mistaken for the LED controller.
KNOWN_IDS: dict[tuple[int, int], str] = {
    (0x1A86, 0x7523): "CH340 (Nano clone)",
    (0x1A86, 0x5523): "CH341",
    (0x0403, 0x6001): "FTDI FT232",
    (0x2341, 0x0043): "Arduino Uno",
    (0x2341, 0x0001): "Arduino Uno (rev1)",
    (0x2A03, 0x0043): "Arduino Uno (org)",
    (0x2341, 0x0243): "Arduino Uno R3",
    (0x10C4, 0xEA60): "CP2102",
}

_BACKOFF = [1.0, 2.0, 3.0, 5.0, 8.0]   # seconds between reconnect attempts


def candidate_ports() -> list[tuple[str, str]]:
    """(device, label) for every port that looks like it could be the board."""
    if not HAVE_SERIAL:
        return []
    found = []
    for port in list_ports.comports():
        vid, pid = port.vid, port.pid
        if vid is None or pid is None:
            continue
        label = KNOWN_IDS.get((vid, pid))
        if label:
            found.append((port.device, label))
    if found:
        return found
    # Nothing matched a known ID. Fall back to anything that presents as a
    # USB serial device — a board we have not catalogued still deserves a
    # HELLO, and the handshake is what actually decides.
    return [
        (p.device, p.description or "serial device")
        for p in list_ports.comports()
        if p.vid is not None
    ]


class SerialLink(QThread):
    """
    Owns the port. Emits on the GUI thread (Qt queues cross-thread signals).

      connected(DeviceInfo, str)  — handshake completed; str is the port name
      disconnected(str)           — link dropped; str is a human reason
      status(str)                 — progress text for the control panel
      frame(int, bytes)           — an inbound device→host frame (op, payload)
      link_error(str)             — something worth surfacing to the operator
    """

    connected = pyqtSignal(object, str)
    disconnected = pyqtSignal(str)
    status = pyqtSignal(str)
    frame = pyqtSignal(int, bytes)
    link_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._out: queue.Queue[bytes] = queue.Queue(maxsize=256)
        self._running = False
        self._seq = 0
        self._port: "serial.Serial | None" = None
        self._device: DeviceInfo | None = None
        self._port_name = ""
        self._reader = proto.FrameReader()

    # ── Public API (call from any thread) ─────────────────────────────────

    @property
    def device(self) -> DeviceInfo | None:
        return self._device

    @property
    def port_name(self) -> str:
        return self._port_name

    @property
    def is_connected(self) -> bool:
        return self._device is not None

    def send(self, op: int, payload: bytes = b"") -> None:
        """Queue a frame. Never blocks; drops the oldest if the queue backs up."""
        self._seq = (self._seq + 1) & 0xFF
        data = proto.encode_frame(self._seq, op, payload)
        try:
            self._out.put_nowait(data)
        except queue.Full:
            try:
                self._out.get_nowait()          # shed the stalest command
                self._out.put_nowait(data)
            except queue.Empty:
                pass

    def stop(self) -> None:
        self._running = False
        self.wait(2500)

    # ── Thread body ───────────────────────────────────────────────────────

    def run(self) -> None:
        self._running = True
        if not HAVE_SERIAL:
            self.link_error.emit(
                "pyserial is not installed — run `uv sync` to enable LED output."
            )
            return

        attempt = 0
        while self._running:
            if self._try_connect():
                attempt = 0
                self._pump()                    # blocks until the link drops
            if not self._running:
                break
            delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            attempt += 1
            self.status.emit(f"No controller found — retrying in {delay:.0f}s")
            self._sleep(delay)

        self._close("shutting down")

    # ── Connect ───────────────────────────────────────────────────────────

    def _try_connect(self) -> bool:
        for device, label in candidate_ports():
            if not self._running:
                return False
            self.status.emit(f"Trying {device} ({label})…")
            if self._handshake(device):
                return True
        return False

    def _handshake(self, device: str) -> bool:
        try:
            port = serial.Serial(device, proto.BAUD, timeout=0.05, write_timeout=1.0)
        except Exception as exc:
            self.status.emit(f"{device}: {exc}")
            return False

        try:
            # Opening the port asserts DTR, which resets an Uno/Nano into the
            # bootloader. Anything sent now lands in the void — wait it out.
            self._sleep(proto.BOOT_DELAY_S)
            if not self._running:
                port.close()
                return False
            port.reset_input_buffer()

            self._reader.reset()
            port.write(proto.encode_frame(0, Op.HELLO))
            port.flush()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                chunk = port.read(256)
                if chunk:
                    for _seq, op, payload in self._reader.feed(chunk):
                        if op == Op.INFO:
                            info = proto.parse_info(payload)
                            self._port = port
                            self._device = info
                            self._port_name = device
                            self.connected.emit(info, device)
                            return True
                if not self._running:
                    break
        except Exception as exc:
            self.status.emit(f"{device}: {exc}")

        try:
            port.close()
        except Exception:
            pass
        return False

    # ── I/O loop ──────────────────────────────────────────────────────────

    def _pump(self) -> None:
        assert self._port is not None
        last_ping = 0.0
        while self._running:
            try:
                now = time.monotonic()
                if now - last_ping >= proto.HEARTBEAT_S:
                    last_ping = now
                    self.send(Op.PING)

                while True:
                    try:
                        self._port.write(self._out.get_nowait())
                    except queue.Empty:
                        break

                chunk = self._port.read(256)
                if chunk:
                    for _seq, op, payload in self._reader.feed(chunk):
                        self.frame.emit(int(op), bytes(payload))
                else:
                    self.msleep(5)
            except Exception as exc:
                self._close(str(exc))
                return

    def _close(self, reason: str) -> None:
        was_up = self._device is not None
        self._device = None
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None
        if was_up:
            self.disconnected.emit(reason)
        self._port_name = ""

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep — never delays shutdown by more than 100ms."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            self.msleep(min(100, int((end - time.monotonic()) * 1000) + 1))


class MockLink(SerialLink):
    """
    A link that pretends. Enabled with PIT_LEDS_FAKE=1.

    The pit machine is Windows and development happens on a Mac, so the board
    usually is not attached while the panel is being built. This keeps the
    whole UI exercisable — it connects, accepts frames, and answers pings.
    """

    def __init__(self, led_count: int = 150, parent=None):
        super().__init__(parent)
        self._fake_count = led_count
        self.sent: list[tuple[int, bytes]] = []

    def send(self, op: int, payload: bytes = b"") -> None:
        self.sent.append((int(op), bytes(payload)))

    def run(self) -> None:
        self._running = True
        self.status.emit("Mock controller (PIT_LEDS_FAKE=1)")
        self._sleep(0.3)
        if not self._running:
            return
        # Reports the current firmware's shape — two segments, fw 2.2 — so
        # the alert path (per-segment colour, the white die) exercises the
        # same branches it will on the real controller.
        half = self._fake_count // 2
        info = proto.DeviceInfo(
            fw_major=2, fw_minor=2, led_count=self._fake_count,
            segments=(proto.Segment(0, half, "centre"),
                      proto.Segment(half, self._fake_count - half, "sides")),
        )
        self._device = info
        self._port_name = "mock"
        self.connected.emit(info, "mock")
        while self._running:
            self.msleep(100)
        self._device = None
        self.disconnected.emit("shutting down")


def make_link(parent=None) -> SerialLink:
    """MockLink when PIT_LEDS_FAKE is set, otherwise the real thing."""
    if os.environ.get("PIT_LEDS_FAKE", "").strip() not in ("", "0", "false", "no"):
        count = int(os.environ.get("PIT_LEDS_COUNT", "150") or 150)
        return MockLink(led_count=count, parent=parent)
    return SerialLink(parent=parent)
