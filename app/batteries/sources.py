"""
Where BFG frames come from: a simulated charging cart, or a real CAN adapter.

Both are the same shape, so the service and the panel can't tell them apart:
`start()`, `stop()`, `read()` → `[(arb_id, data, t)]`, `send(arb_id, data)`,
and `describe()` for the panel's source line.

**Real hardware (`CanSource`)** reads through `python-can` (not a dependency
yet; the panel says so if it's missing). Which interface depends on the OS:

* **CANivore on Linux:** CTRE's `canivore-usb` kernel module makes it a normal
  SocketCAN interface; `PIT_BATTERY_CAN=socketcan:<name>`. Documented by CTRE.
* **CANivore on Windows:** CTRE documents it only through its own tools
  (Tuner's diagnostic server); no raw-frame access for other programs is
  documented. **Unverified: don't plan on it without testing.**
* **Any Windows machine:** a generic USB-CAN adapter (CANable / candleLight,
  `gs_usb`) works through `python-can` directly, e.g.
  `PIT_BATTERY_CAN=gs_usb:0` or `slcan:COM5`.

The bus is 1 Mbit/s like every FRC CAN bus (`PIT_BATTERY_BITRATE` if not).
The BFGs need nothing from a robot: each is powered by its own battery, and
the cart's CAN pigtails daisy-chain to the adapter with 120 Ω at both ends.
"""

from __future__ import annotations

import os
import queue
import random
import threading
import time

from app.batteries import bfg


class SimulatedCart:
    """
    Six batteries on a charging cart, emitting exactly what real BFGs would.
    Runs `SPEED`x faster than life so a demo shows charging move; the panel
    says "simulated" everywhere its numbers appear.
    """

    SPEED = 30.0
    DESIGN_MAH = 18_000

    def __init__(self):
        rnd = random.Random(3937)
        # name, state of charge (fraction), capacity (mAh), cycles, age days
        plan = [("2026-A", 0.22, 17_400, 41, 210), ("2026-B", 0.55, 17_900, 18, 120),
                ("2026-C", 0.83, 16_800, 77, 400), ("2025-D", 0.93, 15_200, 152, 700),
                ("2025-E", 1.00, 16_100, 96, 540), ("2026-F", 0.41, 17_600, 9, 60)]
        self._cells = []
        for dev, (name, frac, cap, cycles, days) in enumerate(plan, start=1):
            self._cells.append({
                "dev": dev, "name": name, "cap": cap, "soc": cap * frac, "cycles": cycles,
                "age": days * 86400, "serial": rnd.randrange(1 << 20, 1 << 23),
                # 2026-F just came off the robot and hasn't been plugged in
                "on_charger": name != "2026-F",
            })
        self._t = time.time()
        self._last_slow = 0.0
        self._out: list[tuple[int, bytes, float]] = []
        self.identified: dict[int, float] = {}

    def describe(self) -> str:
        return f"Simulated charging cart · {len(self._cells)} batteries · {self.SPEED:.0f}× speed"

    @property
    def simulated(self) -> bool:
        return True

    def start(self) -> None:
        self._t = time.time()

    def stop(self) -> None:
        pass

    def send(self, arb_id: int, data: bytes) -> None:
        if arb_id == bfg.CONFIG_ID and data[0] == bfg.CMD_IDENTIFY:
            serial = int.from_bytes(data[1:4], "little")
            for c in self._cells:
                if c["serial"] == serial:
                    self.identified[c["dev"]] = time.time()

    @staticmethod
    def _charger(frac: float) -> tuple[str, float, float]:
        """A typical 6 A charger's profile: (state, amps in, volts)."""
        if frac < 0.80:
            return "charging_cc", 6.0, 12.3 + 2.1 * (frac / 0.80) ** 2
        if frac < 0.97:
            return "charging_cv", max(0.5, 6.0 * (0.97 - frac) / 0.17), 14.4
        if frac < 0.999:
            return "charging_trickle", 0.25, 13.8
        return "charged", 0.02, 13.5

    def read(self) -> list[tuple[int, bytes, float]]:
        now = time.time()
        dt = (now - self._t) * self.SPEED
        self._t = now
        out = []
        slow = now - self._last_slow >= 1.0
        if slow:
            self._last_slow = now
        for c in self._cells:
            frac = c["soc"] / c["cap"]
            if c["on_charger"]:
                state, amps, volts = self._charger(frac)
                c["soc"] = min(c["cap"], c["soc"] + amps * 1000 * dt / 3600)
                current = -amps
            else:
                state, current, volts = "discharging", 0.0, 11.9 + 0.9 * frac
            volts += random.uniform(-0.01, 0.01)
            current += random.uniform(-0.03, 0.03) if abs(current) > 0.1 else 0.0
            dod = int(c["cap"] - c["soc"])
            dev = c["dev"]
            out += [bfg.encode_power(dev, current, volts, dod),
                    bfg.encode_soc(dev, int(c["soc"]), c["cap"], state, int(dod * 3.6 * 12))]
            if slow:
                out += [bfg.encode_heartbeat(dev, c["serial"], 0x0203),
                        bfg.encode_health(dev, int(c["age"]), c["cap"], c["cycles"]),
                        bfg.encode_cycle_energy(dev, self.DESIGN_MAH),
                        *bfg.encode_nickname(dev, c["name"])]
        return [(i, d, now) for i, d in out]


class CanSource:
    """A real bus through python-can, read on a background thread."""

    def __init__(self, spec: str, bitrate: int = 1_000_000):
        interface, _, channel = spec.partition(":")
        self.interface, self.channel, self.bitrate = interface, channel, bitrate
        self._bus = None
        self._q: queue.Queue = queue.Queue(maxsize=20_000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error = ""
        self.dropped = 0

    @property
    def simulated(self) -> bool:
        return False

    def describe(self) -> str:
        where = f"{self.interface} {self.channel}".strip()
        return f"CAN · {where} · {self.bitrate // 1000} kbit/s" + (f" · {self.error}" if self.error else "")

    def start(self) -> None:
        try:
            import can  # optional until this leaves mock-up
        except ImportError:
            self.error = "python-can is not installed"
            return
        try:
            self._bus = can.Bus(interface=self.interface, channel=self.channel or None,
                                bitrate=self.bitrate)
        except Exception as e:
            self.error = f"couldn't open the adapter: {e}"
            return
        self._thread = threading.Thread(target=self._pump, name="bfg-can", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self._bus.recv(timeout=0.2)
            except Exception as e:
                self.error = f"read failed: {e}"
                return
            if msg is None or not msg.is_extended_id:
                continue
            try:
                self._q.put_nowait((msg.arbitration_id, bytes(msg.data), msg.timestamp or time.time()))
            except queue.Full:
                self.dropped += 1

    def read(self) -> list[tuple[int, bytes, float]]:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out

    def send(self, arb_id: int, data: bytes) -> None:
        if self._bus is None:
            return
        import can
        self._bus.send(can.Message(arbitration_id=arb_id, data=data, is_extended_id=True))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass


def from_environment():
    """`PIT_BATTERIES_FAKE=1` → the simulated cart; `PIT_BATTERY_CAN=iface:chan` → a bus."""
    if os.environ.get("PIT_BATTERIES_FAKE") == "1":
        return SimulatedCart()
    spec = os.environ.get("PIT_BATTERY_CAN", "").strip()
    if spec:
        return CanSource(spec, int(os.environ.get("PIT_BATTERY_BITRATE", "1000000")))
    return None
