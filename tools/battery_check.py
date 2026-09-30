"""
Check the battery cart mock-up: the BFG decoder against the manual's byte
layouts, the simulated cart, the service and the panel. Exit 0/1.

    uv run tools/battery_check.py

Frames here are built by hand from the BFG FRC User Manual (rev 2026-02-27,
pp. 22-28), not with `bfg.encode_*`, so a decoder bug can't hide behind a
matching encoder bug. No hardware needed; nothing is sent anywhere.
"""

from __future__ import annotations

import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.console import use_utf8  # noqa: E402

use_utf8()

from app.batteries import bfg, sources  # noqa: E402

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        _failures.append(name)
    return ok


def frames_by_hand() -> None:
    print("Decoder, against the manual's layouts")
    bats: dict[int, bfg.Battery] = {}
    # Battery Power 0x0A0B00ss: i32 mA (+ discharge), u16 mV, i16 DoD mAh
    bfg.apply(bats, 0x0A0B0007, struct.pack("<iHh", -6123, 13456, -250))
    b = bats[7]
    check("power: charging current is negative", b.current_a == -6.123 and b.charge_rate_a == 6.123)
    check("power: volts and depth of discharge", b.voltage_v == 13.456 and b.dod_mah == -250)
    # State of Charge 0x0A0B07ss: u16 SoC mAh, u16 effective mAh, u8 state, u24 DoD J
    bfg.apply(bats, 0x0A0B0707, struct.pack("<HHB", 9000, 17000, 3) + (123456).to_bytes(3, "little"))
    check("state of charge", (b.soc_mah, b.effective_mah, b.state, b.dod_j)
          == (9000, 17000, "charging_cv", 123456))
    check("every state code named", all(bfg.STATES.get(c) for c in range(7)))
    # Battery Health 0x0A0B01ss: u32 age s, capacity, u16 cycles
    bfg.apply(bats, 0x0A0B0107, struct.pack("<IHH", 86400 * 200, 16500, 88))
    check("health", (b.age_s, b.capacity_mah, b.cycles) == (86400 * 200, 16500, 88))
    # Heartbeat 0x1F0B01ss: 0, u24 serial, u16 part 0x130, u16 firmware
    bfg.apply(bats, 0x1F0B0107, bytes([0]) + (0xABCDEF).to_bytes(3, "little") + struct.pack("<HH", 0x130, 0x0203))
    check("heartbeat: serial and firmware", (b.serial, b.firmware) == (0xABCDEF, 0x0203))
    bfg.apply(bats, 0x1F0B0109, bytes(4) + struct.pack("<HH", 0x999, 1))
    check("a heartbeat with another part number is ignored", 9 not in bats)
    # Nickname 1 / 2 (0x0A0B0F / 0x0A0B10): 8 + 3 ASCII bytes
    bfg.apply(bats, 0x0A0B0F07, b"CART-A-0")
    bfg.apply(bats, 0x0A0B1007, b"42\0\0\0\0\0\0")
    check("nickname across two frames", b.nickname == "CART-A-042", repr(b.nickname))
    # Match frames
    bfg.apply(bats, 0x0A0B0207, struct.pack("<ii", -500, 142_000))
    bfg.apply(bats, 0x0A0B0307, struct.pack("<HHHH", 9870, 13100, 0, 0))
    bfg.apply(bats, 0x0A0B0607, struct.pack("<II", 38_500, 12_000))
    check("last match: peak, min volts, RMS", (b.match_peak_a, b.match_min_v, b.match_rms_a)
          == (142.0, 9.87, 38.5))
    bfg.apply(bats, 0x0A0B0D07, struct.pack("<HHHB", 10, 90, 12900, 2) + b"\0")
    bfg.apply(bats, 0x0A0B0C07, struct.pack("<HHHH", 0, 0, 12800, 18000))
    check("manufacturer and design capacity", (b.manufacturer, b.design_mah) == ("Interstate", 18000))
    check("health fraction from the BFG's own figures", abs(b.health - 16500 / 18000) < 1e-9)
    check("not a BFG frame: ignored", bfg.apply(bats, 0x02051801, bytes(8)) is None)
    # Identify: Device Configuration 0x1F0B03FF, cmd 0x0D, u24 serial, u16 part, 2 args
    arb, data = bfg.encode_identify(0xABCDEF)
    check("identify frame", arb == 0x1F0B03FF and data == bytes([0x0D, 0xEF, 0xCD, 0xAB, 0x30, 0x01, 0, 0]),
          data.hex())
    bfg.apply(bats, 0x1F0B0107, bytes([0]) + (0x111111).to_bytes(3, "little") + struct.pack("<HH", 0x130, 1))
    check("two BFGs on one device ID are flagged", b.clash)


def simulated_cart() -> None:
    print("\nSimulated cart")
    cart = sources.SimulatedCart()
    cart.start()
    bats: dict[int, bfg.Battery] = {}
    for _ in range(15):
        for arb, data, _t in cart.read():
            bfg.apply(bats, arb, data)
        time.sleep(0.1)
    check("six batteries", len(bats) == 6)
    check("all named and serialed", all(b.nickname and b.serial for b in bats.values()))
    states = {b.state for b in bats.values()}
    check("CC, CV, charged and off-charger all present",
          {"charging_cc", "charging_cv", "charged", "discharging"} <= states, str(states))
    cc = [b for b in bats.values() if b.state == "charging_cc"]
    check("constant-current batteries take ~6 A and have an ETA",
          all(5.5 < b.charge_rate_a < 6.5 and b.minutes_to_full() for b in cc))
    target = next(b for b in bats.values() if b.state == "charged")
    cart.send(*bfg.encode_identify(target.serial))
    check("identify reaches the right simulated battery", target.device_id in cart.identified)


def service_and_panel() -> None:
    print("\nService and panel (offscreen)")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["PIT_BATTERIES_FAKE"] = "1"
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from main import _load_fonts
    _load_fonts(app)
    from app.batteries import batteries, init_batteries
    init_batteries()
    from app.widgets.battery_panel import BatteryPanel
    panel = BatteryPanel()
    panel.resize(1100, 900)
    panel.show()
    loop = QEventLoop()
    QTimer.singleShot(2500, loop.quit)
    loop.exec()
    bats = batteries.batteries()
    check("service decodes the cart", len(bats) == 6)
    nxt = batteries.next_battery()
    check("next battery is the charged one, listed first",
          nxt is not None and nxt.state == "charged" and bats[0] is nxt)
    check("one row per battery", len(panel._rows) == 6)
    check("history is building", all(len(batteries.history(b.device_id)) >= 1 for b in bats))
    batteries.use_simulation(False)
    check("stopping the simulation empties the panel", not batteries.active and not panel._rows)
    check("panel renders", not panel.grab().isNull())
    batteries.shutdown()
    app.processEvents()


def main() -> int:
    frames_by_hand()
    simulated_cart()
    service_and_panel()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print("All battery checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
