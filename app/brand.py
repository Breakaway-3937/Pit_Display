"""
Breakaway 3937 — brand system tokens and helpers.

Single source of truth for colors, fonts, and the signature "Cut" chamfer
geometry. See Breakaway_Branding.md for the full spec. Everything visual in
the app should pull from here rather than hard-coding hex values.

Design thesis: *Precision under speed.* One red used with intent; white (or
carbon, reversed) carries the layout. Chakra Petch for display/numbers,
Roboto for body. The Cut appears sparingly, as a signature — never on the logo.
"""

# ── Type faces ──────────────────────────────────────────────────────────────
FONT_DISPLAY = "Chakra Petch"   # headlines, section titles, numbers, eyebrows
FONT_BODY    = "Roboto"         # body copy, labels, tables, UI text

# ── Core brand colors ───────────────────────────────────────────────────────
RED       = "#C82027"   # Breakaway Red — the brand. Focus, CTAs, highlights
RED_HOVER = "#B01C22"   # primary button hover
EMBER     = "#8E1519"   # deep red — pressed states, small accents (sparingly)
CARBON    = "#181416"   # ink on light; dark backgrounds. Not pure black
WHITE     = "#FFFFFF"

# ── Neutral scale ───────────────────────────────────────────────────────────
N50  = "#FAF9F8"   # app background (light)
N100 = "#F3F1F0"   # surface / fill
N200 = "#E5E2E1"   # hairline / border
N300 = "#CFCBC9"   # input border
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

# ── Status dots ─────────────────────────────────────────────────────────────
STATUS_ONLINE  = "#2E8B7F"
STATUS_PENDING = "#E08A1E"
STATUS_FAULT   = "#C82027"
STATUS_IDLE    = "#A6A19E"

# ── Data-viz categorical (red = focus; rest neutral/context) ────────────────
CATEGORICAL = ["#C82027", "#2B3A67", "#E08A1E", "#2E8B7F", "#6B4E71", "#59595B"]


# ── The Cut (chamfer) size tiers, in px ─────────────────────────────────────
CUT_SMALL  = 8    # tags / buttons
CUT_MEDIUM = 14   # cards / callouts
CUT_LARGE  = 28   # heroes / banners


def cut_polygon(w: int, h: int, cut: int = CUT_MEDIUM):
    """
    Points for The Cut applied to two diagonally-opposite corners
    (top-right + bottom-left) — one consistent diagonal shear implying motion.
    Returns a list of (x, y) tuples winding clockwise from top-left.
    """
    return [
        (0, 0),
        (w - cut, 0),
        (w, cut),
        (w, h),
        (cut, h),
        (0, h - cut),
    ]


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
