"""
Ten-band equaliser: band definitions, presets, and persistence.

The presets are opinionated on purpose. A 10x10 pit with a concrete floor and
fabric walls has two problems that show up every single event: low end piling
up and booming into the neighbours, and midrange mud that buries speech. Every
preset here high-passes the bottom and cuts 200-400Hz for that reason.

Worth saying plainly: aiming the speakers down into the pit rather than across
the aisle will beat any of these. The EQ is the second lever, not the first.
"""

from dataclasses import dataclass, field

from app.db import db

# libVLC's fixed ten-band layout.
BAND_HZ = [31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
BAND_LABELS = ["31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]

GAIN_MIN = -20.0
GAIN_MAX = 20.0
N_BANDS = 10


@dataclass
class EQPreset:
    name: str
    preamp: float = 0.0
    gains: list[float] = field(default_factory=lambda: [0.0] * N_BANDS)
    built_in: bool = False
    description: str = ""

    def clamped(self) -> list[float]:
        return [max(GAIN_MIN, min(GAIN_MAX, g)) for g in self.gains]


#                          31    62   125   250   500    1K    2K    4K    8K   16K
BUILT_INS: tuple[EQPreset, ...] = (
    EQPreset(
        "Flat", 0.0, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], True,
        "No correction. Start here when judging a new room.",
    ),
    EQPreset(
        "Pit Default", -2.0,
        [-12, -8, -3, -4, -2, 0, 2, 3, 1, 0], True,
        "High-pass the boom, cut the mud, lift presence. The everyday setting.",
    ),
    EQPreset(
        "Crowded", -3.0,
        [-14, -10, -4, -5, -2, 1, 3, 4, 2, 0], True,
        "More cut below and more presence up top so it carries over a full pit "
        "without getting louder.",
    ),
    EQPreset(
        "Judges Visiting", -6.0,
        [-16, -12, -6, -6, -3, 0, 2, 2, 0, -2], True,
        "Quiet and speech-clear. Music stays under the conversation.",
    ),
    EQPreset(
        "Lunch", -2.0,
        [-8, -5, -2, -3, -1, 0, 1, 2, 1, 0], True,
        "Fuller and warmer. Nobody is being judged over it.",
    ),
)

BUILT_IN_NAMES = {p.name for p in BUILT_INS}


def ensure_seeded() -> None:
    """Write the built-ins into the DB once, so they can be listed uniformly."""
    with db.transaction():
        for preset in BUILT_INS:
            db.execute(
                """INSERT OR IGNORE INTO eq_presets (name, preamp, gains, built_in)
                   VALUES (?, ?, ?, 1)""",
                (preset.name, preset.preamp,
                 ",".join(str(g) for g in preset.gains)),
            )


def _row_to_preset(row) -> EQPreset:
    try:
        gains = [float(x) for x in row["gains"].split(",")]
    except (ValueError, AttributeError):
        gains = [0.0] * N_BANDS
    gains = (gains + [0.0] * N_BANDS)[:N_BANDS]
    desc = next((p.description for p in BUILT_INS if p.name == row["name"]), "")
    return EQPreset(row["name"], row["preamp"], gains, bool(row["built_in"]), desc)


def all_presets() -> list[EQPreset]:
    """
    Built-ins in **authored order**, then anything the team saved, by name.

    The authored order is the order an operator reaches for them — Flat to
    judge the room, Pit Default for the day, then the three exceptions — and
    sorting the built-ins alphabetically put "Crowded" first, which is nobody's
    starting point.
    """
    rows = [_row_to_preset(r) for r in db.fetchall("SELECT * FROM eq_presets")]
    order = {p.name: i for i, p in enumerate(BUILT_INS)}
    return sorted(rows, key=lambda p: (0, order[p.name], "") if p.name in order
                  else (1, 0, p.name))


def get_preset(name: str) -> EQPreset | None:
    row = db.fetchone("SELECT * FROM eq_presets WHERE name = ?", (name,))
    return _row_to_preset(row) if row else None


def save_preset(preset: EQPreset) -> None:
    """Upsert. Built-ins are protected — saving over one creates a copy."""
    name = preset.name
    if name in BUILT_IN_NAMES:
        name = f"{name} (edited)"
    with db.transaction():
        db.execute(
            """INSERT INTO eq_presets (name, preamp, gains, built_in)
               VALUES (?, ?, ?, 0)
               ON CONFLICT(name) DO UPDATE SET preamp = excluded.preamp,
                                               gains  = excluded.gains""",
            (name, preset.preamp, ",".join(str(g) for g in preset.clamped())),
        )


def delete_preset(name: str) -> bool:
    if name in BUILT_IN_NAMES:
        return False
    with db.transaction():
        db.execute("DELETE FROM eq_presets WHERE name = ? AND built_in = 0", (name,))
    return True


# Which EQ preset each display mode selects when the EQ is following the mode.
MODE_PRESETS: dict[str, str] = {
    "standard": "Pit Default",
    "judges":   "Judges Visiting",
    "lunch":    "Lunch",
}
