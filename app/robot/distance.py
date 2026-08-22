"""
How far did a drive wheel actually roll?

## The two different questions

*Net displacement* — where the wheel ended up relative to where it started — is
`Position(last) - Position(first)`. It is not an odometer: drive forward ten
feet and back ten feet and it reads zero.

*Distance travelled* is the path length, the integral of |velocity| over time.
That is what "how far did it drive" means, and it is what this module computes.

## Two things about this data the maths depends on

**Units are motor-shaft rotations, not wheel rotations.** On the drive motors in
this log `Velocity` and `RotorVelocity` are identical, which means no
`SensorToMechanismRatio` is configured on the TalonFX. So the gear reduction has
to be applied here. (Verify with `check_units()` before trusting a number from a
different robot — if that ratio is ever configured in robot code, dividing by
GEAR_RATIO again would under-report by the ratio.)

**Samples are change-only.** A stored point holds its value until the next one,
so the integral is a zero-order hold — each segment contributes
`value × (next_timestamp - this_timestamp)`. The final point extends to the end
of the session. Treating stored points as evenly spaced would be badly wrong;
they are not.
"""

from dataclasses import dataclass

from app.db import db

# ── Module constants ─────────────────────────────────────────────────────
# These are the only things standing between this query and a wrong answer, and
# a wrong value produces a plausible-looking number rather than an error.
#
# SDS MK5i on R2 drive gearing. 6.02 was supplied by the team — SDS publishes
# the MK5i ratios only as an image on the product page, so it could not be
# confirmed from a text source. Re-check it if the modules are ever re-geared.
GEAR_RATIO = 6.02           # motor rotations per wheel rotation

# Nominal. A worn tread measures smaller and the error is linear, so if the
# absolute distances start looking long, measure a wheel before doubting the
# maths. ← CONFIRM against the wheels actually fitted.
WHEEL_DIAMETER_IN = 4.0

INCHES_PER_MILE = 63_360.0

# Below this the wheel is stationary and the reading is sensor noise, not travel.
# At 0.05 rot/s a 4" wheel creeps 0.6 in/s, and noise at that level integrated
# over a 10-minute log would otherwise add phantom distance.
IDLE_DEADBAND_RPS = 0.05


@dataclass(frozen=True)
class Distance:
    device: str
    motor_rotations: float      # total, unsigned — the odometer
    net_motor_rotations: float  # signed displacement, for comparison
    moving_s: float
    span_s: float
    gear_ratio: float
    wheel_diameter_in: float

    @property
    def wheel_rotations(self) -> float:
        return self.motor_rotations / self.gear_ratio

    @property
    def inches(self) -> float:
        return self.wheel_rotations * 3.141592653589793 * self.wheel_diameter_in

    @property
    def feet(self) -> float:
        return self.inches / 12.0

    @property
    def miles(self) -> float:
        return self.inches / INCHES_PER_MILE

    @property
    def net_inches(self) -> float:
        return (self.net_motor_rotations / self.gear_ratio) \
            * 3.141592653589793 * self.wheel_diameter_in

    @property
    def duty_pct(self) -> float:
        return 100.0 * self.moving_s / self.span_s if self.span_s else 0.0


# ── The query ────────────────────────────────────────────────────────────
# Zero-order hold integration over a change-only series.
#
#   segment length = next stored timestamp - this one
#                    (the last point runs to the end of the session)
#   total rotations = Σ |rps| × segment_seconds
#
# Everything is done in SQL so the 133k-point series never crosses into Python.
_SQL = """
WITH bounds AS (
    SELECT CAST(COALESCE(duration_s, 0) * 1000 AS INTEGER) AS end_ms
    FROM log_session WHERE id = ?
),
pts AS (
    SELECT sa.t_ms,
           sa.v AS rps,
           LEAD(sa.t_ms) OVER (ORDER BY sa.t_ms, sa.ord) AS next_ms
    FROM samples.sample sa
    JOIN series se ON se.id = sa.series_id
    JOIN device d  ON d.id  = se.device_id
    JOIN signal s  ON s.id  = se.signal_id
    WHERE se.session_id  = ?
      AND d.device_type  = ?
      AND d.can_id       = ?
      AND s.name         = 'Velocity'
),
seg AS (
    -- Each stored point holds until the next one; the last runs to session end.
    SELECT rps,
           (COALESCE(next_ms, (SELECT end_ms FROM bounds)) - t_ms) / 1000.0 AS dt_s
    FROM pts
)
SELECT
    -- Deadbanded sums: only segments where the wheel was actually turning
    -- contribute distance. span_s deliberately is NOT deadbanded, so the duty
    -- cycle compares moving time against the whole covered span.
    COALESCE(SUM(CASE WHEN ABS(rps) > ? THEN ABS(rps) * dt_s END), 0) AS motor_rotations,
    COALESCE(SUM(CASE WHEN ABS(rps) > ? THEN rps      * dt_s END), 0) AS net_motor_rotations,
    COALESCE(SUM(CASE WHEN ABS(rps) > ? THEN dt_s          END), 0)   AS moving_s,
    COALESCE(SUM(dt_s), 0)                                            AS span_s
FROM seg
WHERE dt_s > 0
"""

def distance_for(
    session_id: int,
    can_id: int,
    device_type: str = "TalonFX",
    gear_ratio: float = GEAR_RATIO,
    wheel_diameter_in: float = WHEEL_DIAMETER_IN,
    deadband: float = IDLE_DEADBAND_RPS,
) -> Distance:
    """Distance travelled by one drive wheel over one session."""
    # Parameter order follows the ? placeholders top-to-bottom:
    # bounds.session_id, pts.session_id, device_type, can_id, then the three
    # deadband comparisons in the SELECT.
    row = db.fetchone(
        _SQL,
        (session_id, session_id, device_type, can_id, deadband, deadband, deadband),
    )
    label = db.fetchone(
        "SELECT label FROM device WHERE device_type=? AND can_id=?",
        (device_type, can_id))
    name = (label["label"] if label and label["label"]
            else f"{device_type} {can_id}")
    return Distance(
        device=name,
        motor_rotations=row["motor_rotations"],
        net_motor_rotations=row["net_motor_rotations"],
        moving_s=row["moving_s"],
        span_s=row["span_s"],
        gear_ratio=gear_ratio,
        wheel_diameter_in=wheel_diameter_in,
    )


def drive_motors(session_id: int) -> list[int]:
    """
    CAN ids that look like drive motors in this session.

    Drive motors run open-loop (VoltageFOC) and reach real speed; steer motors
    run PositionVoltageFOC and barely turn. Picking them by peak velocity avoids
    hardcoding a CAN map that changes between robots.
    """
    return [r["can_id"] for r in db.fetchall(
        """SELECT d.can_id, se.v_max FROM series se
           JOIN device d ON d.id = se.device_id
           JOIN signal s ON s.id = se.signal_id
           WHERE se.session_id = ? AND d.device_type = 'TalonFX'
             AND s.name = 'Velocity' AND MAX(ABS(se.v_max), ABS(se.v_min)) > 5
           ORDER BY d.can_id""", (session_id,))]


def check_units(session_id: int, can_id: int) -> tuple[bool, str]:
    """
    Confirm Velocity is rotor-referenced before applying the gear ratio.

    Returns (rotor_referenced, explanation). If this ever returns False, the
    TalonFX has a SensorToMechanismRatio configured and `GEAR_RATIO` must NOT
    be applied a second time.
    """
    rows = {r["name"]: (r["v_min"], r["v_max"]) for r in db.fetchall(
        """SELECT s.name, se.v_min, se.v_max FROM series se
           JOIN device d ON d.id = se.device_id
           JOIN signal s ON s.id = se.signal_id
           WHERE se.session_id=? AND d.device_type='TalonFX' AND d.can_id=?
             AND s.name IN ('Velocity','RotorVelocity')""", (session_id, can_id))}
    if "Velocity" not in rows or "RotorVelocity" not in rows:
        return True, "RotorVelocity not logged — assuming rotor units."
    same = all(abs(a - b) < 1e-9 for a, b in zip(rows["Velocity"], rows["RotorVelocity"]))
    if same:
        return True, "Velocity == RotorVelocity — motor-shaft units, gear ratio applies."
    est = rows["RotorVelocity"][1] / rows["Velocity"][1] if rows["Velocity"][1] else 0
    return False, (f"Velocity differs from RotorVelocity (~{est:.2f}x) — a "
                   f"SensorToMechanismRatio is configured, so Velocity is already "
                   f"mechanism units. Do NOT divide by GEAR_RATIO again.")
