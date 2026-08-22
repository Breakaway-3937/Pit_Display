"""
Queries over imported robot logs.

Everything the screens need, expressed once. All of these read the summary
tables rather than `samples.sample` — measured at well under 3 ms even with a
62-million-row import behind them.
"""

from dataclasses import dataclass

from app.db import db


@dataclass(frozen=True)
class DeviceRow:
    id: int
    device_type: str
    can_id: int
    label: str
    subsystem: str
    # What the log itself reported was plugged in — a TalonFX knows which motor
    # is attached and says so via ConnectedMotor. Empty for devices that have
    # no such signal (CANcoder, Pigeon2) or that have never appeared in a log.
    detected: str = ""

    @property
    def display(self) -> str:
        """What a human should see. Falls back to the CAN address."""
        return self.label or f"{self.device_type} {self.can_id}"

    @property
    def named(self) -> bool:
        return bool(self.label)


# ── The CAN-id → English name map ────────────────────────────────────────

def detected_hardware() -> dict[int, str]:
    """
    device_id → the motor the robot reported, e.g. 'KrakenX60'.

    Read from the most recent session's `ConnectedMotor` constant. The firmware
    reports it as 'KrakenX60_Integrated'; the suffix is noise for a name column.
    """
    rows = db.fetchall(
        """SELECT sc.device_id, sc.v_text
           FROM session_constant sc
           JOIN signal s ON s.id = sc.signal_id
           WHERE s.name = 'ConnectedMotor' AND sc.v_text IS NOT NULL
             AND sc.session_id = (SELECT MAX(session_id) FROM session_constant)""")
    return {r["device_id"]: r["v_text"].replace("_Integrated", "").strip()
            for r in rows}


def devices() -> list[DeviceRow]:
    detected = detected_hardware()
    return [
        DeviceRow(r["id"], r["device_type"], r["can_id"],
                  r["label"] or "", r["subsystem"] or "",
                  detected.get(r["id"], ""))
        for r in db.fetchall(
            "SELECT * FROM device ORDER BY device_type, can_id")
    ]


def set_device_name(device_id: int, label: str, subsystem: str) -> None:
    with db.transaction():
        db.execute("UPDATE device SET label=?, subsystem=? WHERE id=?",
                   (label.strip() or None, subsystem.strip() or None, device_id))


def add_device(device_type: str, can_id: int, label: str = "",
               subsystem: str = "") -> int | None:
    """
    Pre-register a device before its first log arrives.

    Returns None if that (type, id) already exists — the caller should edit the
    existing row rather than create a duplicate.
    """
    if db.fetchone("SELECT id FROM device WHERE device_type=? AND can_id=?",
                   (device_type, can_id)):
        return None
    with db.transaction():
        cur = db.execute(
            "INSERT INTO device (device_type, can_id, label, subsystem) VALUES (?,?,?,?)",
            (device_type, can_id, label.strip() or None, subsystem.strip() or None))
    return cur.lastrowid


def delete_device(device_id: int) -> bool:
    """Only allowed while nothing references it."""
    if db.fetchone("SELECT 1 FROM series WHERE device_id=? LIMIT 1", (device_id,)):
        return False
    with db.transaction():
        db.execute("DELETE FROM device WHERE id=?", (device_id,))
    return True


def unnamed_count() -> int:
    return db.fetchone("SELECT COUNT(*) n FROM device WHERE label IS NULL")["n"]


# ── Sessions ─────────────────────────────────────────────────────────────

def sessions() -> list:
    return db.fetchall(
        """SELECT id, source_name, started_at, duration_s, raw_rows, stored_rows,
                  source_bytes, match_key, keep, imported_at
           FROM log_session ORDER BY imported_at DESC""")


def session(session_id: int):
    return db.fetchone("SELECT * FROM log_session WHERE id=?", (session_id,))


def set_session_meta(session_id: int, match_key: str, keep: bool) -> None:
    with db.transaction():
        db.execute("UPDATE log_session SET match_key=?, keep=? WHERE id=?",
                   (match_key.strip() or None, 1 if keep else 0, session_id))


# ── What the pit display actually asks ───────────────────────────────────

def peak_by_signal(session_id: int, signal_name: str) -> list:
    """Per-device min/max/mean for one signal, named where a name exists."""
    return db.fetchall(
        """SELECT d.device_type, d.can_id, d.label, d.subsystem,
                  se.v_min, se.v_max, se.v_mean
           FROM series se
           JOIN device d ON d.id = se.device_id
           JOIN signal s ON s.id = se.signal_id
           WHERE se.session_id = ? AND s.name = ?
           ORDER BY se.v_max DESC""", (session_id, signal_name))


def sticky_faults(session_id: int) -> list:
    """Every latched fault that tripped — the headline diagnostic."""
    return db.fetchall(
        """SELECT d.device_type, d.can_id, d.label, d.subsystem, s.name,
                  f.t_ms_start, f.t_ms_end
           FROM fault_event f
           JOIN device d ON d.id = f.device_id
           JOIN signal s ON s.id = f.signal_id
           WHERE f.session_id = ? AND f.sticky = 1
           ORDER BY d.device_type, d.can_id, s.name""", (session_id,))


def battery_low(session_id: int) -> float | None:
    r = db.fetchone(
        """SELECT MIN(se.v_min) lo FROM series se
           JOIN signal s ON s.id = se.signal_id
           WHERE se.session_id = ? AND s.name = 'SupplyVoltage'""", (session_id,))
    return r["lo"] if r else None


def trace(session_id: int, device_id: int, signal_name: str) -> list:
    """Full-resolution change points for charting."""
    return db.fetchall(
        """SELECT sa.t_ms, sa.v FROM samples.sample sa
           JOIN series se ON se.id = sa.series_id
           JOIN signal s ON s.id = se.signal_id
           WHERE se.session_id=? AND se.device_id=? AND s.name=?
           ORDER BY sa.t_ms, sa.ord""", (session_id, device_id, signal_name))


def rollup(session_id: int, device_id: int, signal_name: str) -> list:
    """1-second min/max/avg — the cheap version for dashboards."""
    return db.fetchall(
        """SELECT r.t_s, r.v_min, r.v_max, r.v_avg FROM samples.sample_1s r
           JOIN series se ON se.id = r.series_id
           JOIN signal s ON s.id = se.signal_id
           WHERE se.session_id=? AND se.device_id=? AND s.name=?
           ORDER BY r.t_s""", (session_id, device_id, signal_name))


def signal_names(session_id: int, klass: str = "telemetry") -> list[str]:
    return [r["name"] for r in db.fetchall(
        """SELECT DISTINCT s.name FROM series se JOIN signal s ON s.id=se.signal_id
           WHERE se.session_id=? AND s.signal_class=? ORDER BY s.name""",
        (session_id, klass))]


def wheel_distance(session_id: int, can_id: int, **kw):
    """Distance one drive wheel rolled. See app/robot/distance.py for the maths."""
    from app.robot.distance import distance_for
    return distance_for(session_id, can_id, **kw)


def drive_distances(session_id: int, **kw) -> list:
    """Every drive wheel in the session, in CAN-id order."""
    from app.robot.distance import distance_for, drive_motors
    return [distance_for(session_id, c, **kw) for c in drive_motors(session_id)]


def session_totals() -> dict:
    r = db.fetchone(
        """SELECT COUNT(*) n, COALESCE(SUM(raw_rows),0) raw,
                  COALESCE(SUM(stored_rows),0) stored FROM log_session""")
    return {"sessions": r["n"], "raw_rows": r["raw"], "stored_rows": r["stored"],
            "samples_bytes": db.samples_bytes()}
