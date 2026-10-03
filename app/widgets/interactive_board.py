"""
The pit-front panel — one interpretive surface, 1080×1920 portrait.

A 32" monitor at standing height, touched by strangers. **The tabs are gone.**
Everything the board has to say is on one surface at one glance — the robot,
the law, the numbers, the programs, the sponsors — stacked as an interpretive
panel rather than paged as an app. Tapping only ever *deepens* what is already
visible; it never navigates away from it.

## One surface, five bands

    identity  →  CAD  →  Act 472  →  reach  →  programs  →  sponsors

top to bottom, each on a 2px rule. **The order is the argument:** this is the
robot, this is what the team changed, this is how far it reached.

## The CAD is not a page

It is the top 648px of the same panel — always live, always orbitable, with the
subsystem chips on its own floor. Focusing a subsystem changes the caption under
it and the pit LEDs; nothing else on the board moves. The viewer is *lent* to
the board by `ProjectScreen` (`attach_cad`) rather than built here, so the pit
never runs two Chromium scenes for one robot.

## The red, spent once

The **Act 472 plate** is this surface's one red — a filled field with white
type, never red letterforms (red on carbon is 2.8:1). Chips, stats and cards
stay carbon and white.

## About, collapsed to a line

The About tab was a paragraph nobody standing up will read. The five E's become
a single mono rail under the wordmark — the mission stated in five words,
legible from across the pit.

## Detail rises, never replaces

A tapped card raises a sheet over the lower two thirds and **the CAD stays
visible above it** — 340ms OutCubic, with the scrim painted to 62%. Press and
release only; there is no hover state anywhere on this screen, because there is
no pointer.

Content below is the team's real material; editing `TABS` reshapes the board.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QScrollArea, QSizePolicy, QFrame,
)
from PyQt6.QtCore import (
    Qt, QEasingCurve, QPropertyAnimation, QRect, QRectF, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap

from app import brand, paths, wording
from app.cad_assets import cad_assets
from app.config import config
from app.touch import is_touch
from app.widgets.brand_widgets import (
    RoundedFrame, RoundedButton, SelectableChip, pocket_path,
)
from app.widgets.chassis import PlatePanel


# ── Content model ─────────────────────────────────────────────────────────────
# Each tab has an id, a short tab-bar label, an eyebrow, a title, and a kind.
# "cards" tabs list items; each item = {id, name, blurb, optional detail,
# optional subs}. `id` is the item's stable name for app/wording.py (an admin
# can edit name, blurb and detail in the app; the text here is the shipped
# default), so never change an id once shipped. `detail` is the longer text shown when the card is tapped (falls back
# to blurb). Editing this list is all the team needs to reshape the board.

TABS: list[dict] = [
    {
        "id": "about",
        "tab": "About",
        "eyebrow": "Who We Are",
        "title": "About Breakaway",
        "kind": "about",
    },
    {
        "id": "legislation",
        "tab": "Legislation",
        "eyebrow": "Advocacy",
        "title": "Legislation",
        "kind": "cards",
        "items": [
            {"id": "act472", "name": "The Bill — Act 472",
             "blurb": "We pioneered and passed Act 472, Arkansas's first state "
                      "funding for competitive robotics teams — signed into law "
                      "by the Governor in April 2025.",
             "detail": "For 24+ months we worked the legislative process end to "
                       "end — hosting legislators at our shop, giving Capitol pit "
                       "tours, and meeting with the Joint Education Committee. We "
                       "organized a Competitive Robotics Capitol Day where 6 FRC "
                       "and 4 VEX teams demonstrated robots and advocated; the "
                       "entire Arkansas Board of Education and the Secretary of "
                       "Education attended. We secured bill sponsors, testified "
                       "before the Senate and House Education Committees, and in "
                       "April 2025 Act 472 was signed into law. We're now helping "
                       "the AR Dept. of Education write the rules, and plan to "
                       "publish a playbook so other teams can do the same."},
            {"id": "museum", "name": "Museum of Discovery",
             "blurb": "Our 10+ year partnership with the Museum of Discovery "
                      "(MoD) in Little Rock has grown from 1 to 3 annual events, "
                      "reaching 3,500+ people and raising $10K+ for MoD.",
             "detail": "For 10+ years we've partnered with the Museum of "
                       "Discovery, hosting a LEGO robotics booth at their "
                       "Tinkerfest festival (2,500+ visitors/year). Three years "
                       "ago we added a QR code to at-home STEM activities; last "
                       "year we added our BIT Kits. The partnership has grown "
                       "from 1 to 3 annual events, reached 3,500+ people and 20+ "
                       "schools, and raised $10K+ for MoD through Tinkerfest "
                       "field-trip sponsorships — grown from 1 to 15 in 3 years."},
        ],
    },
    {
        "id": "outreach",
        "tab": "Outreach",
        "eyebrow": "In the Community",
        "title": "Outreach & Events",
        "kind": "cards",
        "items": [
            {"id": "girls_stem", "name": "Girls in STEM Camp",
             "blurb": "For 2 years, team members have served as guest mentors at "
                      "the Museum of Discovery's Girls in STEM Camp, helping girls "
                      "see themselves in STEM."},
            # NOTE: "Tinkerfest" in the team outline = MoD's original in Little Rock.
            {"id": "tinkerfest_lr", "name": "Tinkerfest (Little Rock)",
             "blurb": "For 10+ years we've run a LEGO robotics booth at MoD's "
                      "Tinkerfest STEM festival (2,500+ visitors/year), plus BIT "
                      "Kits and QR-linked at-home activities — 600+ kits since 2024."},
            # NOTE: read the outline's "Circe Tinkerfest" as SEARCY Tinkerfest — confirm.
            {"id": "tinkerfest_searcy", "name": "Searcy Tinkerfest",
             "blurb": "Inspired by MoD, we started our own Tinkerfest in Searcy — "
                      "now a STEM field trip at the Arkansas Regional reaching "
                      "1,800+ students from 13 schools across 9 districts.",
             "detail": "Seeing MoD's Tinkerfest succeed in Little Rock, we "
                       "partnered with MoD and local leaders to start our own in "
                       "Searcy. Begun in 2018 as a community festival, it's now a "
                       "STEM field trip at the Arkansas Regional: students watch "
                       "matches and join pit tours, interactive STEM booths, and "
                       "team Q&A. Since 2023 it's grown to 1,800+ students from 13 "
                       "schools across 9 districts and 8 communities in 4 counties."},
            {"id": "foster_care", "name": "Foster Care",
             "blurb": "Since 2024 we've partnered with 2 local foster-care "
                      "organizations, providing 100+ dual-activity BIT Kits to "
                      "visit centers so parents and children can build together, "
                      "plus fundraising help and a summer STEM session."},
            {"id": "first_arkansas", "name": "FIRST in Arkansas",
             "blurb": "We host or co-host every FRC event in Arkansas and have "
                      "mentored 15+ teams — including 100% of AR FRC teams.",
             "detail": "We host or co-host every FRC touchpoint in Arkansas: FRC "
                       "Kickoff with 25 workshops (100% of AR FRC teams taught), a "
                       "Week 0 Scrimmage on a full field, the Arkansas Regional "
                       "(the only in-state event — closest competition for 75% of "
                       "AR teams), and the Ozark Mountain Brawl off-season. Our "
                       "year-round Open Shop has hosted 86% of AR FRC teams, and "
                       "our Programming Initiative supports 60% of them. Over 3 "
                       "years we've dedicated 1.3K+ hours mentoring 15+ teams and "
                       "started 4 FLL teams and 1 FTC team.",
             "subs": ["Events", "Teams"]},
        ],
    },
    {
        "id": "community",
        "tab": "Camps",
        "eyebrow": "Year-Round Programs",
        "title": "Community Camps & Clubs",
        "kind": "cards",
        "items": [
            {"id": "lego_club", "name": "LEGO Club",
             "blurb": "Our 10-week LEGO Club (since 2016) teaches K–6 students to "
                      "design, build, and code LEGO Education robots — 240+ "
                      "students over the last 3 years."},
            {"id": "lego_camp", "name": "LEGO Camp",
             "blurb": "A one-week LEGO summer camp we've run with our school for "
                      "10+ years, introducing young students to robotics."},
            {"id": "downtown_camp", "name": "Downtown Discovery Camp",
             "blurb": "We partner with community summer programs like Downtown "
                      "Discovery Camp to run robot demos and hands-on STEM "
                      "activities for local kids."},
        ],
    },
    {
        "id": "awards",
        "tab": "Awards",
        "eyebrow": "Recognition",
        "title": "Award Submissions",
        "kind": "cards",
        "items": [
            {"id": "exec_summaries", "name": "Executive Summaries",
             "blurb": "Our answers to the 13 FIRST Impact Award questions — the "
                      "data and stories behind our reach: 9K+ served, 100% of AR "
                      "FRC teams, Act 472, and more."},
            {"id": "impact_essay", "name": "Impact Essay",
             "blurb": "“What began as a sketch is now a growing network that "
                      "makes STEM accessible across Arkansas.” Our FIRST Impact "
                      "Award essay, told through the 5 E's.",
             "detail": "In 2011, 12 students and 1 mentor sketched a map for a "
                       "single goal: build a competitive robot. Each year since, "
                       "Breakaway has iterated that map toward a growing vision "
                       "that “Every Kid Can!” succeed in STEM. Told through our 5 "
                       "E's — Excite, Engage, Equip, Empower, Expand — the essay "
                       "traces how we've reached 9K+ people, mentored 100% of AR "
                       "FRC teams, and passed Act 472. “To Breakaway, impact is "
                       "not just a statistic; it is an investment into our state. "
                       "We are changing the map of Arkansas.”"},
            {"id": "woodie_flowers", "name": "Woodie Flowers Essay",
             "blurb": "Honoring Head Coach Brian Jones, who founded Breakaway in "
                      "2011 and leads “without limits.”",
             "detail": "“If I have seen further, it is by standing on the "
                       "shoulders of giants.” Coach Brian Jones founded Breakaway "
                       "in 2011 in Searcy and teaches six high-school math and "
                       "science classes, arriving 30–60 minutes early every day "
                       "to help any student. He hosts our fall goal-setting "
                       "retreat and monthly department-lead lunches, and "
                       "personally hosts or runs all four Arkansas FRC events "
                       "each year. At every event you'll find him re-taping "
                       "carpet, lending spare parts, and sending our own members "
                       "to help other teams. To Coach, “without limits” means "
                       "never giving up on FIRST in Arkansas."},
            {"id": "kpis", "name": "KPIs",
             "blurb": "We're developing Key Performance Indicators to pair "
                      "qualitative relationship data with our quantitative reach, "
                      "adding dimension to the full scope of our impact."},
        ],
    },
]

# ── The mission and the reach ────────────────────────────────────────────────
# Breakaway's 5 E's of Opportunity. The panel sets these as one mono rail — the
# mission in five words — rather than the paragraph the About tab used to hold,
# which nobody standing at a kiosk was ever going to read.
_ABOUT_ES_DETAIL = [
    ("Excite",  "Raise STEM awareness through events, robot demos, and partnerships."),
    ("Engage",  "Build relationships that make STEM accessible to more students."),
    ("Equip",   "Give Arkansas teams the tools to build beyond the Kitbot."),
    ("Empower", "Help students and teams showcase their new skills and confidence."),
    ("Expand",  "Create brand-new STEM opportunities across the state of Arkansas."),
]
_ABOUT_STATS = [
    ("9K+",   "People Reached"),
    ("1.5K+", "Volunteer Hrs / Year"),
    ("100%",  "AR FRC Teams Served"),
    ("29+",   "Arkansas Counties"),
]
# The sponsor marks, left to right, in the order the team asked for. Each is
# (file in assets/Sponsor Logos, the ground its artwork was drawn for). A mark
# is never recoloured or boxed differently to suit the panel; the plate takes
# the ground the artwork expects instead — the Haas file is the dark-ground
# version (its white H vanishes on white), the rest are drawn on white.
SPONSOR_DIR = ("assets", "Sponsor Logos")
_SPONSORS = [
    ("HA_Wildcats_ColorNoStroke.jpg", "light"),
    ("HU Logo.png", "light"),
    ("Copy of dark background image.png", "dark"),
    ("Full_Color_DoWSTEM_Logo.jpg", "light"),
]


_Bold = QFont.Weight.Bold
_Demi = QFont.Weight.DemiBold


class _CardButton(RoundedFrame):
    """A tappable Rounded card. Emits `clicked`; brightens its hairline on
    hover/press — the interaction affordance, not a static Bracket."""

    clicked = pyqtSignal()

    def __init__(self, fill, border, accent, radius=brand.R_CARD, parent=None):
        super().__init__(fill=fill, border=border, radius=radius, parent=parent)
        self._border_rest = border
        self._accent_hex = accent
        self._down = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        self.set_border(self._accent_hex)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.set_border(self._border_rest)
        self._down = False
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._down = True
        self.set_border(self._accent_hex)

    def mouseReleaseEvent(self, e):
        if self._down and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        self._down = False
        # A touchscreen never sends leaveEvent, so without this the accent
        # border stays painted on the last card anyone tapped — the board ends
        # the day covered in them.
        if is_touch(e):
            self.set_border(self._border_rest)


# ── What the panel actually shows ────────────────────────────────────────────
# The five E's, as one rail rather than a paragraph nobody standing up reads.
_MISSION_LINE = " · ".join(name.upper() for name, _ in _ABOUT_ES_DETAIL)

# The law gets the surface's one red, so it is pulled out of the card list by
# id rather than by position — reordering TABS must not move the red.
_ACT_ID = "act472"


def _act_item() -> dict:
    for tab in TABS:
        for item in tab.get("items", []):
            if item["id"] == _ACT_ID:
                return item
    return {"id": _ACT_ID, "name": "Act 472", "blurb": "", "detail": ""}


def _program_items() -> list[tuple[str, dict]]:
    """(tab id, item) for every card in the grid, Act 472 excluded."""
    out = []
    for tab in TABS:
        for item in tab.get("items", []):
            if item["id"] != _ACT_ID:
                out.append((tab["id"], item))
    return out


# ── Editable wording (app/wording.py) ────────────────────────────────────────
# Every word a visitor reads on this panel is a wording field: the text above
# and below is the shipped default, an admin's edit (Control → Screen Wording)
# replaces it live. Keys are fixed; the board looks text up by key at build
# time and again on `config.wording_changed`.
_FRONT = "Front panel"


def _register_words() -> None:
    f = wording.field
    f("board/mission", "Every kid can", "Mission (the big line)", f"{_FRONT} · Top", 24)
    f("board/mission_line", _MISSION_LINE, "Mission rail (small caps)", f"{_FRONT} · Top", 60)
    act = _act_item()
    g = f"{_FRONT} · Act 472 (the red plate)"
    f("board/act/eyebrow", "Advocacy · Arkansas", "Eyebrow", g, 32)
    f("board/act/stamp", "SIGNED  ·  APRIL 2025", "Stamp (top right)", g, 28)
    f("board/act/title", "Act 472", "Headline", g, 16)
    f("board/act/body", "Arkansas's first state funding for competitive robotics teams — "
      "written, argued and passed by this team over 24+ months.", "Sentence", g, 160, True)
    f("board/act/rail", "CAPITOL DAY: 6 FRC + 4 VEX TEAMS · SENATE & HOUSE TESTIMONY · "
      "TAP FOR MORE", "Rail (small caps)", g, 90)
    f("board/act/detail", act.get("detail") or act.get("blurb", ""),
      "Tap-for-more text", g, 1200, True)
    g = f"{_FRONT} · Reach figures"
    for i, (number, caption) in enumerate(_ABOUT_STATS):
        f(f"board/stat/{i}/number", number, f"Figure {i + 1}", g, 7)
        f(f"board/stat/{i}/caption", caption, f"Figure {i + 1} caption", g, 28)
    g = f"{_FRONT} · Headings"
    f("board/programs/title", "What we run", "Program cards heading", g, 24)
    f("board/programs/hint", "TAP ANY CARD", "Program cards hint", g, 20)
    f("board/sponsors", "Sponsors", "Sponsors label", g, 16)
    for tab in TABS:
        f(f"board/tab/{tab['id']}", tab["eyebrow"], f"{tab['tab']}: category on the sheet",
          f"{_FRONT} · Program cards", 32)
        for item in tab.get("items", []):
            if item["id"] == _ACT_ID:
                continue
            g = f"{_FRONT} · Card: {item['name']}"
            f(f"board/card/{item['id']}/name", item["name"], "Name", g, 36)
            f(f"board/card/{item['id']}/blurb", item.get("blurb", ""), "Card text", g,
              max(160, int(len(item.get("blurb", "")) * 1.5)), True)
            f(f"board/card/{item['id']}/detail", item.get("detail") or item.get("blurb", ""),
              "Tap-for-more text", g, 1200, True)


_register_words()


# ── Base type sizes, in design px against the 1080 width ─────────────────────
FS_WORDMARK  = 40
FS_LOCATION  = 18
FS_MISSION   = 19
FS_RAIL      = 17
FS_ACT_EYE   = 19
FS_ACT       = 88
FS_ACT_BODY  = 25
FS_STAT_NUM  = 58
FS_STAT_CAP  = 17
FS_SECTION   = 19
FS_CARD      = 30
FS_CARD_BODY = 21
FS_CHIP_TXT  = 20
FS_HINT      = 22
FS_SHEET_EYE = 22
FS_SHEET_TTL = 54
FS_SHEET_BODY = 26

CAD_STAGE_H  = 648
# 648 of 1920 — the stage's share of the panel. Held as a ratio as well as a
# design px so a panel shorter than the design gives the stage up in the same
# proportion as everything else, instead of pushing the Act plate through the
# subsystem chips.
CAD_STAGE_RATIO = CAD_STAGE_H / 1920.0
SPONSOR_H    = 76


class _SponsorMark(QWidget):
    """
    One sponsor logo, contain-fitted and centred inside its plate.

    Painted rather than a QLabel pixmap so it rescales with the plate and never
    stretches. A file that won't load leaves the plate empty rather than
    showing a filename to the public.
    """

    PAD = 0.14          # of the plate's height, all round

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._pix = QPixmap(str(path))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event):
        if self._pix.isNull():
            return
        r = QRectF(self.rect())
        pad = r.height() * self.PAD
        box = r.adjusted(pad, pad, -pad, -pad)
        if box.width() <= 0 or box.height() <= 0:
            return
        s = min(box.width() / self._pix.width(), box.height() / self._pix.height())
        w, h = self._pix.width() * s, self._pix.height() * s
        target = QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))


class _Band(QFrame):
    """
    A content band closed by a 2px rule — the panel's only separator.

    Five bands, one rule weight, no cards around the whole thing: the order of
    the bands *is* the argument the panel is making, and a stack of framed
    boxes would read as five unrelated widgets instead of one page.
    """

    def __init__(self, rule: str = "top", parent=None):
        super().__init__(parent)
        self.setObjectName("band")
        # The app-wide `QWidget { background-color: … }` rule paints a QFrame,
        # which on the plate's gradient shows up as a flat dark rectangle. Every
        # container that sits *on* the plate has to opt out of it explicitly.
        self.setStyleSheet("QFrame#band { background: transparent; }")
        self._line = brand.CARBON_LINE
        self._rule = rule

    def set_line(self, hex_color: str):
        self._line = hex_color
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._line))
        y = 0 if self._rule == "top" else self.height() - 2
        p.drawRect(QRectF(0, y, self.width(), 2))
        p.end()


class _CADStage(QFrame):
    """
    The robot, always live. A radial-lit well that the web view sits inside.

    Painted rather than styled because the well is a radial gradient and QSS
    has no radial: `qlineargradient` is all Qt's stylesheet syntax offers.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cad_stage")
        self._radius = 14
        self._target = 400
        self._dark = True
        # Same reason as _Band: the QSS ground would square off the corners of
        # the rounded well this paints for itself.
        self.setStyleSheet("QFrame#cad_stage { background: transparent; }")

    def set_radius(self, r: int):
        self._radius = r
        self.update()

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def set_target_height(self, h: int):
        """
        The height the stage *wants*, not the height it insists on.

        The stage is the band that gives. A fixed height meant that on a window
        shorter than the design the layout had nowhere to take the deficit from
        and simply overlapped the Act plate through the subsystem chips. The
        robot is also the most compressible thing here — it is a 3D view that
        reframes itself — while the plate, the figures and the cards each carry
        a sentence that cannot shrink past its own type.
        """
        self._target = max(1, int(h))
        self.setMaximumHeight(self._target)
        self.setMinimumHeight(min(self._target, 120))
        self.updateGeometry()

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(0, self._target)

    def minimumSizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(0, min(self._target, 120))

    def paintEvent(self, _e):
        from PyQt6.QtGui import QRadialGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        grad = QRadialGradient(rect.width() * 0.5, rect.height() * 0.34,
                               max(rect.width() * 1.2, rect.height() * 0.9))
        if self._dark:
            grad.setColorAt(0.0, QColor("#2A262A"))
            grad.setColorAt(0.68, QColor("#151215"))
        else:
            # The same well, lit: a pale dish the light-theme viewer sits in.
            grad.setColorAt(0.0, QColor(brand.WHITE))
            grad.setColorAt(0.68, QColor(brand.N200))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, self._radius, self._radius)

        # The Pocket: engineering scope, at 13% so it marks the stage
        # without becoming a second focal element.
        d = self.width() * 0.048
        p.setBrush(QColor(250, 249, 248, 33) if self._dark
                   else QColor(24, 20, 22, 26))
        p.drawPath(pocket_path(rect.right() - d * 0.9, d * 0.9, d / 2, rot=132))
        p.end()


class _SubsystemChip(SelectableChip):
    """
    A CAD focus chip. Filled when it is the focused subsystem — white on the
    dark plate, carbon on the light one, so "this is the one that is on" is
    the loud neutral either way and never the accent.
    """

    def __init__(self, sub_id: str, text: str):
        super().__init__(text.upper(), radius=brand.R_PILL)
        self.sub_id = sub_id
        self._light = False
        self.setMinimumHeight(56)

    def set_light(self, light: bool):
        self._light = light
        self.update()

    def _colors(self):
        if not self._light:
            return super()._colors()
        if self._active:
            return QColor(brand.CARBON), QColor(brand.WHITE), None
        if self.isDown():
            return QColor(brand.N200), QColor(brand.CARBON), QColor(brand.N400)
        return QColor(0, 0, 0, 0), QColor(brand.N600), QColor(brand.N300)


class InteractiveBoard(PlatePanel):
    """The pit-front panel. Theme + team aware; scales from its own width."""

    SCREEN_ID = "project"

    def __init__(self, parent=None):
        super().__init__(screen_id="project", parent=parent)
        self._scale = 1.0
        self._fonts: list[tuple] = []          # (label, base_px, weight, family, tracking)
        self._roled: list[tuple] = []          # (label, colour role) — re-resolved per theme
        self._cards: list[RoundedFrame] = []
        self._chips: list[_SubsystemChip] = []
        self._sponsor_plates: list[RoundedFrame] = []
        self._sponsor_grounds: dict[RoundedFrame, str] = {}
        self._cad_host: QWidget | None = None
        self._cad_view = None
        self._sheet: "_DetailSheet | None" = None
        self._worded: list[tuple[QLabel, str]] = []   # (label, wording key)

        self._build()
        config.team_changed.connect(self._repaint_on_team)
        config.wording_changed.connect(self._on_wording_changed)
        cad_assets.subsystem_focused.connect(self._on_subsystem_focused)
        cad_assets.config_changed.connect(self._rebuild_chips)

    def _repaint_on_team(self, *_args):
        # A bound method, not a lambda: this widget is destroyed with its
        # screen, and only a QObject method slot is auto-disconnected.
        self.update()

    def _bind(self, lbl: QLabel, key: str) -> QLabel:
        """Show wording field `key` in `lbl`, and keep showing it as admins edit."""
        self._worded.append((lbl, key))
        return lbl

    def _on_wording_changed(self):
        # In place: rebuilding would tear the borrowed CAD viewer out.
        for lbl, key in self._worded:
            lbl.setText(wording.text(key))
        self.updateGeometry()

    # ── Type helpers (registered so everything scales together) ───────────
    # Labels get NO Qt object names: the app QSS role rules carry font sizes,
    # which override setFont() and would freeze the responsive type.

    def _reg(self, lbl: QLabel, base: int, weight, family: str,
             tracking: int = 0, colour: str | None = None) -> QLabel:
        self._fonts.append((lbl, base, weight, family, tracking))
        f = QFont(family)
        f.setPixelSize(max(8, int(base * self._scale)))
        f.setWeight(weight)
        if tracking:
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
        lbl.setFont(f)
        self._paint_label(lbl, colour)
        return lbl

    # Colour on this panel is a *role*, never a literal, so the light theme
    # can re-resolve every label: "ink" / "body" / "muted" / "faint" map onto
    # the chassis inks for the current plate. A literal is still accepted for
    # the one place a fixed colour is right — white type on the red Act plate.
    _ROLES = ("ink", "body", "muted", "faint")

    def _role(self, role: str) -> str:
        return {"ink": self.ink, "body": self.body_ink, "muted": self.muted,
                "faint": self.faint}[role]

    def _paint_label(self, lbl: QLabel, colour: str | None):
        if not colour:
            return
        if colour in self._ROLES:
            self._roled.append((lbl, colour))
            colour = self._role(colour)
        lbl.setStyleSheet(f"color:{colour}; background:transparent;")

    def _disp(self, text, base, weight=_Bold, colour="ink", track=0):
        return self._reg(QLabel(text), base, weight, brand.FONT_DISPLAY,
                         track, colour)

    def _body_lbl(self, text, base=FS_CARD_BODY, colour="muted"):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        return self._reg(lbl, base, QFont.Weight.Normal, brand.FONT_BODY,
                         0, colour)

    def _mono(self, text, base=FS_RAIL, colour="faint", track=112):
        lbl = QLabel(text)
        f = QFont()
        f.setFamilies(brand.FONT_MONO_STACK)
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setWeight(_Demi)
        f.setPixelSize(max(8, int(base * self._scale)))
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, track)
        lbl.setFont(f)
        self._paint_label(lbl, colour)
        self._fonts.append((lbl, base, _Demi, None, track))
        return lbl

    # ── Construction ──────────────────────────────────────────────────────

    def _build(self):
        col = self.content_layout()

        col.addLayout(self._identity_row())
        col.addWidget(self._mission_rail())
        col.addWidget(self._cad_band())
        col.addWidget(self._act_plate())
        col.addLayout(self._stat_row())
        col.addWidget(self._programs_band(), stretch=1)
        # Pinned under the scroll, not in it: the sponsors are on the panel
        # whatever a visitor has scrolled to.
        self.footer_layout().addWidget(self._sponsor_strip())

    def _identity_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(20)
        # The identity is the type, not artwork: a white wordmark plate would
        # punch a hole in the plate's surface and spend budget the Act needs.
        name = QLabel()
        name.setTextFormat(Qt.TextFormat.RichText)
        self._wordmark = self._reg(name, FS_WORDMARK, _Bold,
                                   brand.FONT_DISPLAY, 102)
        self._refresh_wordmark()
        row.addWidget(name)
        row.addStretch(1)
        team = config.active_team
        where = (team.location or "").upper()
        self._location = self._mono(
            f"{where}  /  SINCE 2011" if where else "SINCE 2011",
            FS_LOCATION)
        row.addWidget(self._location, alignment=Qt.AlignmentFlag.AlignBottom)
        return row

    def _refresh_wordmark(self):
        team = config.active_team
        self._wordmark.setText(
            f'<span style="color:{self.ink}">{(team.name or "Team").upper()}</span>'
            f'<span style="color:{self.faint}"> {team.number}</span>')

    def _mission_rail(self) -> _Band:
        host = _Band(rule="bottom")
        self._mission_band = host
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 18)
        row.setSpacing(22)
        self._mission_lbl = self._bind(self._disp(wording.text("board/mission"), FS_MISSION,
                                                  _Demi, "ink", track=120), "board/mission")
        row.addWidget(self._mission_lbl)
        row.addStretch(1)
        row.addWidget(self._bind(self._mono(wording.text("board/mission_line"), FS_RAIL),
                                 "board/mission_line"))
        return host

    def _cad_band(self) -> QWidget:
        self._stage = _CADStage()
        self._stage.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(self._stage)
        lay.setContentsMargins(30, 28, 30, 26)
        lay.setSpacing(0)

        top = QHBoxLayout()
        self._stage_label = self._mono("LIVE CAD  /  robot.glb", FS_RAIL)
        top.addWidget(self._stage_label)
        top.addStretch(1)
        lay.addLayout(top)

        # The web view lives here once ProjectScreen lends it to us.
        self._cad_host = QWidget()
        self._cad_host.setObjectName("cad_host")
        self._cad_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # A QWebEngineView paints white until its page has loaded, which on a
        # near-black panel reads as a fault. Give the well its own dark ground
        # so the load is invisible.
        self._cad_host.setStyleSheet(
            "QWidget#cad_host { background: transparent; }")
        host_lay = QVBoxLayout(self._cad_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        self._cad_placeholder = self._mono(
            "THREE.JS STAGE  ·  NO MODEL UPLOADED", FS_RAIL, "faint")
        self._cad_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        host_lay.addWidget(self._cad_placeholder)
        lay.addWidget(self._cad_host, stretch=1)

        # The caption is the one thing focusing a subsystem changes.
        self._cad_hint = self._body_lbl(
            "Drag to orbit · pinch to zoom · tap a subsystem to isolate it.",
            FS_HINT, "muted")
        lay.addWidget(self._cad_hint)
        lay.addSpacing(12)

        self._chip_row = QHBoxLayout()
        self._chip_row.setSpacing(10)
        lay.addLayout(self._chip_row)
        self._rebuild_chips()
        return self._stage

    def _rebuild_chips(self):
        while self._chip_row.count():
            item = self._chip_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips.clear()

        entries = [("", "Whole robot")]
        try:
            entries += [(s["id"], s.get("name", s["id"]))
                        for s in cad_assets.load_config().get("subsystems", [])
                        if s.get("id")]
        except Exception:
            # A malformed subsystems.json must leave the visitor a working
            # robot, not an empty floor.
            pass
        for sub_id, name in entries:
            chip = _SubsystemChip(sub_id, name)
            chip.set_light(not self.dark)
            chip.clicked.connect(lambda _c=False, s=sub_id: self._focus(s))
            self._chips.append(chip)
            self._chip_row.addWidget(chip)
        self._chip_row.addStretch(1)
        self._sync_chip_state()
        self._apply_scale()

    def _focus(self, sub_id: str):
        cad_assets.focus_subsystem(sub_id or "")

    def _on_subsystem_focused(self, sub_id: str):
        self._sync_chip_state()
        name = next((c.text().title() for c in self._chips
                     if c.sub_id == sub_id), "")
        self._cad_hint.setText(
            f"{name} isolated — tap Whole robot to bring the rest back."
            if sub_id else
            "Drag to orbit · pinch to zoom · tap a subsystem to isolate it.")

    def _sync_chip_state(self):
        focused = getattr(cad_assets, "focused_id", "") or ""
        for chip in self._chips:
            chip.set_active(chip.sub_id == focused)

    def _act_plate(self) -> QWidget:
        w, b = wording.text, self._bind
        # The surface's one red: a filled field with white type. Never red
        # letterforms — red on carbon is 2.8:1 and forbidden for text.
        plate = _CardButton(fill=brand.RED, border=None, accent=brand.RED,
                            radius=brand.R_CARD)
        plate.clicked.connect(self._open_act)
        lay = QVBoxLayout(plate)
        lay.setContentsMargins(34, 30, 34, 30)
        lay.setSpacing(14)

        top = QHBoxLayout()
        top.addWidget(b(self._disp(w("board/act/eyebrow"), FS_ACT_EYE, _Demi,
                                   "rgba(255,255,255,0.82)", track=120), "board/act/eyebrow"))
        top.addStretch(1)
        top.addWidget(b(self._mono(w("board/act/stamp"), FS_RAIL,
                                   "rgba(255,255,255,0.82)"), "board/act/stamp"))
        lay.addLayout(top)

        lay.addWidget(b(self._disp(w("board/act/title"), FS_ACT, _Bold, brand.WHITE,
                                   track=98), "board/act/title"))
        lay.addWidget(b(self._body_lbl(w("board/act/body"), FS_ACT_BODY, brand.WHITE),
                        "board/act/body"))
        rail = b(self._mono(w("board/act/rail"), FS_RAIL, "rgba(255,255,255,0.82)",
                            track=106), "board/act/rail")
        rail.setWordWrap(True)       # an edited rail may run longer than the shipped one
        lay.addWidget(rail)
        return plate

    def _open_act(self):
        # Read at tap time, so an edit made since the board was built shows.
        self._open_sheet(wording.text("board/act/eyebrow"), wording.text("board/act/title"),
                         wording.text("board/act/detail"))

    def _stat_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        for i, _stat in enumerate(_ABOUT_STATS):
            tile = RoundedFrame(fill=self.tile, border=self.rule,
                                radius=brand.R_CARD)
            self._cards.append(tile)
            v = QVBoxLayout(tile)
            v.setContentsMargins(22, 20, 22, 20)
            v.setSpacing(10)
            v.addWidget(self._bind(self._disp(wording.text(f"board/stat/{i}/number"),
                                              FS_STAT_NUM, _Bold, "ink", track=99),
                                   f"board/stat/{i}/number"))
            cap = self._bind(self._disp(wording.text(f"board/stat/{i}/caption"), FS_STAT_CAP,
                                        _Demi, "muted", track=114), f"board/stat/{i}/caption")
            # "Volunteer Hrs / Year" does not fit a quarter of a 768px panel on
            # one line, and an unwrapped caption is simply cut in half.
            cap.setWordWrap(True)
            cap.setSizePolicy(QSizePolicy.Policy.Ignored,
                              QSizePolicy.Policy.Preferred)
            cap.setMinimumWidth(0)
            v.addWidget(cap)
            row.addWidget(tile)
        return row

    def _programs_band(self) -> QWidget:
        band = _Band()
        self._band = band
        lay = QVBoxLayout(band)
        lay.setContentsMargins(0, 20, 0, 0)
        lay.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(self._bind(self._disp(wording.text("board/programs/title"), FS_SECTION,
                                             _Demi, "ink", track=120), "board/programs/title"))
        head.addStretch(1)
        head.addWidget(self._bind(self._mono(wording.text("board/programs/hint"), FS_RAIL),
                                  "board/programs/hint"))
        lay.addLayout(head)

        # Every card is here, two across. The comp shows four at rest; the rest
        # are a drag away rather than deleted, because they are real programs
        # the team runs and a kiosk that hides them is lying by omission.
        # Every card, two across, at full height. The grid used to be the one
        # thing on the panel that scrolled; now the whole plate does
        # (`PlatePanel`), so the grid simply takes the room it needs.
        inner = QWidget()
        inner.setObjectName("program_grid")
        inner.setStyleSheet("QWidget#program_grid { background: transparent; }")
        grid = QGridLayout(inner)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(14)
        for i, (tab_id, item) in enumerate(_program_items()):
            grid.addWidget(self._program_card(tab_id, item), i // 2, i % 2)
        lay.addWidget(inner)
        return band

    def _program_card(self, tab_id: str, item: dict) -> QWidget:
        card = _CardButton(fill=self.tile, border=self.rule,
                           accent=brand.N400, radius=brand.R_CARD)
        self._cards.append(card)
        key = f"board/card/{item['id']}"
        # The card's own signal, so the lambda dies with the card; the words
        # are read at tap time so an edit since the build shows.
        card.clicked.connect(
            lambda: self._open_sheet(wording.text(f"board/tab/{tab_id}"),
                                     wording.text(f"{key}/name"),
                                     wording.text(f"{key}/detail")))
        v = QVBoxLayout(card)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(10)
        name = self._bind(self._disp(wording.text(f"{key}/name"), FS_CARD, _Bold, "ink"),
                          f"{key}/name")
        name.setWordWrap(True)
        v.addWidget(name)
        v.addWidget(self._bind(self._body_lbl(wording.text(f"{key}/blurb"), FS_CARD_BODY),
                               f"{key}/blurb"))
        v.addStretch(1)
        return card

    def _sponsor_strip(self) -> QWidget:
        band = _Band()
        self._sponsor_band = band
        row = QHBoxLayout(band)
        row.setContentsMargins(0, 18, 0, 0)
        row.setSpacing(18)
        row.addWidget(self._bind(self._disp(wording.text("board/sponsors"), FS_STAT_CAP, _Demi,
                                            "faint", track=120), "board/sponsors"))
        for filename, ground in _SPONSORS:
            plate = RoundedFrame(fill=brand.WHITE, border=None,
                                 radius=brand.R_MEDIA)
            plate.setFixedHeight(SPONSOR_H)
            self._sponsor_plates.append(plate)
            self._sponsor_grounds[plate] = ground
            self._style_sponsor_plate(plate)
            self._cards.append(plate)
            inner = QVBoxLayout(plate)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.addWidget(_SponsorMark(paths.resource(*SPONSOR_DIR, filename)))
            row.addWidget(plate, stretch=1)
        return band

    def _style_sponsor_plate(self, plate) -> None:
        """
        White under a light-ground mark, carbon under a dark-ground one, on
        either theme: the JPEGs carry their own white, so any other light
        fill would show as a box around them. A plate only needs an edge when
        it matches the panel around it.
        """
        if self._sponsor_grounds.get(plate) == "dark":
            plate.set_fill(brand.CARBON)
            plate.set_border(brand.CARBON_LINE if self.dark else None)
        else:
            plate.set_fill(brand.WHITE)
            plate.set_border(None if self.dark else brand.N200)

    # ── The detail sheet ──────────────────────────────────────────────────

    def _open_sheet(self, category: str, title: str, body: str):
        if self._sheet is not None:
            self._sheet.close_sheet()
        self._sheet = _DetailSheet(self, category, title, body, self._scale)
        self._sheet.closed.connect(self._on_sheet_closed)
        self._sheet.open_sheet()

    def _on_sheet_closed(self):
        self._sheet = None

    # ── CAD hand-off ──────────────────────────────────────────────────────

    def attach_cad(self, view) -> None:
        """
        Take the project screen's CAD viewer into the stage.

        Lent, not owned: the same widget is handed back for the full-screen CAD
        content mode, so the pit never runs two Chromium scenes for one robot.
        """
        if view is self._cad_view:
            return
        self._cad_view = view
        self._cad_placeholder.setVisible(False)
        self._cad_host.layout().addWidget(view)
        view.setVisible(True)

    def detach_cad(self):
        if self._cad_view is None:
            return
        self._cad_host.layout().removeWidget(self._cad_view)
        self._cad_view = None
        self._cad_placeholder.setVisible(True)

    # ── Theme / scale ─────────────────────────────────────────────────────

    def apply_theme(self, theme: str):
        """
        Re-resolve every colour on the panel for `theme`.

        Everything here was once a literal — white type, carbon tiles — which
        on the light plate left white words on a white ground. Labels carry a
        role, tiles and cards take their fill from the chassis, the chips and
        the CAD well and the sheet each know which plate they are on.
        """
        self.set_theme(theme)
        line = self.rule
        for band in (getattr(self, "_band", None),
                     getattr(self, "_mission_band", None),
                     getattr(self, "_sponsor_band", None)):
            if band is not None:
                band.set_line(line)
        for lbl, role in self._roled:
            lbl.setStyleSheet(f"color:{self._role(role)}; background:transparent;")
        if hasattr(self, "_wordmark"):
            self._refresh_wordmark()
        for card in self._cards:
            if card in self._sponsor_plates:
                self._style_sponsor_plate(card)
                continue
            if isinstance(card, _CardButton) and card._accent_hex == brand.RED:
                continue                    # the Act plate is red on both
            card.set_fill(self.tile)
            card.set_border(line)
            if isinstance(card, _CardButton):
                card._border_rest = line
        for chip in self._chips:
            chip.set_light(not self.dark)
        if hasattr(self, "_stage"):
            self._stage.set_dark(self.dark)
        if self._sheet is not None:
            self._sheet.apply_theme(self.dark)
        self.update()

    def apply_team(self, hex_color: str):
        self._refresh_wordmark()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scale()

    def _apply_scale(self):
        """
        Everything is sized from the panel, never from a fixed px.

        The scale is the chassis' contain fit, not `width / 1080`: this panel
        is portrait, and a window that opens wide and short would otherwise set
        every figure at nearly twice the size it has room for.
        """
        scale = self.scale()
        self._scale = scale
        for lbl, base, weight, family, tracking in self._fonts:
            f = QFont(family) if family else QFont()
            if not family:
                f.setFamilies(brand.FONT_MONO_STACK)
                f.setStyleHint(QFont.StyleHint.Monospace)
            f.setPixelSize(max(8, int(base * scale)))
            f.setWeight(weight)
            if tracking:
                f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
            lbl.setFont(f)
        if hasattr(self, "_stage"):
            # The plate scrolls, so the stage no longer has to give: it is
            # its design height at this scale, and the page grows below it.
            self._stage.set_target_height(max(140, int(CAD_STAGE_H * scale)))
            self._stage.set_radius(int(14 * scale))
        for plate in self._sponsor_plates:
            plate.setFixedHeight(max(28, int(SPONSOR_H * scale)))
        for chip in self._chips:
            chip.setMinimumHeight(int(56 * scale))
            cf = chip.font()
            cf.setPixelSize(max(8, int(FS_CHIP_TXT * scale)))
            chip.setFont(cf)
        if self._sheet is not None:
            self._sheet.rescale(scale)


class _DetailSheet(QFrame):
    """
    A tapped card, deepened — raised over the lower two thirds of the panel.

    **It never replaces the stage.** The robot stays visible above it, which is
    the whole reason this is a sheet and not a page: a visitor who taps a card
    has not asked to stop looking at the robot.
    """

    closed = pyqtSignal()

    _RISE_MS = 340
    _COVER = 2 / 3

    def __init__(self, parent: "InteractiveBoard", category: str, title: str,
                 body: str, scale: float):
        super().__init__(parent)
        self._board = parent
        self._scale = scale
        self.setObjectName("sheet")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 34, 40, 34)
        lay.setSpacing(16)

        head = QHBoxLayout()
        self._eye = QLabel(category.upper())
        head.addWidget(self._eye)
        head.addStretch(1)
        self._close = RoundedButton("Close", variant="secondary",
                                    radius=brand.R_PILL)
        self._close.clicked.connect(self.close_sheet)
        head.addWidget(self._close)
        lay.addLayout(head)

        self._title = QLabel(title)
        self._title.setWordWrap(True)
        lay.addWidget(self._title)

        self._body = QLabel(body)
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.apply_theme(parent.dark)
        scroll = QScrollArea()
        scroll.setObjectName("sheet_scroll")
        scroll.setWidget(self._body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Both the area and its viewport, or the light QSS paints a grey box
        # behind the body text.
        scroll.setStyleSheet(
            "QScrollArea#sheet_scroll { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setStyleSheet("background: transparent;")
        lay.addWidget(scroll, stretch=1)

        self.rescale(scale)
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(self._RISE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def apply_theme(self, dark: bool):
        """The sheet is a raised surface on the plate: one step lighter than
        the plate on dark, white with a hairline on light."""
        fill = brand.CARBON_SURF if dark else brand.WHITE
        line = brand.CARBON_LINE if dark else brand.N200
        self.setStyleSheet(
            f"QFrame#sheet {{ background-color: {fill};"
            f" border-top-left-radius: {brand.R_BANNER}px;"
            f" border-top-right-radius: {brand.R_BANNER}px;"
            f" border: 1.5px solid {line}; }}")
        self._eye.setStyleSheet(
            f"color:{brand.N400 if dark else brand.N500}; background:transparent;")
        self._title.setStyleSheet(
            f"color:{brand.WHITE if dark else brand.CARBON}; background:transparent;")
        self._body.setStyleSheet(
            f"color:{brand.N300 if dark else brand.N600}; background:transparent;")
        self._close.set_on_light(not dark)

    def rescale(self, scale: float):
        self._scale = scale
        for lbl, px, weight, family in (
                (self._eye, FS_SHEET_EYE, _Demi, brand.FONT_DISPLAY),
                (self._title, FS_SHEET_TTL, _Bold, brand.FONT_DISPLAY),
                (self._body, FS_SHEET_BODY, QFont.Weight.Normal,
                 brand.FONT_BODY)):
            f = QFont(family)
            f.setPixelSize(max(8, int(px * scale)))
            f.setWeight(weight)
            if lbl is self._eye:
                f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118)
            lbl.setFont(f)
        cf = self._close.font()
        cf.setPixelSize(max(8, int(20 * scale)))
        self._close.setFont(cf)
        self._close.setMinimumHeight(int(52 * scale))

    def _target(self) -> QRect:
        h = int(self._board.height() * self._COVER)
        return QRect(0, self._board.height() - h, self._board.width(), h)

    def open_sheet(self):
        target = self._target()
        self.setGeometry(QRect(target.x(), self._board.height(),
                               target.width(), target.height()))
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    def close_sheet(self):
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(QRect(0, self._board.height(),
                                     self.width(), self.height()))
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        self._anim.finished.connect(self._finish_close)
        self._anim.start()

    def _finish_close(self):
        self.closed.emit()
        self.hide()
        self.deleteLater()
