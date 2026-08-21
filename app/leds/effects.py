"""
Named LED presets — the vocabulary the control panel and the mode hooks share.

A preset is a small bundle of firmware state, not an animation: the firmware
owns the actual pixel work. `color=None` means "use the active team's primary
colour", so presets follow the team selector without being rewritten per team.
"""

from dataclasses import dataclass

from app.leds.protocol import Mode


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    mode: Mode
    speed: int = 128
    brightness: int = 180
    color: str | None = None      # None → the active team's primary colour
    description: str = ""


PRESETS: tuple[Preset, ...] = (
    Preset("team_solid", "Team Solid", Mode.SOLID, brightness=190,
           description="Flat wash in the active team's colour."),
    Preset("team_breathe", "Breathe", Mode.BREATHE, speed=60, brightness=190,
           description="Slow pulse. Calm enough to sit behind a conversation."),
    Preset("chase", "Chase", Mode.CHASE, speed=170, brightness=200,
           description="Running dot in the team colour."),
    Preset("wipe", "Wipe", Mode.WIPE, speed=140, brightness=200,
           description="Colour sweeps along the strip and holds."),
    Preset("sparkle", "Sparkle", Mode.SPARKLE, speed=150, brightness=210,
           description="Random twinkles over a dim base."),
    Preset("rainbow", "Rainbow", Mode.RAINBOW, speed=110, brightness=200,
           description="Full spectrum cycle. Ignores the team colour."),
    Preset("lunch", "Lunch", Mode.BREATHE, speed=40, brightness=150,
           color="#E08A1E",
           description="Warm amber, slow. Pairs with the lunch overlay."),
    Preset("judges", "Judges", Mode.BREATHE, speed=45, brightness=165,
           description="Slow and quiet so the strips never pull focus."),
    Preset("alert", "Alert", Mode.ALERT, speed=220, brightness=255,
           color="#C82027",
           description="Fast red flash. Use it for something that matters."),
    Preset("off", "Off", Mode.OFF, brightness=0,
           description="Blank the strip."),
)

BY_KEY: dict[str, Preset] = {p.key: p for p in PRESETS}

# Which preset each display mode selects when "follow app mode" is on.
MODE_PRESETS: dict[str, str] = {
    "standard": "team_solid",
    "judges":   "judges",
    "lunch":    "lunch",
}


def get(key: str) -> Preset | None:
    return BY_KEY.get(key)
