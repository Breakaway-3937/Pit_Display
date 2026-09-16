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
    # The white die instead of a colour — the only real white these strips
    # have. RGB is sent black; the fourth channel does all of it (fw 2.2+).
    white: bool = False
    description: str = ""


# Full white on the W die is the pit's work light and its resting look. 160
# matches the firmware's own WHITE switch position: every pixel lit at once is
# the one state that draws full current everywhere (~2.1 A at 169 px), and
# the pit supply was sized against that figure. Raise it only against a
# known supply — and in the firmware's OVERRIDE_WHITE_BRIGHTNESS too.
WHITE_BRIGHTNESS = 160


PRESETS: tuple[Preset, ...] = (
    Preset("white", "White", Mode.SOLID, brightness=WHITE_BRIGHTNESS, white=True,
           description="Full white on the white die — the work light, and "
                       "the resting look. Standard and judges modes select it."),
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
    Preset("lunch", "Lunch", Mode.SOLID, brightness=190, color="#FF0000",
           description="Solid red, and nothing else runs on the strips until "
                       "lunch is over. Pairs with the lunch overlay."),
    Preset("alert", "Alert", Mode.ALERT, speed=220, brightness=255,
           color="#C82027",
           description="Fast red flash. Use it for something that matters."),
    Preset("off", "Off", Mode.OFF, brightness=0,
           description="Blank the strip."),
)

BY_KEY: dict[str, Preset] = {p.key: p for p in PRESETS}

# Which preset each display mode selects when "follow app mode" is on.
# Standard and judges are both the white work light; the difference is that
# judges and lunch are *overrides* — no alert touches the strips while either
# is active (`_LEDService.start_alert`), where standard lets the queue and
# inspection alerts through.
MODE_PRESETS: dict[str, str] = {
    "standard": "white",
    "judges":   "white",
    "lunch":    "lunch",
}

# Modes in which the strips are locked to their preset and every alert is
# ignored: the judges are at the pit, or the pit is at lunch.
OVERRIDE_MODES = ("judges", "lunch")


def get(key: str) -> Preset | None:
    return BY_KEY.get(key)
