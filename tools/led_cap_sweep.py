"""
Find the power limit that stops the flicker — by eye, at full amplifier load.

    uv run tools/led_cap_sweep.py              the violet breathe through 7 limits, 12 s each
    uv run tools/led_cap_sweep.py --seconds 20
    uv run tools/led_cap_sweep.py --only 35 0 --seconds 600   one limit, held while you look

Firmware 2.7+ takes a live power limit (OP_SET_CAP: centre %, sides % of the
white work light's own draw). This steps the violet breathe at brightness 180
— the look that flickers — through centre-only limits, then centre + sides,
printing each stage with an estimate of the strips' total current so what you
see can be matched to a number. Watch the centre run; note the first stage that
holds steady. Close the Pit Display app first. The limit is RAM only: the
controller returns to its compiled default on reboot, and the app's own
self-healing is off for this run so it doesn't put white back.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.console import use_utf8  # noqa: E402

use_utf8()
os.environ.setdefault("PIT_CAD_PORT", "0")
from PyQt6.QtCore import QCoreApplication  # noqa: E402

MA_PER_DIE_FULL = 19.8          # back-calculated from the measured 2.1 A white look
VIOLET, BRIGHT = (128, 0, 255), 180


def est_amps(centre_pct: int, sides_pct: int) -> float:
    """Peak-of-breath current: centre 93 px, sides 2 x 76 px (Y-split)."""
    def s8(v, b): return v * b >> 8
    per_px = sum(s8(c, BRIGHT) for c in VIOLET)             # channel units at the peak
    def channel(px, pct, physical):
        raw = per_px * px
        if pct:
            raw = min(raw, px * 160 * pct // 100)
        return raw * physical
    units = channel(93, centre_pct, 1) + channel(76, sides_pct, 2)
    return units / 255 * MA_PER_DIE_FULL / 1000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--only", nargs=2, type=int, metavar=("CENTRE", "SIDES"),
                    help="hold just this limit (percent; 0 = none) for --seconds")
    args = ap.parse_args()
    app = QCoreApplication(sys.argv)
    from app.config import init_config
    from app.cad_assets import init_cad_assets
    init_config(); init_cad_assets()
    from app.leds import init_leds, leds
    from app.leds.protocol import (ALL_SEGMENTS, Mode, Op, payload_brightness,
                                   payload_color, payload_mode)
    init_leds()
    leds._link.telemetry_changed.disconnect(leds._check_in_sync)   # no self-healing here
    leds.start()

    def pump(s):
        end = time.monotonic() + s
        while time.monotonic() < end:
            app.processEvents(); time.sleep(0.005)

    t0 = time.monotonic()
    while not leds.connected and time.monotonic() - t0 < 15:
        pump(0.1)
    if not leds.connected:
        print(f"No controller: {leds.status}"); return 1
    info = leds.device
    if (info.fw_major, info.fw_minor) < (2, 7):
        print(f"Firmware {info.version} has no live limit; flash 2.7."); return 1
    link = leds._link
    link.send(Op.SET_BRIGHT, payload_brightness(BRIGHT))
    link.send(Op.SET_COLOR, payload_color(VIOLET, ALL_SEGMENTS, 0))
    link.send(Op.SET_MODE, payload_mode(Mode.BREATHE, 40))
    print(f"Connected on {leds.port_name} — {info.describe()}. Violet breathe at {BRIGHT}.")
    print(f"Uncapped peak would be ~{est_amps(0, 0):.1f} A. Watch the CENTRE run.\n", flush=True)

    stages = ([tuple(args.only)] if args.only else
              [(100, 0), (75, 0), (50, 0), (35, 0), (20, 0), (50, 50), (35, 35)])
    for n, (c, sd) in enumerate(stages, 1):
        link.send(Op.SET_CAP, bytes([c, sd]))
        sides = f"sides {sd:3d}%" if sd else "sides unlimited"
        print(f"STAGE {n}: centre {c:3d}% · {sides}  →  peak ~{est_amps(c, sd):.1f} A total", flush=True)
        pump(args.seconds)
        d = leds.telemetry()["device"]
        if d:
            print(f"          controller: mode {d['mode']} · centre scale {d['cap_scale']:.0%} at last report", flush=True)
    link.send(Op.SET_CAP, bytes([100, 0]))
    pump(1.0)
    t = leds.telemetry()
    print(f"\nDone; limit back to the default. Link: delivered {t['delivered']}, given up {t['unanswered']}.")
    print("Which stage was the first to hold steady?")
    leds._link.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
