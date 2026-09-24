#!/usr/bin/env python3
"""
Automated checks for the EQ's live band display (`app/music/analyser.py`).

    uv run tools/eq_check.py

A spectrum display is the easiest thing in this app to fake convincingly, so
the checks that matter are the ones that prove it is measuring: a tone at a
known frequency has to light its own band and not a neighbour, and silence has
to fall to the floor rather than idle prettily.

The other half is the promise that this **cannot stop the music**. libVLC's
audio callbacks replace the output, so the analyser is fed by a second,
output-less decoder of the same file; the player the pit is listening to is
never reconfigured. That is checked by playing something and watching both.

Exit status is 0 when every check passed, 1 otherwise.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PIT_LEDS_FAKE", "1")

from app.console import use_utf8  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    return bool(ok)


def tone_wav(path: str, hz: float, seconds: float = 4.0, amp: float = 0.37):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        frames = bytearray()
        for i in range(int(44100 * seconds)):
            v = int(amp * 32767 * math.sin(2 * math.pi * hz * i / 44100))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


def block(hz: float, n: int = 4410, amp: float = 0.4):
    return [amp * math.sin(2 * math.pi * hz * i / 44100) for i in range(n)]


def main() -> int:
    use_utf8()
    from app.music.analyser import (BAND_HZ, FLOOR_DB, BandAnalyser,
                                    normalise)

    print("\n── It measures what is playing ────────────────────────────", flush=True)
    for hz in (62.5, 250.0, 1000.0, 4000.0, 16000.0):
        a = BandAnalyser(44100, 1)
        for _ in range(5):
            a.feed(block(hz), 4410)
        levels = a.levels()
        loudest = BAND_HZ[levels.index(max(levels))]
        check(f"a {hz:g} Hz tone lights the {hz:g} Hz band",
              loudest == hz, f"loudest was {loudest:g} Hz")

    a = BandAnalyser(44100, 1)
    for _ in range(5):
        a.feed(block(1000.0), 4410)
    levels = a.levels()
    centre = levels[BAND_HZ.index(1000.0)]
    neighbours = max(levels[BAND_HZ.index(500.0)], levels[BAND_HZ.index(2000.0)])
    check("neighbouring bands are clearly below the centre",
          centre - neighbours > 5.0, f"{centre - neighbours:.1f} dB down")

    print("\n── It does not invent anything ────────────────────────────", flush=True)
    a.silence()
    a.feed([0.0] * 4410, 4410)
    check("silence sits at the floor, with no idle animation",
          all(v <= FLOOR_DB + 0.01 for v in a.levels()),
          f"max {max(a.levels()):.1f} dBFS")
    fresh = BandAnalyser(44100, 1)
    check("a analyser that has never been fed reads the floor",
          all(v <= FLOOR_DB + 0.01 for v in fresh.levels()))
    check("normalise maps the floor to 0 and full scale to 1",
          normalise(FLOOR_DB) == 0.0 and normalise(0.0) == 1.0)

    print("\n── Levels fall, they do not stick ─────────────────────────", flush=True)
    a = BandAnalyser(44100, 1)
    for _ in range(5):
        a.feed(block(1000.0), 4410)
    loud = max(a.levels())
    for _ in range(6):
        a.feed([0.0] * 4410, 4410)
    quiet = max(a.levels())
    check("a band decays once the sound stops", quiet < loud - 10.0,
          f"{loud:.1f} -> {quiet:.1f} dBFS")

    print("\n── The cost ───────────────────────────────────────────────", flush=True)
    samples = block(1000.0)
    a = BandAnalyser(44100, 1)
    a.feed(samples, 4410)
    t0 = time.perf_counter()
    rounds = 10
    for _ in range(rounds):
        a.feed(samples, 4410)
    per = (time.perf_counter() - t0) / rounds
    load = per / (4410 / 44100.0) * 100
    check("analysing costs under 15% of one core", load < 15.0,
          f"{load:.1f}% of a core for ten bands")

    print("\n── It cannot stop the music ───────────────────────────────", flush=True)
    try:
        from app.music.engine import VLCEngine
        engine = VLCEngine()
    except Exception as e:
        check("libVLC is available to test the shadow decoder", False, str(e))
        return report()

    path = os.path.join(tempfile.gettempdir(), "eq_check_tone.wav")
    tone_wav(path, 1000.0)
    analyser = BandAnalyser()
    engine.set_volume(0)
    engine.set_analyser(analyser)
    engine.play(path)
    deadline = time.time() + 6
    while time.time() - deadline < 0 and not analyser.active:
        time.sleep(0.1)

    check("the player the pit hears is still playing", engine.is_playing())
    check("the analyser was fed by the shadow decoder", analyser.active,
          f"max {max(analyser.levels()):.1f} dBFS")
    levels = analyser.levels()
    loudest = BAND_HZ[levels.index(max(levels))]
    check("and it measured the right band", loudest == 1000.0,
          f"{loudest:g} Hz")

    engine.set_analyser(None)
    time.sleep(0.4)
    check("turning it off leaves the music playing", engine.is_playing())
    check("and the levels fall back to the floor",
          all(v <= FLOOR_DB + 0.01 for v in analyser.levels()))
    engine.stop()
    engine.release()
    os.unlink(path)

    print("\n── The display reads what it measured ─────────────────────", flush=True)
    from app.widgets.eq_field import _LEVEL_RAMP, _level_colour
    from app import brand
    stops = [st for st, _ in _LEVEL_RAMP]
    check("the level ramp runs floor to ceiling in order",
          stops == sorted(stops) and stops[0] == 0.0 and stops[-1] == 1.0,
          f"{len(stops)} stops")
    check("quiet is cool and loud is hot",
          _level_colour(0.0) == brand.HARBOR
          and _level_colour(1.0) == brand.STATUS_FAULT,
          f"{_level_colour(0.0)} -> {_level_colour(1.0)}")
    # Red is the brand's exceptional state and is spent on running out of
    # headroom, not on ordinary loud.
    check("red is the top of the ramp only",
          _level_colour(0.80) != brand.STATUS_FAULT,
          f"at 80% the bar is {_level_colour(0.80)}")

    print("\n── It keeps up with the audio ─────────────────────────────", flush=True)
    # The shadow decoder delivers a 50 ms block roughly every 42 ms. A display
    # that repaints more slowly than that can leave a block unshown, which is
    # felt as lag and was the original complaint.
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["eq-check"])
    from app.widgets.eq_field import EQField
    probe = EQField()
    interval = probe._tick.interval()
    check("the display repaints faster than data arrives",
          interval <= 42, f"{interval} ms repaint vs ~42 ms per block")
    # ...but not so much faster that it burns a core drawing the same frame.
    check("and not wastefully faster than that", interval >= 25,
          f"{interval} ms")
    from app.music.analyser import DECAY_PER_S, FLOOR_DB
    fall = abs(FLOOR_DB + 8) / DECAY_PER_S
    check("a bar falls from -8 dBFS to the floor in under 0.6 s",
          fall < 0.6, f"{fall:.2f} s at {DECAY_PER_S:.0f} dB/s")

    print("\n── The field only works when it is working ────────────────", flush=True)
    field = EQField()
    field.resize(760, 246)
    check("no repaint timer with the display off", not field._tick.isActive())
    field.set_analyser(BandAnalyser())
    check("the timer runs only once an analyser is attached",
          field._tick.isActive(), f"{field._tick.interval()} ms")
    field.set_analyser(None)
    check("and stops again when it is turned off", not field._tick.isActive())
    _ = app
    return report()


def report() -> int:
    print()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    if failed:
        print("\nFAILED:", flush=True)
        for name in failed:
            print(f"  - {name}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    status = main()
    sys.stdout.flush()
    os._exit(status)
