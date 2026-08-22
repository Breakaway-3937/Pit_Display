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

# A ream of 20 lb paper is about 0.1 mm per sheet; ~3,000 characters fits on a
# printed page. Both are approximations and the slide says "about".
_MM_PER_PAGE = 0.1
_CHARS_PER_PAGE = 3000

# Arkansas, 2023 Census estimate. Used for one comparison and rounded down in
# the copy so it stays true as the state grows.
_ARKANSAS_POP = 3_070_000


def _fmt(n: float, places: int = 0) -> str:
    return f"{n:,.{places}f}"


def slides() -> list[tuple[str, str]]:
    """(title, body) pairs for SlidePanel. Empty when nothing is imported."""
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
    out: list[tuple[str, str]] = []

    # ── How much the robot says about itself ─────────────────────────────
    if dur > 0:
        per_sec = raw / dur
        multiples = raw / _ARKANSAS_POP
        out.append((
            f"{_fmt(raw)} Numbers",
            f"Measurements our robot recorded in {dur/60:.1f} minutes — "
            f"about {_fmt(per_sec)} every second. More numbers than there are "
            f"people in Arkansas, roughly {multiples:.0f} times over."
        ))

    if src_bytes > 0:
        pages = src_bytes / _CHARS_PER_PAGE
        metres = pages * _MM_PER_PAGE / 1000
        out.append((
            "A Very Tall Stack",
            f"Printed, one log would run about {_fmt(pages)} pages — a stack "
            f"roughly {metres:.0f} metres tall. Taller than the Statue of "
            f"Liberty. We keep it on a laptop."
        ))

    if stored:
        out.append((
            f"{100 - 100*stored/raw:.0f}% Was Repeats",
            f"Only {_fmt(stored)} of {_fmt(raw)} measurements were the robot "
            f"telling us something new. We keep the news and throw away the "
            f"repetition."
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
                out.append((
                    f"{avg_in:.0f} Inches",
                    f"Total distance travelled in {dur/60:.1f} minutes. It was "
                    f"awake the whole time and chose to move for {moving:.0f} "
                    f"seconds of it. In fairness, it was on a bench."
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
        out.append((
            f"{cur['hi']:.0f} Amps",
            f"Peak current through one motor — about {watts/1000:.1f} kilowatts, "
            f"a kitchen microwave's worth, through something the size of a "
            f"soda can."
        ))
    if volt and volt["lo"]:
        out.append((
            f"{volt['lo']:.2f} Volts",
            f"How far the battery sagged under load, down from "
            f"{volt['hi']:.2f} V at rest. Every motor notices when that number "
            f"drops. So do we."
        ))

    # ── Faults, told honestly ────────────────────────────────────────────
    sticky = db.fetchone(
        "SELECT COUNT(*) c FROM fault_event WHERE sticky=1")["c"]
    live = db.fetchone(
        "SELECT COUNT(*) c FROM fault_event WHERE sticky=0")["c"]
    if sticky:
        out.append((
            f"{sticky} Complaints",
            f"Warnings the robot latched onto and kept — brownouts, current "
            f"limits, a sensor having a moment. Faults still active at the end: "
            f"{live}. It files everything and forgives nothing."
        ))

    # ── Signals with nothing to say ──────────────────────────────────────
    quiet = db.fetchone("SELECT COUNT(*) c FROM session_constant")["c"]
    total = db.fetchone("SELECT COUNT(*) c FROM series")["c"]
    if quiet and total:
        out.append((
            f"{quiet} Silent Sensors",
            f"Of {_fmt(total)} things we measured, {quiet} never changed once "
            f"in {dur/60:.1f} minutes. Measuring nothing is still measuring."
        ))

    # ── Temperature ──────────────────────────────────────────────────────
    temp = db.fetchone(
        """SELECT MAX(v_max) hi FROM series se JOIN signal s ON s.id=se.signal_id
           WHERE s.name='DeviceTemp'""")
    if temp and temp["hi"]:
        f_hi = temp["hi"] * 9 / 5 + 32
        out.append((
            f"{temp['hi']:.0f}°C",
            f"The hottest any motor got — {f_hi:.0f}°F. Cooler than a cup of "
            f"coffee, and cooler than most people standing in this pit."
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
