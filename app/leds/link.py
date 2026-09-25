"""
Serial link to the LED controller — discovery, connection, and the I/O loop.

Everything here runs on a QThread. The GUI never blocks on a serial write: the
UI thread drops a frame on a queue and returns immediately.

**Every command that changes the strips is delivered, not just sent.** The
controller is deaf while it writes a strip — its UART holds two bytes with
interrupts off — so on an animated look about half of all frames were lost
(measured 2026-09-24), and a lost "go white" left the pit sparkling while the
app believed otherwise. Output commands now go one at a time, in order, and
each is resent until the controller ACKs it (`_RETRY_S` plus jitter); a newer command for
the same setting replaces an older one still waiting, so a backlog never
replays stale looks. PING and STATUS are probes: sent once, never retried.

**Nothing blocks on the port.** Reads are non-blocking and the thread sleeps
on an event that `send()` sets, so a command goes out the moment it is queued
and an ACK is read the moment it lands. The old loop blocked in `read()` for
its whole 50 ms timeout, which alone added ~27 ms (median) to every command
and ~54 ms to every round trip.

Reconnection is the normal case, not the error case. Cables get kicked, boards
get replugged, and the port name changes when they do — so the link discovers
the board by USB VID/PID and confirms with a HELLO handshake rather than
trusting a remembered port. If it loses the device it backs off and keeps
looking, forever, without any user action.
"""

import os
import random
import threading
import time
from collections import deque

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

# Output commands: resent until ACKed. The firmware ACKs as soon as it parses a
# frame (a few ms, USB included), so 25 ms without one means the frame was lost
# to a strip write. Give up after ~1.5 s of trying — the link is failing, and
# the service's resync (it compares the controller's STATUS against intent)
# will put the look back once it recovers.
_RELIABLE = frozenset({Op.SET_MODE, Op.SET_COLOR, Op.SET_BRIGHT,
                       Op.SET_PIXELS, Op.SAVE, Op.OFF, Op.SET_CAP})
_RETRY_S = 0.012
# …plus up to this much random delay. A fixed interval falls into step with
# the firmware's frame period and lands in the same deaf window over and over
# (measured: p95 delivery 58 ms at a fixed 25 ms); jitter breaks the lock-step.
_RETRY_JITTER_S = 0.014
_MAX_TRIES = 60
# A PING or STATUS with no answer after this long is counted lost.
_PROBE_TIMEOUT_S = 1.0
# Output commands waiting to go. Latest-wins coalescing keeps this tiny in
# practice; the cap only matters for a flood of SET_PIXELS.
_QUEUE_CAP = 64

# How often to ask a fw 2.5+ controller for its own telemetry. 39 bytes back,
# and the firmware does not redraw for it, so this costs the link nothing.
_STATUS_EVERY_S = 2.0

# Round trips and queue waits kept for percentiles.
_SAMPLES = 200


class LinkStats:
    """
    What the serial link has done, for Control → Pit Systems → Telemetry.

    Written by the link's own thread as frames go out and come back, read by
    the GUI thread as a snapshot, so every access holds the lock. Nothing here
    needs the firmware to report anything new: it is built from what the
    controller already sends — an ACK for every command (matched to the
    command by sequence number, which gives the round-trip time), a NAK for a
    frame it rejected, and a LOG line when the three-way switch moves.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[int, float] = {}      # seq → monotonic time written
        self.reboots = 0            # controller uptime went backwards while connected
        self.reset_session()
        self.attempts = 0
        self.reconnects = 0
        self.drops: deque[dict] = deque(maxlen=8)
        self.logs: deque[tuple[float, str]] = deque(maxlen=12)
        self.switch = ""            # "WHITE" / "RED" / "HOST", "" = not reported yet
        self.switch_at = 0.0

    def reset_session(self) -> None:
        self.connected_at = 0.0     # wall clock
        self.sent = 0
        self.bytes_out = 0
        self.acks = 0
        self.naks = 0
        self.unanswered = 0         # output commands given up on after _MAX_TRIES
        self.delivered = 0          # output commands ACKed (after any retries)
        self.retries = 0            # resends of an output command
        self.superseded = 0         # replaced by a newer command before delivery
        self.probes_lost = 0        # PINGs/STATUS requests that got no answer
        self.shed = 0               # commands dropped because the queue was full
        self._deliveries: deque[float] = deque(maxlen=_SAMPLES)  # ms, queued → ACKed
        self.last_heard = 0.0       # wall clock of the last frame from the device
        self.last_rtt_ms = 0.0
        self._rtt_sum = 0.0
        self._rtt_n = 0
        self.last_nak_at = 0.0
        self._pending = {}
        self._rtts: deque[float] = deque(maxlen=_SAMPLES)       # ms
        self._waits: deque[float] = deque(maxlen=_SAMPLES)      # ms, queue → wire
        self._writes: deque[float] = deque(maxlen=2000)         # monotonic
        self.last_cmd_at = 0.0      # monotonic: last command that changes the output
        self.status = None          # latest DeviceStatus (fw 2.5+)
        self.status_at = 0.0
        self._status_prev = None
        self._status_prev_at = 0.0
        self.dev_show_max_us = 0.0  # worst since connect — the firmware
        self.dev_gap_max_us = 0.0   # resets its own maxima on every reply
        self.dev_cmd_max_us = 0.0

    # ── Written by the link thread ──

    def on_connect(self) -> None:
        with self._lock:
            self.reset_session()
            self.connected_at = self.last_heard = time.time()

    def on_drop(self, reason: str) -> None:
        with self._lock:
            if self.connected_at:
                self.drops.appendleft({"ended_at": time.time(),
                                       "uptime_s": time.time() - self.connected_at,
                                       "reason": reason})
            self.connected_at = 0.0
            self._pending = {}

    def on_write(self, seq: int, size: int, waited_s: float = 0.0,
                 op: int | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            if op is not None and op not in (Op.PING, Op.STATUS, Op.HELLO):
                self.last_cmd_at = now
            self.sent += 1
            self.bytes_out += size
            if waited_s >= 0:
                self._waits.append(waited_s * 1000)
            self._writes.append(now)
            # Round-trip bookkeeping only; loss is accounted by the link, which
            # knows what was a retry and what was a probe.
            self._pending[seq] = now
            for k in [k for k, t in self._pending.items() if now - t > 5.0]:
                del self._pending[k]

    def on_frame(self, seq: int, op: int, payload: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            self.last_heard = time.time()
            if op in (Op.ACK, Op.STATUS_REPLY):
                if op == Op.ACK:
                    self.acks += 1
                sent_at = self._pending.pop(seq, None)
                if sent_at is not None:
                    self.last_rtt_ms = (now - sent_at) * 1000
                    self._rtt_sum += self.last_rtt_ms
                    self._rtt_n += 1
                    self._rtts.append(self.last_rtt_ms)
                if op == Op.STATUS_REPLY:
                    self._on_status(payload)
            elif op == Op.NAK:
                self.naks += 1
                self.last_nak_at = time.time()
                self._pending.pop(seq, None)
            elif op == Op.LOG:
                text = bytes(payload).decode("ascii", "replace")
                self.logs.appendleft((time.time(), text))
                if text.startswith("switch="):
                    self.switch = text.split("=", 1)[1].split()[0]
                    self.switch_at = time.time()

    def _on_status(self, payload: bytes) -> None:
        # Called with the lock held.
        try:
            st = proto.parse_status(payload)
        except proto.ProtocolError:
            return
        if self.status is not None and st.uptime_ms < self.status.uptime_ms:
            self.reboots += 1           # brown-out, a reset, a watchdog…
            self._status_prev = None
        else:
            self._status_prev, self._status_prev_at = self.status, self.status_at
        self.status, self.status_at = st, time.monotonic()
        self.dev_show_max_us = max(self.dev_show_max_us, st.show_max_us)
        self.dev_gap_max_us = max(self.dev_gap_max_us, st.gap_max_us)
        self.dev_cmd_max_us = max(self.dev_cmd_max_us, st.cmd_max_us)

    def on_delivered(self, queued_at: float) -> None:
        with self._lock:
            self.delivered += 1
            self._deliveries.append((time.monotonic() - queued_at) * 1000)

    def on_retry(self) -> None:
        with self._lock:
            self.retries += 1

    def on_failed(self) -> None:
        with self._lock:
            self.unanswered += 1

    def on_superseded(self) -> None:
        with self._lock:
            self.superseded += 1

    def on_probe_lost(self, n: int = 1) -> None:
        with self._lock:
            self.probes_lost += n

    def on_shed(self) -> None:
        with self._lock:
            self.shed += 1

    # ── Read by the GUI ──

    @staticmethod
    def _pct(values, q: float):
        if not values:
            return None
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]

    def _device(self) -> dict | None:
        st = self.status
        if st is None:
            return None
        out = {k: getattr(st, k) for k in st.__dataclass_fields__}
        out["age_s"] = time.monotonic() - self.status_at
        # Produced after every output command had been written (with margin
        # for the wire and the controller) — so a disagreement with the app's
        # intent means a command was lost, not that one is still in flight.
        out["after_last_command"] = self.status_at > self.last_cmd_at + 0.3
        out["show_max_session_us"] = self.dev_show_max_us
        out["gap_max_session_us"] = self.dev_gap_max_us
        out["cmd_max_session_us"] = self.dev_cmd_max_us
        prev = self._status_prev
        if prev is not None:
            dt = max(1e-3, self.status_at - self._status_prev_at)
            def rate(a, b):             # 16-bit counters wrap
                return ((a - b) & 0xFFFF) / dt
            out["frames_per_s"] = rate(st.frames_ok, prev.frames_ok)
            out["shows_per_s"] = rate(st.shows, prev.shows)
            out["rx_bytes_per_s"] = ((st.rx_bytes - prev.rx_bytes) & 0xFFFFFFFF) / dt
            out["errors_since_last"] = sum(
                (getattr(st, k) - getattr(prev, k)) & 0xFFFF
                for k in ("crc_errors", "decode_errors", "overruns", "unknown_ops"))
        return out

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            mono = time.monotonic()
            recent = [t for t in self._writes if mono - t <= 10.0]
            return {
                "connected_s": now - self.connected_at if self.connected_at else None,
                "last_heard_s": now - self.last_heard if self.connected_at else None,
                "sent": self.sent, "bytes_out": self.bytes_out,
                "acks": self.acks, "naks": self.naks,
                "unanswered": self.unanswered, "shed": self.shed,
                "delivered": self.delivered, "retries": self.retries,
                "superseded": self.superseded, "probes_lost": self.probes_lost,
                "deliver_p50_ms": self._pct(self._deliveries, 0.5),
                "deliver_p95_ms": self._pct(self._deliveries, 0.95),
                "deliver_max_ms": max(self._deliveries) if self._deliveries else None,
                "in_flight": len(self._pending),
                "last_rtt_ms": self.last_rtt_ms if self._rtt_n else None,
                "avg_rtt_ms": (self._rtt_sum / self._rtt_n) if self._rtt_n else None,
                "last_nak_s": now - self.last_nak_at if self.last_nak_at else None,
                "rtt_p50_ms": self._pct(self._rtts, 0.5),
                "rtt_p95_ms": self._pct(self._rtts, 0.95),
                "rtt_max_ms": max(self._rtts) if self._rtts else None,
                "wait_p50_ms": self._pct(self._waits, 0.5),
                "wait_p95_ms": self._pct(self._waits, 0.95),
                "wait_max_ms": max(self._waits) if self._waits else None,
                "cmd_per_s": len(recent) / 10.0,
                "device": self._device(),
                "reboots": self.reboots,
                "attempts": self.attempts, "reconnects": self.reconnects,
                "switch": self.switch,
                "switch_s": now - self.switch_at if self.switch_at else None,
                "logs": list(self.logs), "drops": list(self.drops),
            }


class _Cmd:
    """One output command on its way: resent until the controller ACKs it."""
    __slots__ = ("op", "payload", "key", "queued_at", "seq", "sent_at", "retry_at", "tries")

    def __init__(self, op: int, payload: bytes, key, queued_at: float):
        self.op, self.payload, self.key, self.queued_at = op, payload, key, queued_at
        self.seq = -1
        self.sent_at = 0.0
        self.retry_at = 0.0
        self.tries = 0


def _key(op: int, payload: bytes):
    """
    Which setting a command writes, for latest-wins coalescing — or None for a
    command that must never be merged (SAVE, SET_PIXELS). Only a whole-value
    register qualifies: dropping an older write of it can't change the final
    state, because the newer write lands after everything in between.
    """
    if op == Op.SET_COLOR and payload:
        return ("color", payload[0])          # segment, or ALL_SEGMENTS
    if op in (Op.SET_MODE, Op.SET_BRIGHT, Op.OFF, Op.SET_CAP):
        return (op,)
    return None


def _supersedes(new, old) -> bool:
    """Does a command writing `new` make a waiting one writing `old` pointless?"""
    if new is None or old is None:
        return False
    if new == old:
        return True
    # SET_COLOR to every segment replaces a pending one to any single segment.
    return (new[0] == "color" and old[0] == "color"
            and new[1] == proto.ALL_SEGMENTS)


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
    # A NAK, a LOG line or a connect/drop — the moments the Telemetry panel
    # should redraw at once. Counters in between are read on its slow tick.
    telemetry_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Output commands, in order, coalesced latest-wins; probes separately.
        # Both are touched from the GUI thread (send) and this thread (_pump).
        self._lock = threading.Lock()
        self._cmds: deque[_Cmd] = deque()
        self._probes: deque[int] = deque()
        self._wake = threading.Event()
        self.stats = LinkStats()
        # The 1 Hz PING. Only `tools/led_diag.py --flash-test` turns it off,
        # to see the strips with no redraws at all; STATUS (fw 2.5+) still
        # feeds the controller's watchdog without costing a redraw.
        self.heartbeat = True
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
        """
        Queue a frame. Never blocks. Output commands are delivered (resent
        until ACKed, in order); a newer one for the same setting replaces an
        older one still waiting. PING/STATUS/HELLO are sent once.
        """
        op = int(op)
        with self._lock:
            if op in _RELIABLE:
                key = _key(op, payload)
                if key is not None:
                    before = len(self._cmds)
                    self._cmds = deque(c for c in self._cmds
                                       if not _supersedes(key, c.key))
                    for _ in range(before - len(self._cmds)):
                        self.stats.on_superseded()
                self._cmds.append(_Cmd(op, bytes(payload), key, time.monotonic()))
                while len(self._cmds) > _QUEUE_CAP:
                    self._cmds.popleft()
                    self.stats.on_shed()
            else:
                self._probes.append(op)
        self._wake.set()

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

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
                self.stats.reconnects += 1
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
            self.stats.attempts += 1
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

            # HELLO is resent every 150 ms until INFO comes back. One HELLO
            # was a coin toss: the controller boots into its animated no-host
            # look, and every strip write leaves its UART deaf for ~8 ms of
            # each 16 ms frame, so a single frame is lost about half the time
            # (measured 2026-09-24: 1 HELLO answered ~50%, 5 HELLOs 5/5). Each
            # lost HELLO then cost a whole backoff cycle of up to 8 s.
            deadline = time.monotonic() + 2.0
            next_hello = 0.0
            hello_seq = 0
            while time.monotonic() < deadline:
                if time.monotonic() >= next_hello:
                    port.write(proto.encode_frame(hello_seq, Op.HELLO))
                    port.flush()
                    hello_seq = (hello_seq + 1) & 0xFF
                    next_hello = time.monotonic() + 0.15
                chunk = port.read(256)
                if chunk:
                    for _seq, op, payload in self._reader.feed(chunk):
                        if op == Op.INFO:
                            info = proto.parse_info(payload)
                            self._port = port
                            self._device = info
                            self._port_name = device
                            self.stats.on_connect()
                            self.connected.emit(info, device)
                            self.telemetry_changed.emit()
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
        """
        One loop, never blocking on the port: write probes, keep one output
        command in flight (sent, then resent every `_RETRY_S` until its ACK),
        read whatever has arrived, and otherwise sleep until `send()` wakes it
        or 2 ms pass.
        """
        port = self._port
        assert port is not None
        port.timeout = 0                        # non-blocking reads from here on
        last_ping = last_status = 0.0
        info = self._device
        wants_status = info is not None and (info.fw_major, info.fw_minor) >= proto.STATUS_MIN_FW
        inflight: _Cmd | None = None
        probes_out: dict[int, float] = {}       # seq → monotonic time sent
        with self._lock:                        # a new connection starts clean:
            self._cmds.clear()                  # the service replays the whole
            self._probes.clear()                # intent on `connected`
        while self._running:
            try:
                busy = False
                now = time.monotonic()
                if self.heartbeat and now - last_ping >= proto.HEARTBEAT_S:
                    last_ping = now
                    self._probes.append(Op.PING)
                if wants_status and now - last_status >= _STATUS_EVERY_S:
                    last_status = now
                    self._probes.append(Op.STATUS)

                # Probes: once each.
                with self._lock:
                    probes = list(self._probes)
                    self._probes.clear()
                for op in probes:
                    seq = self._next_seq()
                    data = proto.encode_frame(seq, op)
                    port.write(data)
                    self.stats.on_write(seq, len(data), -1, op)
                    probes_out[seq] = now
                    busy = True

                # Output commands: one in flight, resent until ACKed.
                if inflight is not None:
                    with self._lock:
                        stale = any(_supersedes(c.key, inflight.key) for c in self._cmds)
                    if stale:                   # a newer one for the same setting
                        self.stats.on_superseded()   # is waiting: stop resending
                        inflight = None
                    elif now >= inflight.retry_at:
                        if inflight.tries >= _MAX_TRIES:
                            self.stats.on_failed()
                            inflight = None
                        else:
                            self.stats.on_retry()
                            self._transmit(port, inflight)
                            busy = True
                if inflight is None:
                    with self._lock:
                        inflight = self._cmds.popleft() if self._cmds else None
                    if inflight is not None:
                        self._transmit(port, inflight)
                        busy = True

                waiting = port.in_waiting
                chunk = port.read(waiting) if waiting else b""
                if chunk:
                    busy = True
                    for seq, op, payload in self._reader.feed(chunk):
                        self.stats.on_frame(seq, int(op), bytes(payload))
                        if inflight is not None and seq == inflight.seq:
                            if op == Op.ACK:
                                self.stats.on_delivered(inflight.queued_at)
                                inflight = None
                            elif op == Op.NAK:
                                inflight.retry_at = 0.0     # resend now
                        probes_out.pop(seq, None)
                        if op in (Op.NAK, Op.LOG, Op.STATUS_REPLY):
                            self.telemetry_changed.emit()
                        self.frame.emit(int(op), bytes(payload))

                lost = [k for k, t in probes_out.items() if now - t > _PROBE_TIMEOUT_S]
                for k in lost:
                    del probes_out[k]
                if lost:
                    self.stats.on_probe_lost(len(lost))

                if not busy:
                    # Until something is queued, or the in-flight command is
                    # due a resend, or 2 ms pass for the next read.
                    self._wake.wait(0.002)
                    self._wake.clear()
            except Exception as exc:
                self._close(str(exc))
                return

    def _transmit(self, port, cmd: "_Cmd") -> None:
        cmd.seq = self._next_seq()
        cmd.tries += 1
        data = proto.encode_frame(cmd.seq, cmd.op, cmd.payload)
        port.write(data)
        # The queue wait is measured once: queued → first on the wire.
        waited = (time.monotonic() - cmd.queued_at) if cmd.tries == 1 else -1
        self.stats.on_write(cmd.seq, len(data), waited, cmd.op)
        cmd.sent_at = time.monotonic()
        cmd.retry_at = cmd.sent_at + _RETRY_S + random.random() * _RETRY_JITTER_S

    def _close(self, reason: str) -> None:
        was_up = self._device is not None
        if was_up:
            self.stats.on_drop(reason)
        self._device = None
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None
        if was_up:
            self.disconnected.emit(reason)
            self.telemetry_changed.emit()
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
        self._frames = 0
        self._t0 = time.monotonic()

    def send(self, op: int, payload: bytes = b"") -> None:
        self.sent.append((int(op), bytes(payload)))
        # Behave like a healthy fw 2.5 controller on the counters: written,
        # then answered — an ACK, or for STATUS a plausible telemetry reply.
        self._seq = (self._seq + 1) & 0xFF
        reliable = int(op) in _RELIABLE
        self.stats.on_write(self._seq, 8 + len(payload), 0.0 if reliable else -1, int(op))
        if self._device is None:
            return
        self._frames += 1
        if int(op) == Op.STATUS:
            self.stats.on_frame(self._seq, int(Op.STATUS_REPLY), self._fake_status())
            self.telemetry_changed.emit()
        else:
            self.stats.on_frame(self._seq, int(Op.ACK), b"")
            if reliable:
                self.stats.on_delivered(time.monotonic())

    def _fake_status(self) -> bytes:
        import struct
        up = int((time.monotonic() - self._t0) * 1000)
        shows = int(up / 1000)                 # static look: ~1 redraw a second
        return (bytes([1]) + struct.pack(">IH", up, 428)
                + bytes([1, 0, 160, 0b010])
                + struct.pack(">12H", self._frames & 0xFFFF, 0, 0, 0, 0,
                              shows & 0xFFFF, 0,
                              1300, 1310, 1320, 4200, 16900)   # ticks of 4 µs
                + struct.pack(">I", self._frames * 9))

    def run(self) -> None:
        self._running = True
        self.status.emit("Mock controller (PIT_LEDS_FAKE=1)")
        self._sleep(0.3)
        if not self._running:
            return
        # Reports the current firmware's shape — two segments, fw 2.5 — so
        # the alert path (per-segment colour, the white die) exercises the
        # same branches it will on the real controller.
        half = self._fake_count // 2
        info = proto.DeviceInfo(
            fw_major=2, fw_minor=5, led_count=self._fake_count,
            segments=(proto.Segment(0, half, "centre"),
                      proto.Segment(half, self._fake_count - half, "sides")),
        )
        self._device = info
        self._port_name = "mock"
        self.stats.attempts += 1
        self.stats.on_connect()
        self.connected.emit(info, "mock")
        self.telemetry_changed.emit()
        last = last_status = 0.0
        while self._running:
            if self.heartbeat and time.monotonic() - last >= proto.HEARTBEAT_S:
                last = time.monotonic()
                self.send(Op.PING)
            if time.monotonic() - last_status >= _STATUS_EVERY_S:
                last_status = time.monotonic()
                self.send(Op.STATUS)
            self.msleep(100)
        self._device = None
        self.disconnected.emit("shutting down")


def make_link(parent=None) -> SerialLink:
    """MockLink when PIT_LEDS_FAKE is set, otherwise the real thing."""
    if os.environ.get("PIT_LEDS_FAKE", "").strip() not in ("", "0", "false", "no"):
        count = int(os.environ.get("PIT_LEDS_COUNT", "150") or 150)
        return MockLink(led_count=count, parent=parent)
    return SerialLink(parent=parent)
