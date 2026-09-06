"""
Brand colour in, LED colour out.

The pit's strips are RGBW, and an RGBW pixel does not render a brand hex the
way a screen does. Measured on the real hardware: green and blue read much
brighter than red, so `#C82027` — the team red, only 13% green and 15% blue —
came out visibly PINK, while pure (200, 0, 0) came out correctly red.

Two ways to answer that. Per-channel gain correction chases a colorimetric
match and needs re-tuning for every strip, every batch, and every colour.
Snapping to a saturated primary does not: it says the strips speak a smaller
vocabulary than the screens do, picks the nearest word in it, and accepts
that an LED red is not a Pantone red.

**This is the deliberate choice.** A pit strip has one job — read as the team
colour from across a venue — and a saturated primary does that better than a
desaturated near-match, which just reads as "washed out". Do not "improve"
this by sending brand hexes straight to the wire; that is what produced pink.

The brand value is unchanged everywhere else. `config.active_team.primary_color`
still drives every screen, and `leds.color` still reports the true hex — only
the bytes on the wire are snapped.
"""

import colorsys

# A hue wheel the strips can actually hit, roughly every 30°. Every entry is
# fully saturated on at most two channels, which is what keeps it clean: the
# more channels a colour mixes, the more the die-brightness skew shows.
_WHEEL: tuple[tuple[float, tuple[int, int, int], str], ...] = (
    (0.0,   (255, 0, 0),     "red"),
    (19.0,  (255, 80, 0),    "orange"),
    (38.0,  (255, 160, 0),   "amber"),
    (60.0,  (255, 255, 0),   "yellow"),
    (120.0, (0, 255, 0),     "green"),
    (150.0, (0, 255, 128),   "spring"),
    (180.0, (0, 255, 255),   "cyan"),
    (210.0, (0, 128, 255),   "azure"),
    (240.0, (0, 0, 255),     "blue"),
    (270.0, (128, 0, 255),   "violet"),
    (300.0, (255, 0, 255),   "magenta"),
    (330.0, (255, 0, 128),   "pink"),
)

WHITE: tuple[int, int, int] = (255, 255, 255)

# Below this saturation there is no hue worth snapping to and the answer is
# white — which on an RGBW strip is the dedicated white die, the one channel
# that renders better than any mix.
_MIN_SATURATION = 0.20

# Near-black stays near-black: snapping it to a full primary would turn a
# deliberate dim colour into a floodlight.
_MIN_VALUE = 0.08


def snap(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Nearest LED-renderable colour to `rgb`. Hue is kept, purity is not."""
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

    if v < _MIN_VALUE:
        return (0, 0, 0)
    if s < _MIN_SATURATION:
        return WHITE

    hue = h * 360.0
    best = min(_WHEEL, key=lambda e: _hue_distance(hue, e[0]))
    return best[1]


def name(rgb: tuple[int, int, int]) -> str:
    """What `snap` would call this colour. For logs and the control panel."""
    r, g, b = rgb
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if v < _MIN_VALUE:
        return "off"
    if s < _MIN_SATURATION:
        return "white"
    hue = h * 360.0
    return min(_WHEEL, key=lambda e: _hue_distance(hue, e[0]))[2]


def _hue_distance(a: float, b: float) -> float:
    """Shortest way round the wheel — red at 359° is 1° from red at 0°."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)
