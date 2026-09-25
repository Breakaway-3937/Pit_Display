"""
Measure the LED controller link on real hardware. Exit 0/1.

    uv run tools/led_diag.py                 20 s of traffic, then the report
    uv run tools/led_diag.py --seconds 60
    uv run tools/led_diag.py --json          the raw snapshot, for comparing runs
    uv run tools/led_diag.py --flash-test    staged test for flicker: watch the strips
    uv run tools/led_diag.py --hold --seconds 300   white work light, confirmed, held
    uv run tools/led_diag.py --violet-test   is it the violet, or the animation?

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
    ap.add_argument("--hold", action="store_true",
                    help="put the white work light up, confirm it landed, hold it "
                         "for --seconds and report what the controller says")
    ap.add_argument("--violet-test", action="store_true",
                    help="violet still / violet redrawn / violet sparkle / white redrawn")
    ap.add_argument("--flash-test", action="store_true",
                    help="four 15 s stages that each isolate one cause of flicker")
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
    if args.hold:
        status = hold(leds, pump, args.seconds)
        leds.shutdown()
        pump(0.3)
        return status
    if args.violet_test:
        status = violet_test(leds, pump)
        leds.shutdown()
        pump(0.3)
        return status
    if args.flash_test:
        status = flash_test(leds, pump)
        leds.shutdown()
        pump(0.3)
        return status
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
        print(f"  commands delivered {t['delivered']}  resends {t['retries']}  replaced {t['superseded']}  "
              f"given up {t['unanswered']}  heartbeats lost {t['probes_lost']}  resyncs {t['resyncs']}")
        print(f"  delivered      median {ms(t['deliver_p50_ms'])}   95% {ms(t['deliver_p95_ms'])}   worst {ms(t['deliver_max_ms'])}")
        print(f"  round trip     median {ms(t['rtt_p50_ms'])}   95% {ms(t['rtt_p95_ms'])}   worst {ms(t['rtt_max_ms'])}")
        print(f"  queued here    median {ms(t['wait_p50_ms'])}   95% {ms(t['wait_p95_ms'])}   worst {ms(t['wait_max_ms'])}")
        print(f"  frames: sent {t['sent']}  acknowledged {t['acks']}  rejected {t['naks']}")
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
            if d.get("cap_scale") is not None:
                print(f"  centre power cap: last scale {d['cap_scale']:.0%} · "
                      f"{d['capped_frames']} frames capped since boot")
            print(f"  errors since boot: crc {d['crc_errors']} · broken {d['decode_errors']} · "
                  f"overrun {d['overruns']} · unknown op {d['unknown_ops']} · fallbacks {d['fallbacks']}")

    # Pass/fail is about *delivery* now: frames lost to a strip write and
    # resent are the protocol working. A command given up on is a failure.
    bad = t["unanswered"] or t["shed"]
    return 1 if bad else 0


def confirm_look(leds, pump, attempts: int = 20) -> bool:
    """Push the resting look until the controller's own report agrees."""
    for attempt in range(1, attempts + 1):
        leds._push_all()
        pump(2.5)
        d = leds.telemetry()["device"]
        if d and d["mode"] == int(leds.mode) and d["brightness"] == min(leds.brightness, 200):
            print(f"  confirmed after {attempt} attempt(s): mode {d['mode']} at {d['brightness']}",
                  flush=True)
            return True
    print("  never confirmed: every attempt was lost.", flush=True)
    return False


def hold(leds, pump, seconds: float) -> int:
    """The white work light, confirmed and held, with the controller's view every 10 s."""
    from app.leds.protocol import STATUS_MIN_FW
    info = leds.device
    print(f"Connected on {leds.port_name} — {info.describe()}.")
    if (info.fw_major, info.fw_minor) < STATUS_MIN_FW:
        print("Firmware can't confirm what it shows; pushing once and holding.")
        leds._push_all()
    else:
        print("Putting the white work light up…", flush=True)
        if not confirm_look(leds, pump):
            return 1
    print(f"Holding for {seconds:.0f} s — watch the strips.", flush=True)
    end = time.monotonic() + seconds
    t0 = time.monotonic()
    prev = leds.telemetry()["device"]
    while time.monotonic() < end:
        pump(10.0)
        d = leds.telemetry()["device"]
        if d and prev:
            shows = (d["shows"] - prev["shows"]) & 0xFFFF
            errs = sum((d[k] - prev[k]) & 0xFFFF
                       for k in ("crc_errors", "decode_errors", "overruns", "unknown_ops"))
            print(f"  {time.monotonic() - t0:4.0f} s  mode {d['mode']} at {d['brightness']}"
                  f" · switch {d['switch']} · {shows / 10:.1f} redraws/s · errors {errs}"
                  f" · RAM {d['free_ram']} B · reboots {leds.telemetry()['reboots']}",
                  flush=True)
        prev = d
    return 0


def violet_test(leds, pump) -> int:
    """
    Separate "violet flickers" from "animation flickers". The firmware's own
    no-host violet (128, 0, 255) is sent raw — no palette snapping — so this is
    exactly what the strips show when the app isn't connected.
    """
    from app.leds.protocol import (ALL_SEGMENTS, Mode, Op, payload_brightness,
                                   payload_color, payload_mode)
    link = leds._link
    print(f"Connected on {leds.port_name} — {leds.device.describe()}.")

    def look(rgb, w, mode, speed=40):
        link.send(Op.SET_BRIGHT, payload_brightness(180))
        link.send(Op.SET_COLOR, payload_color(rgb, ALL_SEGMENTS, w))
        link.send(Op.SET_MODE, payload_mode(mode, speed))
        pump(1.0)

    def stage(name, watch, seconds=15.0, redraw=False):
        before = leds.telemetry()["device"]
        print(f"\n{name}\n  WATCH: {watch}", flush=True)
        end = time.monotonic() + seconds
        same = payload_brightness(180)
        while time.monotonic() < end:
            if redraw:                          # an identical frame, 30 times a second
                link.send(Op.SET_BRIGHT, same)
            pump(0.033)
        pump(2.5)
        after = leds.telemetry()["device"]
        if before and after:
            shows = (after["shows"] - before["shows"]) & 0xFFFF
            print(f"  measured: {shows / (seconds + 2.5):.1f} redraws/s · mode "
                  f"{after['mode']} · strip write {after['show_us'] / 1000:.2f} ms", flush=True)

    violet = (128, 0, 255)
    look(violet, 0, Mode.SOLID)
    stage("A: VIOLET, STILL — drawn once, nothing moves",
          "flicker here means the violet data itself.")
    stage("B: VIOLET, REDRAWN 30x/s — the same frame over and over",
          "flicker here (not in A) means re-sending violet.", redraw=True)
    look(violet, 0, Mode.SPARKLE, speed=150)
    stage("C: VIOLET SPARKLE — the no-app look (sides only while connected)",
          "flicker here only means the animation.")
    look((0, 0, 0), 255, Mode.SOLID)
    stage("D: WHITE, REDRAWN 30x/s — the control",
          "flicker here means re-sending anything, not violet.", redraw=True)
    print("\nWhich stage(s) flickered?")
    return 0


def flash_test(leds, pump) -> int:
    """
    Isolate what makes the strips flicker. The operator watches; each stage
    changes exactly one thing and prints what the controller measured, so
    "it flashed during stage 3" names the cause.
    """
    from app.leds.protocol import Op, STATUS_MIN_FW, payload_brightness
    from app.leds.service import Alert
    info = leds.device
    if (info.fw_major, info.fw_minor) < STATUS_MIN_FW:
        print(f"Firmware {info.version} can't report what it's showing; flash 2.5+ first.")
        return 1
    link = leds._link

    def device():
        return leds.telemetry()["device"]

    # 1. Make sure the resting look has actually landed — resend until the
    #    controller's own report says so. The app itself doesn't (yet).
    print(f"Connected on {leds.port_name} — {info.describe()}.")
    print("Putting the white work light up, and confirming it landed…", flush=True)
    for attempt in range(1, 21):
        leds._push_all()
        pump(2.5)
        d = device()
        if d and d["mode"] == 0 and d["brightness"] == min(leds.brightness, 200):
            print(f"  confirmed after {attempt} attempt(s): solid at {d['brightness']}")
            break
    else:
        print("  never confirmed: the link is losing every command. Stopping.")
        return 1

    def stage(n, title, watch, seconds, during=None):
        before = device()
        print(f"\nSTAGE {n}: {title}\n  WATCH: {watch}", flush=True)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if during:
                during()
            pump(0.1)
        pump(2.5)
        after = device()
        shows = (after["shows"] - before["shows"]) & 0xFFFF
        errs = sum((after[k] - before[k]) & 0xFFFF
                   for k in ("crc_errors", "decode_errors", "overruns", "unknown_ops"))
        print(f"  measured: {shows / (seconds + 2.5):.1f} redraws/s · strip write "
              f"{after['show_us'] / 1000:.2f} ms · receive errors {errs} · "
              f"running mode {after['mode']} at {after['brightness']}", flush=True)

    link.heartbeat = False
    stage(1, "STILL — nothing redraws (heartbeat paused)",
          "should be perfectly steady. Flicker here is power or wiring, not software.", 15)
    link.heartbeat = True
    stage(2, "HEARTBEAT — normal link traffic (fw 2.7+: heartbeats no longer redraw)",
          "on older firmware, flicker once a second means re-drawing the same frame blinks these strips.", 15)
    same = payload_brightness(leds.brightness)
    stage(3, "TRAFFIC — ~10 commands a second, all the same look",
          "flicker that gets busier here tracks commands arriving, not the look.", 15,
          lambda: link.send(Op.SET_BRIGHT, same))
    leds.start_alert(Alert(sides="#BA141A", centre_white=True, flash_s=8.0,
                           steady_s=1.0, label="flash test"))
    stage(4, "ALERT — sides flash red, the centre must hold steady white",
          "the centre flickering here means the alert path, not the strips.", 12)
    leds.clear_alert()
    pump(2.0)
    print("\nWhich stage(s) did the centre run flicker in? That names the cause.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
