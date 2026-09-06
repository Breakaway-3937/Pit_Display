#!/usr/bin/env python3
"""
Self-paced colour check for the pit controller.

Every timed test we tried was misread, because judging a colour means walking
to the pit and the strip had already moved on. This one waits for you: it
sets a colour, then blocks until you press Enter, so the strip holds whatever
it is showing for as long as you need.

    uv run python tools/led_color_check.py

It keeps pinging in the background thread so the firmware watchdog (5 s)
never reverts the strips mid-look.
"""

import threading
import time

import serial
from serial.tools import list_ports

from app.leds import protocol as proto
from app.leds.protocol import Mode, Op

STEPS = [
    ("RED",        (255, 0, 0),     "pure red, no white"),
    ("GREEN",      (0, 255, 0),     "pure green"),
    ("BLUE",       (0, 0, 255),     "pure blue"),
    ("TEAM RED",   (200, 32, 39),   "#C82027 — the one that must look right"),
    ("WHITE",      (255, 255, 255), "all channels"),
]


def find_port() -> str:
    for p in list_ports.comports():
        if p.vid is not None:
            return p.device
    raise SystemExit("no USB serial port found")


def main():
    port = find_port()
    ser = serial.Serial(port, proto.BAUD, timeout=0.1)
    time.sleep(proto.BOOT_DELAY_S + 0.2)
    ser.reset_input_buffer()

    lock = threading.Lock()
    seq = [0]
    stop = threading.Event()

    def send(op, payload=b""):
        with lock:
            seq[0] = (seq[0] + 1) & 0xFF
            ser.write(proto.encode_frame(seq[0], op, payload))
            ser.flush()

    # The watchdog reverts the strips after 5 s of silence, which would undo
    # the colour halfway through you looking at it.
    def heartbeat():
        while not stop.wait(1.0):
            send(Op.PING)

    t = threading.Thread(target=heartbeat, daemon=True)
    t.start()

    print(f"Controller on {port}\n")
    send(Op.SET_BRIGHT, proto.payload_brightness(180))
    send(Op.SET_MODE, proto.payload_mode(Mode.SOLID, 128))

    seen = []
    try:
        for name, rgb, note in STEPS:
            send(Op.SET_COLOR, proto.payload_color(rgb))
            print(f"  Asking for {name:<9} rgb{rgb}  ({note})")
            answer = input("    What colour is actually showing? ").strip()
            seen.append((name, answer))
    finally:
        stop.set()
        ser.close()

    print("\n" + "=" * 46)
    print("  asked        ->  seen")
    print("=" * 46)
    ok = True
    for name, answer in seen:
        match = answer.upper().replace(" ", "") in name.replace(" ", "")
        flag = "ok " if match else "MISMATCH"
        if not match:
            ok = False
        print(f"  {name:<12} ->  {answer:<16} {flag}")
    print("=" * 46)
    print("All correct." if ok else
          "Report the mismatches — they name the channel permutation exactly.")


if __name__ == "__main__":
    main()
