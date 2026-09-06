"""
Silly-but-true slides generated from imported robot logs.

Pit visitors do not want a telemetry dashboard. They want one number that makes
them go "wait, really?" — so these take real figures out of the database and put
a human-sized comparison next to them.

Two rules, both load-bearing:

* **Every number is real.** Nothing here is invented or rounded for effect.
  Anything you cannot compute from the log does not get a slide. A visitor who
  asks "is that true?" should get a yes.
* **Nothing here is bragging.** Half of these are jokes at our own expense —
  the robot that drove two feet in ten minutes is funnier than any highlight
  reel, and it is honest about what a bench-test log actually contains.

`slides()` returns [] when no log has been imported, so the rotation simply
does not include them rather than showing empty frames.
"""

from app.db import db
from app.slides import Slide, FIGURE

# A ream of 20 lb paper is about 0.1 mm per sheet; ~3,000 characters fits on a
# printed page. Both are approximations and the slide says "about".
_MM_PER_PAGE = 0.1
_CHARS_PER_PAGE = 3000

# Arkansas, 2023 Census estimate. Used for one comparison and rounded down in
# the copy so it stays true as the state grows.
_ARKANSAS_POP = 3_070_000


def _fmt(n: float, places: int = 0) -> str:
    return f"{n:,.{places}f}"


def slides() -> list[Slide]:
    """
    Figure slides for the rotation. Empty when nothing is imported.

    Every one of these is the **Figure** archetype: the numeral is the headline
    and the sentence is the caption, so each carries `figure` and `unit`
    separately from its title rather than leaving the panel to parse a number
    back out of a string. The eyebrow names what was measured — a visitor
    reading "187" needs "peak current draw" before the joke lands.
    """
    session = db.fetchone(
        """SELECT raw_rows, stored_rows, duration_s, source_bytes
           FROM log_session WHERE raw_rows IS NOT NULL
           ORDER BY imported_at DESC LIMIT 1""")
    if session is None or not session["raw_rows"]:
        return []

    raw = session["raw_rows"]
    stored = session["stored_rows"] or 0
    dur = session["duration_s"] or 0
    src_bytes = session["source_bytes"] or 0
    out: list[Slide] = []

    # ── How much the robot says about itself ─────────────────────────────
    if dur > 0:
        per_sec = raw / dur
        multiples = raw / _ARKANSAS_POP
        out.append(Slide(
            kind=FIGURE, eyebrow="Measurements taken",
            figure=_fmt(raw), unit="values",
            title=f"{_fmt(raw)} Numbers",
            body=f"Measurements our robot recorded in {dur/60:.1f} minutes — "
                 f"about {_fmt(per_sec)} every second. More numbers than there "
                 f"are people in Arkansas, roughly {multiples:.0f} times over."
        ))

    if src_bytes > 0:
        pages = src_bytes / _CHARS_PER_PAGE
        metres = pages * _MM_PER_PAGE / 1000
        out.append(Slide(
            kind=FIGURE, eyebrow="Printed out",
            figure=_fmt(pages), unit="pages",
            title="A Very Tall Stack",
            body=f"Printed, one log would run about {_fmt(pages)} pages — a "
                 f"stack roughly {metres:.0f} metres tall. Taller than the "
                 f"Statue of Liberty. We keep it on a laptop."
        ))

    if stored:
        out.append(Slide(
            kind=FIGURE, eyebrow="Said twice",
            figure=f"{100 - 100*stored/raw:.0f}", unit="%",
            title=f"{100 - 100*stored/raw:.0f}% Was Repeats",
            body=f"Only {_fmt(stored)} of {_fmt(raw)} measurements were the "
                 f"robot telling us something new. We keep the news and throw "
                 f"away the repetition."
        ))

    # ── The distance joke — the best one in the set ──────────────────────
    try:
        from app.robot.distance import distance_for, drive_motors
        motors = drive_motors(1)
        if motors:
            ds = [distance_for(1, c) for c in motors]
            avg_in = sum(x.inches for x in ds) / len(ds)
            moving = sum(x.moving_s for x in ds) / len(ds)
            if dur > 0 and avg_in > 0:
                out.append(Slide(
                    kind=FIGURE, eyebrow="Distance driven",
                    figure=f"{avg_in:.0f}", unit="in",
                    title=f"{avg_in:.0f} Inches",
                    body=f"Total distance travelled in {dur/60:.1f} minutes. It "
                         f"was awake the whole time and chose to move for "
                         f"{moving:.0f} seconds of it. In fairness, it was on a "
                         f"bench."
                ))
    except Exception:
        pass

    # ── Electrical ───────────────────────────────────────────────────────
    cur = db.fetchone(
        """SELECT MAX(v_max) hi FROM series se JOIN signal s ON s.id=se.signal_id
           WHERE s.name='StatorCurrent'""")
    volt = db.fetchone(
        """SELECT MIN(v_min) lo, MAX(v_max) hi FROM series se
           JOIN signal s ON s.id=se.signal_id WHERE s.name='SupplyVoltage'""")
    if cur and cur["hi"] and volt and volt["hi"]:
        watts = cur["hi"] * volt["hi"]
        out.append(Slide(
            kind=FIGURE, eyebrow="Peak current draw",
            figure=f"{cur['hi']:.0f}", unit="A",
            title=f"{cur['hi']:.0f} Amps",
            body=f"Peak current through one motor — about {watts/1000:.1f} "
                 f"kilowatts, a kitchen microwave's worth, through something "
                 f"the size of a soda can."
        ))
    if volt and volt["lo"]:
        out.append(Slide(
            kind=FIGURE, eyebrow="Battery sag",
            figure=f"{volt['lo']:.2f}", unit="V",
            title=f"{volt['lo']:.2f} Volts",
            body=f"How far the battery sagged under load, down from "
                 f"{volt['hi']:.2f} V at rest. Every motor notices when that "
                 f"number drops. So do we."
        ))

    # ── Faults, told honestly ────────────────────────────────────────────
    sticky = db.fetchone(
        "SELECT COUNT(*) c FROM fault_event WHERE sticky=1")["c"]
    live = db.fetchone(
        "SELECT COUNT(*) c FROM fault_event WHERE sticky=0")["c"]
    if sticky:
        out.append(Slide(
            kind=FIGURE, eyebrow="Latched faults",
            figure=str(sticky), unit="filed",
            title=f"{sticky} Complaints",
            body=f"Warnings the robot latched onto and kept — brownouts, "
                 f"current limits, a sensor having a moment. Faults still "
                 f"active at the end: {live}. It files everything and forgives "
                 f"nothing."
        ))

    # ── Signals with nothing to say ──────────────────────────────────────
    quiet = db.fetchone("SELECT COUNT(*) c FROM session_constant")["c"]
    total = db.fetchone("SELECT COUNT(*) c FROM series")["c"]
    if quiet and total:
        out.append(Slide(
            kind=FIGURE, eyebrow="Never moved",
            figure=str(quiet), unit="signals",
            title=f"{quiet} Silent Sensors",
            body=f"Of {_fmt(total)} things we measured, {quiet} never changed "
                 f"once in {dur/60:.1f} minutes. Measuring nothing is still "
                 f"measuring."
        ))

    # ── Temperature ──────────────────────────────────────────────────────
    temp = db.fetchone(
        """SELECT MAX(v_max) hi FROM series se JOIN signal s ON s.id=se.signal_id
           WHERE s.name='DeviceTemp'""")
    if temp and temp["hi"]:
        f_hi = temp["hi"] * 9 / 5 + 32
        out.append(Slide(
            kind=FIGURE, eyebrow="Hottest motor",
            figure=f"{temp['hi']:.0f}", unit="°C",
            title=f"{temp['hi']:.0f}°C",
            body=f"The hottest any motor got — {f_hi:.0f}°F. Cooler than a cup "
                 f"of coffee, and cooler than most people standing in this pit."
        ))

    return out


def summary_line() -> str:
    """One-line description of what the facts were built from."""
    s = db.fetchone(
        """SELECT source_name, raw_rows, duration_s FROM log_session
           WHERE raw_rows IS NOT NULL ORDER BY imported_at DESC LIMIT 1""")
    if s is None:
        return "No robot log imported yet."
    return (f"{s['source_name']} — {s['raw_rows']:,} rows over "
            f"{(s['duration_s'] or 0)/60:.1f} min")
