"""
The battery cart: live charge state of every battery with a BFG on it.

**Mock-up (2026-09-30).** The decoder (`bfg.py`) follows PWF's published
CAN spec; the transport (`sources.py`) is either a simulated cart or a real
CAN adapter through `python-can`, which isn't a dependency yet. Nothing here
talks to a robot: the BFGs on the cart, a USB-CAN adapter (a CANivore on
Linux, or a generic adapter on Windows), and this machine.

Off unless something is configured: `PIT_BATTERIES_FAKE=1` for the simulated
cart, `PIT_BATTERY_CAN=<interface>:<channel>` for a real bus, or the panel's
"Show the simulated cart" button. With none, no timer runs.
"""

from __future__ import annotations

import time
from collections import deque

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.batteries import bfg, sources
from app.lazy_proxy import LazyProxy

_POLL_MS = 100
_EMIT_EVERY_S = 0.25
HISTORY_S = 600            # the sparkline: ten minutes at one point a second
OFFLINE_AFTER_S = 5.0      # power frames come every 10 ms; 5 s of silence is gone


class _BatteryService(QObject):
    changed = pyqtSignal()           # batteries moved (at most 4x a second)
    source_changed = pyqtSignal()    # started, stopped, failed

    def __init__(self):
        super().__init__()
        self._source = None
        self._batteries: dict[int, bfg.Battery] = {}
        self._history: dict[int, deque] = {}
        self._last_sample = 0.0
        self._last_emit = 0.0
        self._dirty = False
        self.identify_sent: dict[int, float] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        source = sources.from_environment()
        if source is not None:
            self._use(source)

    # ── source ────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._source is not None

    @property
    def simulated(self) -> bool:
        return bool(self._source is not None and self._source.simulated)

    @property
    def error(self) -> str:
        return getattr(self._source, "error", "") if self._source else ""

    def describe(self) -> str:
        return self._source.describe() if self._source else ""

    def _use(self, source) -> None:
        self.stop()
        self._source = source
        self._batteries.clear()
        self._history.clear()
        source.start()
        self._timer.start()
        self.source_changed.emit()
        self.changed.emit()

    def use_simulation(self, on: bool) -> None:
        if on:
            self._use(sources.SimulatedCart())
        elif self.simulated:
            self.stop()
            self._batteries.clear()
            self._history.clear()
            self.source_changed.emit()
            self.changed.emit()

    def stop(self) -> None:
        self._timer.stop()
        if self._source is not None:
            self._source.stop()
            self._source = None

    # ── batteries ─────────────────────────────────────────────────────────

    def batteries(self) -> list[bfg.Battery]:
        """Every battery heard from, the next one to use first, then by name."""
        best = self.next_battery()
        return sorted(self._batteries.values(),
                      key=lambda b: (b is not best, b.name.lower()))

    def online(self, b: bfg.Battery) -> bool:
        return b.age_since(time.time()) < OFFLINE_AFTER_S

    def history(self, device_id: int) -> list[tuple[float, float, float]]:
        """(t, amps in (+ = charging), volts), oldest first, one a second."""
        return list(self._history.get(device_id, ()))

    def next_battery(self) -> bfg.Battery | None:
        """
        PWF's own advice (manual, "Charge Indicator"): the most charge
        remaining, then the most effective capacity. Only a battery that's
        online and not mid-discharge qualifies.
        """
        ready = [b for b in self._batteries.values()
                 if self.online(b) and b.state != "discharging" and b.soc_mah is not None]
        if not ready:
            return None
        return max(ready, key=lambda b: (b.state == "charged", b.soc_mah or 0, b.effective_mah or 0))

    def identify(self, device_id: int) -> None:
        """Ask one BFG to show its identification page (the only command sent)."""
        b = self._batteries.get(device_id)
        if b is None or b.serial is None or self._source is None:
            return
        self._source.send(*bfg.encode_identify(b.serial))
        self.identify_sent[device_id] = time.time()
        self.changed.emit()

    # ── the poll ──────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if self._source is None:
            return
        frames = self._source.read()
        for arb_id, data, t in frames:
            if bfg.apply(self._batteries, arb_id, data, now=time.time()) is not None:
                self._dirty = True
        now = time.time()
        if now - self._last_sample >= 1.0:
            self._last_sample = now
            for dev, b in self._batteries.items():
                if self.online(b) and b.current_a is not None and b.voltage_v is not None:
                    h = self._history.setdefault(dev, deque(maxlen=HISTORY_S))
                    h.append((now, -b.current_a, b.voltage_v))
            self._dirty = True            # ages and "offline" move with time
        if self._dirty and now - self._last_emit >= _EMIT_EVERY_S:
            self._dirty = False
            self._last_emit = now
            self.changed.emit()

    def shutdown(self) -> None:
        self.stop()


batteries: _BatteryService = LazyProxy("batteries", "init_batteries")  # type: ignore[assignment]


def init_batteries() -> _BatteryService:
    """Before the control screen (its Batteries panel subscribes)."""
    real = _BatteryService()
    batteries._install(real)
    return real
