#!/usr/bin/env python3
"""
Drive firmware/pit_probe — the pin/length/colour-order finder.

pit_leds.ino bakes its pins and pixel counts in at compile time, so a pit
nobody has measured cannot be configured from the app: a wrong pin looks
exactly like a dead strip, and a count that is too short looks like "half
the lights are broken". This talks to the probe sketch instead, which
drives every usable pin from one shared buffer, one pin at a time.

    uv run python tools/led_probe.py id             # which pin is which run
    uv run python tools/led_probe.py sweep          # same, one pin at a time
    uv run python tools/led_probe.py ruler 6 180    # how long is that run
    uv run python tools/led_probe.py color 6        # is COLOR_ORDER right
    uv run python tools/led_probe.py solid 6 60 255 0 0
    uv run python tools/led_probe.py flood 1000 255 0 0   # all pins, red
    uv run python tools/led_probe.py off

Flash the probe first:
    arduino-cli upload --fqbn arduino:avr:uno -p <port> firmware/pit_probe
and put pit_leds back when you are done.
"""

import sys
import time

import serial
from serial.tools import list_ports

BAUD = 115200
BOOT_S = 2.0                      # opening the port resets the board
PINS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]


def find_port() -> str:
    """First port with a USB VID. The probe is the only thing plugged in."""
    for p in list_ports.comports():
        if p.vid is not None:
            return p.device
    raise SystemExit("no USB serial port found — is the Arduino plugged in?")


class Probe:
    def __init__(self, port: str | None = None):
        self.port_name = port or find_port()
        self.ser = serial.Serial(self.port_name, BAUD, timeout=0.2)
        time.sleep(BOOT_S)
        self.ser.reset_input_buffer()

    def send(self, line: str, settle: float = 0.35) -> str:
        self.ser.write((line + "\n").encode())
        self.ser.flush()
        time.sleep(settle)
        return self.ser.read(4096).decode("ascii", "replace").strip()

    def close(self):
        try:
            self.send("o", 0.2)
        finally:
            self.ser.close()


def cmd_id(pr: Probe):
    """
    Light every pin at once, each holding a count equal to its pin number.

    Preferred over sweep: nothing is timed, so there is no moment to miss.
    The pattern latches in the pixels and stays lit until something blanks
    it, which means the operator can walk the pit and count at their own
    pace instead of watching a terminal and a strip simultaneously.
    """
    print(f"Probe on {pr.port_name}\n")
    print(pr.send("i", 0.8))
    print("""
Every run in the pit is now lit and WILL STAY LIT. Nothing to watch for.

Walk the pit and, for each section that is lit, count the lit pixels:

    that count IS the Arduino pin its data wire is in.

  2 lit = pin 2      6 lit = pin 6      10 lit = pin 10
  3 lit = pin 3      7 lit = pin 7      11 lit = pin 11
  4 lit = pin 4      8 lit = pin 8      12 lit = pin 12
  5 lit = pin 5      9 lit = pin 9      13 lit = pin 13

The FIRST pixel is RED and counts toward the total. The red end is
pixel 0 — the end the data wire goes into. Note which end of each run
it is on; that is what sets `dir` in the strips[] table.

A section that stays dark is not on any pin 2-13, or is not powered.

Blank it again with:  uv run python tools/led_probe.py off
""")


def cmd_solo(pr: Probe, pin: int, n: int = 1000, rgb=(255, 0, 0)):
    """One pin driven, every other pin blanked and left idle low."""
    r, g, b = rgb
    print(pr.send(f"F {pin} {n} {r} {g} {b}", 0.8))


def cmd_flood(pr: Probe, n: int = 1000, rgb=(255, 0, 0)):
    """
    One colour down every pin, for an arbitrary length.

    Not capped by the sketch's buffer: the driver repeats a single CRGB
    rather than reading an array, so length costs time on the wire and no
    SRAM at all. Use it when a run is longer than MAX_PIXELS, or when you
    just want to see every pixel in the pit at once.
    """
    r, g, b = rgb
    print(pr.send(f"f {n} {r} {g} {b}", 0.8))
    print(f"\nAll pins flooded: {n} px of rgb({r},{g},{b}), held.")
    print("Pixels past the end of a run fall off the chain harmlessly.")


def cmd_sweep(pr: Probe, dwell: float = 5.0):
    """Light each pin in turn so the operator can name the section."""
    print(f"Probe on {pr.port_name}\n")
    print(pr.send("?", 0.5))
    print("\nWatch the pit. Each pin lights for "
          f"{dwell:.0f}s in WHITE, then goes dark.")
    print("Note which pin number lights which physical run.\n")
    for pin in PINS:
        # 60 px is enough to be unmistakable without asking a bench supply
        # for a full-length white run.
        reply = pr.send(f"s {pin} 60 255 255 255", 0.3)
        print(f"  PIN {pin:>2}  →  {reply}", flush=True)
        time.sleep(dwell)
    pr.send("o")
    print("\nAll pins blanked.")


def cmd_ruler(pr: Probe, pin: int, n: int = 180):
    print(pr.send(f"m {pin} {n}", 0.5))
    print(f"\nOn pin {pin}: RED every 50 px, WHITE every 10 px, dim blue between.")
    print("Count the RED marks, then the WHITE ones past the last red:")
    print("    length = 50*reds + 10*whites + leftover dim pixels")
    print("Pixels past the end of the run stay dark — that is where it ends.")


def cmd_color(pr: Probe, pin: int, n: int = 30):
    print(pr.send(f"c {pin} {n}", 0.5))
    print(f"\nOn pin {pin}, the first three pixels should read RED, GREEN, BLUE.")
    print("Any other order means COLOR_ORDER is wrong for this strip:")
    print("    R,B,G → RGB      G,R,B → GRB (current)     B,G,R → BRG")


def cmd_wipe(pr: Probe, pin: int, n: int = 180, ms: int = 40):
    print(f"Wiping one white pixel along pin {pin}. Watch where it stops.\n")
    pr.ser.write(f"w {pin} {n} {ms}\n".encode())
    pr.ser.flush()
    deadline = time.monotonic() + (n * ms / 1000.0) + 5
    while time.monotonic() < deadline:
        line = pr.ser.readline().decode("ascii", "replace").strip()
        if line:
            print(" ", line, flush=True)
            if "done" in line:
                break


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    pr = Probe()
    try:
        if cmd == "rgbwraw":
            pin = int(rest[0]); n = int(rest[1])
            rgb = tuple(int(x) for x in rest[2:5]) if len(rest) >= 5 else (255, 0, 0)
            w = int(rest[5]) if len(rest) > 5 else 0
            print(pr.send(f"W {pin} {n} {rgb[0]} {rgb[1]} {rgb[2]} {w}", 0.8))
        elif cmd == "rgbw":
            on = 0 if (rest and rest[0] in ("0", "off", "false")) else 1
            print(pr.send(f"x {on}", 0.5))
        elif cmd == "solo":
            cmd_solo(pr, int(rest[0]),
                     int(rest[1]) if len(rest) > 1 else 1000,
                     tuple(int(x) for x in rest[2:5]) if len(rest) >= 5 else (255, 0, 0))
        elif cmd in ("flood", "red"):
            n = int(rest[0]) if rest else 1000
            rgb = tuple(int(x) for x in rest[1:4]) if len(rest) >= 4 else (255, 0, 0)
            cmd_flood(pr, n, rgb)
        elif cmd == "id":
            cmd_id(pr)
        elif cmd == "sweep":
            cmd_sweep(pr, float(rest[0]) if rest else 5.0)
        elif cmd == "ruler":
            cmd_ruler(pr, int(rest[0]), int(rest[1]) if len(rest) > 1 else 180)
        elif cmd == "color":
            cmd_color(pr, int(rest[0]))
        elif cmd == "wipe":
            cmd_wipe(pr, int(rest[0]),
                     int(rest[1]) if len(rest) > 1 else 180,
                     int(rest[2]) if len(rest) > 2 else 40)
        elif cmd == "solid":
            pin, n, r, g, b = (int(x) for x in rest[:5])
            print(pr.send(f"s {pin} {n} {r} {g} {b}", 0.4))
            input("\nPress Enter to blank…")
        elif cmd == "off":
            print(pr.send("o", 0.3))
        else:
            print(__doc__)
    finally:
        if cmd not in ("solid",):
            pr.ser.close()
        else:
            pr.close()


if __name__ == "__main__":
    main()
