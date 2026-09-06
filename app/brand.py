"""
Breakaway 3937 — brand system tokens and helpers (Brand System v2.0).

Single source of truth for colors, fonts, the Rounded radius scale, and the
typography scale. See Breakaway_Branding.md for the full spec. Everything
visual in the app should pull from here rather than hard-coding hex values.

Design thesis: *Precision under speed.* Confident, disciplined, roomy. One red
used with intent (the "red budget" — one focal red per surface); white (or
carbon, reversed) carries the layout. Chakra Petch for display/numbers, Roboto
for body.

The device system (§5): **Rounded** is the shell on every surface; **Bracket**,
**Pocket**, and **Trace** are accents — one accent per surface, never stacked.
The reusable Qt widgets live in app/widgets/brand_widgets.py.
"""

# ── Type faces ──────────────────────────────────────────────────────────────
FONT_DISPLAY = "Chakra Petch"   # headlines, section titles, numbers, eyebrows
FONT_BODY    = "Roboto"         # body copy, labels, tables, UI text
FONT_MONO    = "JetBrains Mono" # data, scores, timestamps (tabular figures)

# Mono is the one role the brand spec defines as a *stack* (§2: "ui-monospace /
# JetBrains Mono"), so it needs real fallbacks. JetBrains Mono is bundled in
# assets/fonts/ and loaded at startup, but naming a family Qt cannot find costs
# a full font-alias sweep on every QFont construction — and prints a warning.
# These are the platform defaults: macOS, Windows, Linux, then the universal.
FONT_MONO_STACK = [FONT_MONO, "Menlo", "Consolas", "DejaVu Sans Mono",
                   "Courier New", "monospace"]

# ── Core brand colors ───────────────────────────────────────────────────────
RED       = "#BA141A"   # Breakaway Red — the brand. Focus, CTAs, highlights
RED_HOVER = "#A21117"   # primary button hover
EMBER     = "#8E1519"   # deep red — pressed states, tint-chip text (sparingly)
CARBON    = "#181416"   # ink on light; dark backgrounds. Not pure black
GRAPHITE  = "#59595B"   # brand grey — secondary series, secondary fields
WHITE     = "#FFFFFF"

# ── Neutral scale ───────────────────────────────────────────────────────────
N50  = "#FAF9F8"   # app background (light)
N100 = "#F3F1F0"   # surface / fill
N200 = "#E5E2E1"   # hairline / border
N300 = "#CFCBC9"   # card border / input border
N400 = "#A6A19E"   # disabled / muted
N500 = "#6A6462"   # secondary text (AA floor on white)
N600 = "#443F3D"   # tertiary ink

# ── Dark (Carbon) surface scale ─────────────────────────────────────────────
# Built around Carbon (#181416) so the dark theme stays on-brand — never black.
CARBON_BG    = "#121013"   # app background
CARBON_SURF  = "#1D1A1C"   # cards / panels
CARBON_SURF2 = "#272327"   # raised controls
CARBON_LINE  = "#332F32"   # hairline / border on dark
INK_DARK     = "#F3F1F0"   # body text on dark (N100)
MUTED_DARK   = "#A6A19E"   # secondary text on dark (N400)
FAINT_DARK   = "#6A6462"   # tertiary text on dark (N500)

# ── Status dots (§7) ────────────────────────────────────────────────────────
STATUS_ONLINE  = "#2E8B7F"
STATUS_PENDING = "#E08A1E"
STATUS_FAULT   = "#BA141A"
STATUS_IDLE    = "#A6A19E"

# ── Data-viz palettes (§1) — red = focus; rest neutral/context ──────────────
CATEGORICAL = ["#BA141A", "#2B3A67", "#E08A1E", "#2E8B7F", "#6B4E71", "#59595B"]
SEQUENTIAL  = ["#FBE4E5", "#F3AEB1", "#E77B7F", "#D8474C", "#BA141A", "#8E1519"]
DIVERGING   = ["#2B3A67", "#6E7FA8", "#B9C2D8", "#EEEBEA", "#E79B9E", "#D8555A",
               "#BA141A"]


# ── Rounded — the container radius scale (§5.1), in px ──────────────────────
# The default shell on every surface. Never mix two radii on one element, and
# never mix a sharp corner with a rounded one.
R_PILL   = 999   # tags / chips / pills (full pill = ½ height)
R_BTN    = 10    # buttons / inputs
R_CARD   = 14    # cards / callouts
R_BANNER = 18    # banners / hero panels (16–20)
R_MEDIA  = 12    # images / media


# ── Typography scale (§2), pixel sizes + tracking (‰, Qt PercentageSpacing) ──
# (px, weight_is_bold, tracking_permille). Weight True = 700, False = 600/400
# per role; consumers pick the concrete QFont weight. Kept as a lookup so the
# app's programmatic type stays in one place alongside the QSS roles in theme.
TYPE = {
    "display_xl": {"px": 56, "track": 99},   # -0.01em
    "display_l":  {"px": 40, "track": 99},   # -0.01em
    "h1":         {"px": 30, "track": 100},
    "h2":         {"px": 22, "track": 100},
    "h3":         {"px": 17, "track": 100},
    "lead":       {"px": 18, "track": 100},
    "body":       {"px": 16, "track": 100},
    "small":      {"px": 14, "track": 100},
    "eyebrow":    {"px": 12, "track": 116},  # +0.16em, UPPER
    "mono":       {"px": 13, "track": 100},
}


def red_tint(alpha: float = 0.12) -> str:
    """Red-tint chip fill (§7): rgba(186,20,26,alpha). Pair with EMBER text."""
    return f"rgba(186, 20, 26, {alpha})"


# ── The Plate — the chassis every full-screen surface is built on ───────────
# One architectural inset panel floating on the app ground, so the screen has a
# physical edge and the type has a room to sit in. Shared by the presentation
# slides, both diagnostics boards and the project screen, which is what makes
# them read as one instrument from across the pit.
#
# The gradient is the depth. On dark it is a lit face falling away to the
# bottom-right; on light the same logic survives as a cast shadow rather than
# being swapped for a flat fill — the light theme is a derivative, not a token
# swap.
PLATE_INSET  = 40    # px from the screen edge at 1920×1080, scaled per surface
PLATE_RADIUS = R_BANNER

PLATE_DARK   = ("#221E22", "#1A171A", "#151215")   # 0% · 46% · 100%
PLATE_LIGHT  = ("#FFFFFF", "#FDFCFC", "#F3F1F0")   # 0% · 50% · 100%
PLATE_ANGLE  = 157   # degrees, clockwise from 12 o'clock

# Surfaces sitting *on* the plate. Not the CARBON_SURF scale: those are tuned
# for controls on the app ground, and a tile on the lit plate needs to sit a
# little above the plate's own mid-tone or it reads as a hole.
TILE_DARK    = "#1E1B1E"   # tile / card fill on the plate
TILE_LIGHT   = "#FFFFFF"
RAISED_DARK  = "#262227"   # active row, pressed control, selected nav item
DIVIDER_DARK = "#2A262A"   # the faintest rule — column separators only
FAULT_EDGE   = "#5A1418"   # the one tile a latched fault came from

# The ambient light-fall: an off-centre radial wash that drifts over 34s. Six
# percent on dark is deliberately near-invisible — it is there to keep a 55"
# panel of near-black from looking dead, not to be seen as a gradient.
LIGHTFALL_ALPHA = 0.06
LIGHTFALL_SECS  = 34


# ── Palette bundles (theme-aware) ───────────────────────────────────────────
# Screens read the right bundle by theme name, and app.theme renders both QSS
# stylesheets from these — one template, light/dark parity guaranteed.

DARK = {
    "bg":           CARBON_BG,
    "surface":      CARBON_SURF,
    "surface2":     CARBON_SURF2,
    "line":         CARBON_LINE,
    "ink":          INK_DARK,
    "muted":        MUTED_DARK,
    "faint":        FAINT_DARK,
    "title":        WHITE,          # headline ink (brighter than body on dark)
    "input_bg":     CARBON_SURF2,
    "input_border": CARBON_LINE,
    "hover_border": "#443F3D",
    "control_hover":   "#322D31",
    "control_pressed": CARBON_SURF,
    "scroll_handle":       "#443F3D",
    "scroll_handle_hover": "#6A6462",
}

LIGHT = {
    "bg":           N50,
    "surface":      WHITE,
    "surface2":     N100,
    "line":         N200,
    "ink":          CARBON,
    "muted":        N500,
    "faint":        N400,
    "title":        CARBON,
    "input_bg":     WHITE,
    "input_border": N300,
    "hover_border": N400,
    "control_hover":   N200,
    "control_pressed": N300,
    "scroll_handle":       N300,
    "scroll_handle_hover": N400,
}


def palette(theme: str) -> dict:
    return LIGHT if theme == "light" else DARK
