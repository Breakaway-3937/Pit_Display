"""
Measure the LED controller link on real hardware. Exit 0/1.

    uv run tools/led_diag.py                 20 s of traffic, then the report
    uv run tools/led_diag.py --seconds 60
    uv run tools/led_diag.py --json          the raw snapshot, for comparing runs

Runs the app's own LED service exactly as the app does — find the controller,
handshake, push the resting look — then sends real commands at irregular
moments (the current brightness again, which changes nothing on the strips)
and reports what `LinkStats` measured: round trip, time queued on this
machine, and, on firmware 2.5+, the controller's own view (strip-write time,
unread-UART gap, command → drawn, receive errors). This is the tool for the
response-rate and latency work: run it, change something, run it again.

**Close the Pit Display app first** — only one program can hold the port.
Fails (exit 1) if no controller answers, anything goes unanswered, or the
controller reports receive errors.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.console import use_utf8  # noqa: E402

use_utf8()
os.environ.setdefault("PIT_CAD_PORT", "0")     # never take :8765 from a running viewer

from PyQt6.QtCore import QCoreApplication  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    app = QCoreApplication(sys.argv)
    from app.config import init_config
    from app.cad_assets import init_cad_assets
    init_config()
    init_cad_assets()
    from app.leds import init_leds, leds
    from app.leds.protocol import Op, payload_brightness
    init_leds()
    leds.start()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    print("Looking for the controller…", flush=True)
    deadline = time.monotonic() + 15
    while not leds.connected and time.monotonic() < deadline:
        pump(0.1)
    if not leds.connected:
        print(f"No controller answered: {leds.status}")
        leds.shutdown()
        return 1
    info = leds.device
    print(f"Connected on {leds.port_name} — {info.describe()}. "
          f"Sending traffic for {args.seconds:.0f} s…", flush=True)

    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        leds._link.send(Op.SET_BRIGHT, payload_brightness(leds.brightness))
        pump(random.uniform(0.05, 0.4))
    pump(2.5)                                   # let the last STATUS land
    t = leds.telemetry()
    leds.shutdown()
    pump(0.3)

    if args.json:
        print(json.dumps(t, indent=2, default=str))
    else:
        def ms(v):
            return "—" if v is None else f"{v:6.1f} ms"
        print()
        print("THIS MACHINE → CONTROLLER")
        print(f"  sent {t['sent']}  acknowledged {t['acks']}  rejected {t['naks']}  "
              f"unanswered {t['unanswered']}  dropped {t['shed']}")
        print(f"  round trip     median {ms(t['rtt_p50_ms'])}   95% {ms(t['rtt_p95_ms'])}   worst {ms(t['rtt_max_ms'])}")
        print(f"  queued here    median {ms(t['wait_p50_ms'])}   95% {ms(t['wait_p95_ms'])}   worst {ms(t['wait_max_ms'])}")
        d = t["device"]
        print("THE CONTROLLER'S OWN VIEW")
        if d is None:
            print(f"  not available on firmware {info.version} (needs 2.5+)")
        else:
            def m(us):
                return f"{us / 1000:6.2f} ms"
            print(f"  up {d['uptime_ms'] / 1000:.0f} s · {d['free_ram']} B RAM free · switch {d['switch']}"
                  f" · mode {d['mode']} · brightness {d['brightness']} · reboots {t['reboots']}")
            if "frames_per_s" in d:
                print(f"  receiving {d['frames_per_s']:.1f} frames/s ({d['rx_bytes_per_s']:.0f} B/s) · "
                      f"redrawing {d['shows_per_s']:.1f}/s")
            print(f"  strip write    last {m(d['show_us'])}   worst {m(d['show_max_session_us'])}")
            print(f"  unread gap     worst {m(d['gap_max_session_us'])}")
            print(f"  cmd → drawn    last {m(d['cmd_us'])}   worst {m(d['cmd_max_session_us'])}")
            print(f"  errors since boot: crc {d['crc_errors']} · broken {d['decode_errors']} · "
                  f"overrun {d['overruns']} · unknown op {d['unknown_ops']} · fallbacks {d['fallbacks']}")

    bad = t["unanswered"] or t["naks"] or (t["device"] and (
        t["device"]["crc_errors"] or t["device"]["decode_errors"] or t["device"]["overruns"]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
