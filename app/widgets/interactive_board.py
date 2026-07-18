"""
Interactive Impact Board — the touch kiosk board for the project screen.

Designed for a 32" monitor mounted in PORTRAIT (tall) orientation, e.g.
1080×1920. Layout is a single scrolling column under a horizontal tab bar,
with a persistent sponsors strip at the bottom. Type scales with the screen
width (see `resizeEvent` / `_apply_scale`) so it stays legible from across a
pit at any resolution.

Interactivity:
  • Horizontal tab bar switches sections.
  • Every program / award card is tappable and opens a full detail view with
    a "‹ Back" control — built for touch.

Content is real (synthesized from the team's FIRST Impact documents). Edit the
`TABS` list to reshape it. Theme + team aware: apply_theme("dark"|"light")
recolors the Cut cards; apply_team(hex) re-accents tabs, headers, and figures.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QStackedWidget, QScrollArea, QSizePolicy, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics

from app import brand
from app.config import config
from app.widgets.brand_widgets import ChamferFrame, ChamferButton, BreakLine


# ── Content model ─────────────────────────────────────────────────────────────
# Each tab has an id, a short tab-bar label, an eyebrow, a title, and a kind.
# "cards" tabs list items; each item = {name, blurb, optional detail, optional
# subs}. `detail` is the longer text shown when the card is tapped (falls back
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
            {"name": "The Bill — Act 472",
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
            {"name": "Museum of Discovery",
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
            {"name": "Girls in STEM Camp",
             "blurb": "For 2 years, team members have served as guest mentors at "
                      "the Museum of Discovery's Girls in STEM Camp, helping girls "
                      "see themselves in STEM."},
            # NOTE: "Tinkerfest" in the team outline = MoD's original in Little Rock.
            {"name": "Tinkerfest (Little Rock)",
             "blurb": "For 10+ years we've run a LEGO robotics booth at MoD's "
                      "Tinkerfest STEM festival (2,500+ visitors/year), plus BIT "
                      "Kits and QR-linked at-home activities — 600+ kits since 2024."},
            # NOTE: read the outline's "Circe Tinkerfest" as SEARCY Tinkerfest — confirm.
            {"name": "Searcy Tinkerfest",
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
            {"name": "Foster Care",
             "blurb": "Since 2024 we've partnered with 2 local foster-care "
                      "organizations, providing 100+ dual-activity BIT Kits to "
                      "visit centers so parents and children can build together, "
                      "plus fundraising help and a summer STEM session."},
            {"name": "FIRST in Arkansas",
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
            {"name": "LEGO Club",
             "blurb": "Our 10-week LEGO Club (since 2016) teaches K–6 students to "
                      "design, build, and code LEGO Education robots — 240+ "
                      "students over the last 3 years."},
            {"name": "LEGO Camp",
             "blurb": "A one-week LEGO summer camp we've run with our school for "
                      "10+ years, introducing young students to robotics."},
            {"name": "Downtown Discovery Camp",
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
            {"name": "Executive Summaries",
             "blurb": "Our answers to the 13 FIRST Impact Award questions — the "
                      "data and stories behind our reach: 9K+ served, 100% of AR "
                      "FRC teams, Act 472, and more."},
            {"name": "Impact Essay",
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
            {"name": "Woodie Flowers Essay",
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
            {"name": "KPIs",
             "blurb": "We're developing Key Performance Indicators to pair "
                      "qualitative relationship data with our quantitative reach, "
                      "adding dimension to the full scope of our impact."},
        ],
    },
]

# ── About tab specifics ───────────────────────────────────────────────────────
_ABOUT_MISSION = (
    "Since 2011, FRC Team 3937 Breakaway has grown a single goal — build a "
    "competitive robot — into a statewide mission that “Every Kid Can” "
    "succeed in STEM. We organize all of our outreach around five E’s of "
    "Opportunity:"
)
# The "E's" from the team outline = Breakaway's 5 E's of Opportunity.
_ABOUT_ES = ["Excite", "Engage", "Equip", "Empower", "Expand"]
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
_ABOUT_STRATEGIC = (
    "Every initiative lives in our annual budget and a strategic plan we review "
    "every 3 years to ensure sustainability. Department chairs and returning "
    "members mentor new members, and we’re building a resource library of "
    "handbooks and training videos."
)
_DEPARTMENTS = [
    "Machining & Assembly", "Programming", "CAD", "Electromatics",
    "Business", "Impact", "Multi-Media Production",
]
# TODO(team): swap in real sponsor logos / names.
_SPONSORS = ["Sponsor", "Sponsor", "Sponsor", "Sponsor"]


# ── Base type sizes (px @ 1080-wide baseline; scaled by screen width) ─────────
FS_WORDMARK   = 38
FS_EYEBROW    = 19
FS_PAGE_TITLE = 48
FS_SECTION    = 30
FS_CARD_TITLE = 32
FS_BODY       = 21
FS_STAT_LABEL = 22
# Stat tiles are the board's key points, so they're hero-scaled: the tile owns a
# fixed height (scaled with the screen) and the number auto-fits to fill it —
# see `_StatNumber` / `_stat_tile`. This keeps the figures dominant and uniform
# across the four tiles at any resolution.
STAT_TILE_H   = 200   # base tile height @1080 baseline
STAT_NUM_RATIO = 0.52  # figure pixel size as a fraction of tile height
FS_DEPT       = 25
FS_TAB        = 22
FS_BACK       = 24
FS_CHIP       = 16
FS_ETAG       = 18
FS_HINT       = 18

_Bold = QFont.Weight.Bold
_Demi = QFont.Weight.DemiBold


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class _Chip(QLabel):
    """A small chamfered tag — used for the E's and FIRST-in-AR sub-sections."""

    def __init__(self, text: str, accent: str, px: int = FS_CHIP):
        super().__init__(text.upper())
        self._accent = accent
        self._px = px
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContentsMargins(14, 7, 14, 7)
        self.set_px(px)
        self.set_accent(accent)

    def set_px(self, px: int):
        self._px = px
        f = QFont(brand.FONT_DISPLAY)
        f.setPixelSize(px)
        f.setWeight(_Demi)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
        self.setFont(f)

    def set_accent(self, accent: str):
        self._accent = accent
        r, g, b = _rgb(accent)
        self.setStyleSheet(
            f"color:{accent}; background:rgba({r},{g},{b},0.12); border-radius:5px;"
        )


class _CardButton(ChamferFrame):
    """A tappable Cut card. Emits `clicked`; brightens its border on hover/press."""

    clicked = pyqtSignal()

    def __init__(self, fill, border, accent, cut=brand.CUT_MEDIUM, parent=None):
        super().__init__(fill=fill, border=border, cut=cut,
                         accent_edge=accent, parent=parent)
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


class _StatNumber(QLabel):
    """A hero stat figure whose size is driven explicitly by `set_px`.

    The board sets the pixel size deterministically from the tile height (see
    `_apply_scale`) so the figure always scales with the screen — it never
    depends on the label's own allocated height, which the layout can squeeze.
    Width is only used as a gentle guard: a long figure (e.g. ``1.5K+``) may be
    nudged down to fit a settled width, but never below half the hero size, so
    it can't collapse to a tiny label the way an unbounded fit-to-width would.
    """

    def __init__(self, text: str, family: str):
        super().__init__(text)
        self._family = family
        self._px = 40
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def _font_at(self, px: int) -> QFont:
        f = QFont(self._family)
        f.setWeight(_Bold)
        f.setPixelSize(max(10, px))
        return f

    def set_px(self, px: int):
        self._px = px
        self._apply()

    def setText(self, text: str):
        super().setText(text)
        self._apply()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()

    def _apply(self):
        px = self._px
        w = self.width()
        # Only shrink on a real, settled width, and never past half the hero
        # size — a transient/degenerate layout pass must not collapse the figure.
        if w > 60:
            floor = max(24, px // 2)
            while px > floor and \
                    QFontMetrics(self._font_at(px)).horizontalAdvance(self.text()) > w:
                px -= 2
        self.setFont(self._font_at(px))


# ── The board ─────────────────────────────────────────────────────────────────

class InteractiveBoard(QWidget):
    """Portrait tabbed impact board for the project screen. Theme + team aware."""

    _BASE_W = 1080

    def __init__(self, parent=None):
        super().__init__(parent)
        self._accent = config.active_team.primary_color
        self._pal = brand.palette(config.screen_theme("project"))
        self._scale = 1.0

        # Tracked for live re-theming / re-branding / re-scaling.
        self._cards: list[ChamferFrame] = []
        self._blocks: list[tuple[ChamferFrame, int]] = []   # (frame, base_height)
        self._stat_tiles: list[tuple[ChamferFrame, "_StatNumber", int]] = []
        self._accent_edges: list[ChamferFrame] = []
        self._tab_buttons: list[ChamferButton] = []
        self._accent_labels: list[QLabel] = []
        self._chips: list[_Chip] = []
        self._fonts: list[tuple] = []   # (label, base_px, weight, family, tracking)
        self._detail_widget: QWidget | None = None
        # List lengths captured when a detail view opens, so its registrations
        # can be dropped again when it closes (see _discard_detail).
        self._detail_marks = (0, 0, 0, 0)

        self._build()
        self._select_tab(0)

        config.team_changed.connect(lambda t: self.apply_team(t.primary_color))

    # ── Text factories (registered so they scale with the screen) ─────────

    def _reg(self, lbl: QLabel, base: int, weight, family: str, tracking: int = 0):
        self._fonts.append((lbl, base, weight, family, tracking))
        f = QFont(family)
        f.setPixelSize(int(base * self._scale))
        f.setWeight(weight)
        if tracking:
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
        lbl.setFont(f)
        return lbl

    def _disp(self, text: str, base: int, weight=_Bold, obj: str = "") -> QLabel:
        lbl = QLabel(text)
        if obj:
            lbl.setObjectName(obj)
        return self._reg(lbl, base, weight, brand.FONT_DISPLAY)

    def _body(self, text: str, base: int = FS_BODY) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("stat_label")
        lbl.setWordWrap(True)
        return self._reg(lbl, base, QFont.Weight.Normal, brand.FONT_BODY)

    def _eyebrow(self, text: str, base: int = FS_EYEBROW) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(f"color:{self._accent}; background:transparent;")
        self._accent_labels.append(lbl)
        return self._reg(lbl, base, _Demi, brand.FONT_DISPLAY, tracking=118)

    # ── Construction ──────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._header())

        # Nav row toggles between the tab bar and the detail "back" bar.
        self._nav = QStackedWidget()
        self._nav.addWidget(self._tab_bar())     # 0
        self._nav.addWidget(self._back_bar())    # 1
        self._nav.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self._nav)

        # Center toggles between the tab pages and a detail view.
        self._center = QStackedWidget()
        self._pages = QStackedWidget()
        for tab in TABS:
            self._pages.addWidget(self._page(tab))
        self._center.addWidget(self._pages)      # 0
        root.addWidget(self._center, stretch=1)

        root.addWidget(self._sponsors_footer())

    def _header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(88)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(30, 0, 30, 0)
        lay.setSpacing(12)

        bw = self._disp("BREAKAWAY", FS_WORDMARK)
        self._accent_labels.append(bw)
        bw.setStyleSheet(f"color:{self._accent}; background:transparent;")
        num = self._disp("3937", FS_WORDMARK, obj="screen_title")
        lay.addWidget(bw)
        lay.addWidget(num)
        lay.addStretch()

        self._header_line = BreakLine(color=self._accent, diameter=34)
        self._header_line.setFixedWidth(170)
        lay.addWidget(self._header_line, alignment=Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _tab_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 6, 20, 10)
        lay.setSpacing(8)
        for i, tab in enumerate(TABS):
            btn = ChamferButton(tab["tab"], variant="ghost",
                                accent=self._accent, cut=brand.CUT_SMALL)
            btn.setMinimumHeight(64)
            f = QFont(brand.FONT_DISPLAY); f.setPixelSize(FS_TAB); f.setWeight(_Demi)
            btn.setFont(f)
            btn.clicked.connect(lambda _=False, idx=i: self._select_tab(idx))
            self._tab_buttons.append(btn)
            lay.addWidget(btn, stretch=1)
        return bar

    def _back_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 6, 20, 10)
        lay.setSpacing(12)
        self._back_btn = ChamferButton("‹  Back", variant="secondary",
                                       accent=self._accent, cut=brand.CUT_SMALL)
        self._back_btn.setMinimumHeight(64)
        self._back_btn.setFixedWidth(200)
        f = QFont(brand.FONT_DISPLAY); f.setPixelSize(FS_BACK); f.setWeight(_Demi)
        self._back_btn.setFont(f)
        self._back_btn.clicked.connect(self._close_detail)
        lay.addWidget(self._back_btn)
        self._back_title = self._disp("", FS_SECTION, obj="screen_title")
        lay.addWidget(self._back_title, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch()
        return bar

    # ── Pages ─────────────────────────────────────────────────────────────

    def _scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(30, 22, 30, 26)
        col.setSpacing(16)
        scroll.setWidget(inner)
        return scroll, col

    def _page(self, tab: dict) -> QWidget:
        scroll, col = self._scroll_page()
        col.addWidget(self._eyebrow(tab["eyebrow"]))
        col.addWidget(self._disp(tab["title"], FS_PAGE_TITLE, obj="screen_title"))

        if tab["kind"] == "about":
            self._build_about(col)
        else:
            hint = self._body("Tap any card to learn more  ›", FS_HINT)
            hint.setStyleSheet(f"color:{self._accent}; background:transparent;")
            self._accent_labels.append(hint)
            col.addWidget(hint)
            col.addSpacing(2)
            self._build_cards(col, tab["items"], tab["title"])

        col.addStretch()
        return scroll

    def _build_about(self, col: QVBoxLayout):
        # Hero image — a team/pit photo anchors the page (drop a real image here).
        col.addWidget(self._image_block(300))

        # "Every Kid Can" mission card with the 5 E's
        mission = self._card(accent_edge=True)
        m = QVBoxLayout(mission)
        m.setContentsMargins(26, 24, 26, 24)
        m.setSpacing(12)
        m.addWidget(self._disp("Every Kid Can", FS_CARD_TITLE, obj="screen_title"))
        m.addWidget(self._body(_ABOUT_MISSION))

        es_row = QHBoxLayout()
        es_row.setSpacing(8)
        for e in _ABOUT_ES:
            chip = _Chip(e, self._accent, int(FS_CHIP * self._scale))
            self._chips.append(chip)
            es_row.addWidget(chip)
        es_row.addStretch()
        m.addLayout(es_row)

        for name, detail in _ABOUT_ES_DETAIL:
            row = QHBoxLayout()
            row.setSpacing(12)
            tag = self._disp(name.upper(), FS_ETAG, weight=_Demi)
            tag.setMinimumWidth(120)
            tag.setStyleSheet(f"color:{self._accent}; background:transparent;")
            self._accent_labels.append(tag)
            row.addWidget(tag, alignment=Qt.AlignmentFlag.AlignTop)
            row.addWidget(self._body(detail), stretch=1)
            m.addLayout(row)
        col.addWidget(mission)

        # Quick stats — 2×2 grid (portrait-friendly)
        stat_grid = QGridLayout()
        stat_grid.setSpacing(12)
        for i, (number, label) in enumerate(_ABOUT_STATS):
            stat_grid.addWidget(self._stat_tile(number, label), i // 2, i % 2)
        col.addLayout(stat_grid)

        # Departments — 2-column grid
        col.addWidget(self._disp("Departments", FS_SECTION, obj="screen_title"))
        dept_grid = QGridLayout()
        dept_grid.setSpacing(10)
        for i, name in enumerate(_DEPARTMENTS):
            card = self._card()
            c = QVBoxLayout(card)
            c.setContentsMargins(18, 16, 18, 16)
            c.addWidget(self._disp(name, FS_DEPT, weight=_Demi, obj="stat_value"))
            dept_grid.addWidget(card, i // 2, i % 2)
        col.addLayout(dept_grid)

        # Strategic plan
        col.addWidget(self._disp("Strategic Plan", FS_SECTION, obj="screen_title"))
        plan = self._card()
        p = QVBoxLayout(plan)
        p.setContentsMargins(26, 20, 26, 20)
        p.addWidget(self._body(_ABOUT_STRATEGIC))
        col.addWidget(plan)

    def _build_cards(self, col: QVBoxLayout, items: list[dict], category: str):
        for item in items:
            col.addWidget(self._item_card(item, category))

    def _item_card(self, item: dict, category: str) -> _CardButton:
        card = _CardButton(self._pal["surface"], self._pal["line"], self._accent)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._cards.append(card)
        self._accent_edges.append(card)

        c = QVBoxLayout(card)
        c.setContentsMargins(24, 22, 24, 20)
        c.setSpacing(12)

        top = QHBoxLayout()
        top.addWidget(self._disp(item["name"], FS_CARD_TITLE, obj="screen_title"),
                      stretch=1)
        view = self._disp("View  ›", FS_ETAG, weight=_Demi)
        view.setStyleSheet(f"color:{self._accent}; background:transparent;")
        self._accent_labels.append(view)
        top.addWidget(view, alignment=Qt.AlignmentFlag.AlignVCenter)
        c.addLayout(top)

        c.addWidget(self._image_block(150))
        c.addWidget(self._body(item.get("blurb", "")))

        subs = item.get("subs")
        if subs:
            row = QHBoxLayout()
            row.setSpacing(8)
            for s in subs:
                chip = _Chip(s, self._accent, int(FS_CHIP * self._scale))
                self._chips.append(chip)
                row.addWidget(chip)
            row.addStretch()
            c.addLayout(row)

        card.clicked.connect(lambda it=item, cat=category: self._open_detail(it, cat))
        return card

    # ── Detail view (tap-through) ─────────────────────────────────────────

    def _open_detail(self, item: dict, category: str):
        self._discard_detail()
        self._detail_marks = (
            len(self._fonts), len(self._chips),
            len(self._accent_labels), len(self._blocks),
        )
        scroll, col = self._scroll_page()
        col.addWidget(self._eyebrow(category))
        col.addWidget(self._disp(item["name"], FS_PAGE_TITLE, obj="screen_title"))
        col.addWidget(self._image_block(300))
        col.addWidget(self._body(item.get("detail", item.get("blurb", "")),
                                 FS_BODY + 2))
        subs = item.get("subs")
        if subs:
            row = QHBoxLayout()
            row.setSpacing(8)
            for s in subs:
                chip = _Chip(s, self._accent, int(FS_CHIP * self._scale))
                self._chips.append(chip)
                row.addWidget(chip)
            row.addStretch()
            col.addLayout(row)
        col.addStretch()

        self._detail_widget = scroll
        self._center.addWidget(scroll)
        self._center.setCurrentWidget(scroll)

        self._back_title.setText(item["name"])
        self._nav.setCurrentIndex(1)
        self._apply_scale()

    def _close_detail(self):
        self._nav.setCurrentIndex(0)
        self._center.setCurrentWidget(self._pages)
        self._discard_detail()

    def _discard_detail(self):
        """Delete the detail view and unregister everything it added to the
        scale/theme tracking lists, so later passes never touch dead widgets."""
        if self._detail_widget is None:
            return
        self._center.removeWidget(self._detail_widget)
        self._detail_widget.deleteLater()
        self._detail_widget = None
        n_fonts, n_chips, n_accents, n_blocks = self._detail_marks
        del self._fonts[n_fonts:]
        del self._chips[n_chips:]
        del self._accent_labels[n_accents:]
        del self._blocks[n_blocks:]

    # ── Sponsors footer (always visible) ──────────────────────────────────

    def _sponsors_footer(self) -> QWidget:
        foot = ChamferFrame(fill=self._pal["surface2"], border=self._pal["line"],
                            cut=brand.CUT_SMALL)
        self._footer = foot
        foot.setFixedHeight(96)
        lay = QHBoxLayout(foot)
        lay.setContentsMargins(28, 14, 28, 14)
        lay.setSpacing(16)
        lay.addWidget(self._eyebrow("Sponsors"))
        for _ in _SPONSORS:
            logo = self._image_block(56)
            logo.setMinimumWidth(120)
            lay.addWidget(logo, stretch=1)
        return foot

    # ── Card / block / tile factories (tracked for theming + scaling) ─────

    def _card(self, accent_edge: bool = False) -> ChamferFrame:
        f = ChamferFrame(
            fill=self._pal["surface"], border=self._pal["line"],
            cut=brand.CUT_MEDIUM,
            accent_edge=self._accent if accent_edge else None,
        )
        f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._cards.append(f)
        if accent_edge:
            self._accent_edges.append(f)
        return f

    def _stat_tile(self, number: str, label: str) -> ChamferFrame:
        tile = self._card()
        tile.setFixedHeight(int(STAT_TILE_H * self._scale))
        v = QVBoxLayout(tile)
        v.setContentsMargins(22, 16, 22, 16)
        v.setSpacing(2)
        num = _StatNumber(number, brand.FONT_DISPLAY)
        num.setStyleSheet(f"color:{self._accent}; background:transparent;")
        num.set_px(int(STAT_TILE_H * STAT_NUM_RATIO * self._scale))
        self._accent_labels.append(num)
        v.addWidget(num, stretch=1)
        v.addWidget(self._body(label, FS_STAT_LABEL), stretch=0)
        self._stat_tiles.append((tile, num, STAT_TILE_H))
        return tile

    def _image_block(self, height: int) -> ChamferFrame:
        block = ChamferFrame(fill=self._pal["surface2"], border=None,
                            cut=brand.CUT_SMALL)
        block.setFixedHeight(int(height * self._scale))
        block.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lbl = QLabel("IMAGE", block)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color:{self._pal['faint']}; background:transparent;")
        f = QFont(brand.FONT_DISPLAY)
        f.setPixelSize(int(13 * self._scale))
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 120)
        lbl.setFont(f)
        v = QVBoxLayout(block)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(lbl)
        self._blocks.append((block, height))
        return block

    # ── Tab selection ─────────────────────────────────────────────────────

    def _select_tab(self, index: int):
        self._close_detail()
        self._pages.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_buttons):
            btn.set_active(i == index)

    # ── Responsive scaling ────────────────────────────────────────────────

    def resizeEvent(self, e):
        super().resizeEvent(e)
        s = max(0.7, min(2.4, e.size().width() / self._BASE_W))
        if abs(s - self._scale) > 0.01:
            self._scale = s
            self._apply_scale()

    def _apply_scale(self):
        s = self._scale
        for lbl, base, weight, family, tracking in self._fonts:
            f = QFont(family)
            f.setPixelSize(max(8, int(base * s)))
            f.setWeight(weight)
            if tracking:
                f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
            lbl.setFont(f)
        for chip in self._chips:
            chip.set_px(max(8, int(FS_CHIP * s)))
        for btn in self._tab_buttons:
            f = QFont(brand.FONT_DISPLAY); f.setPixelSize(int(FS_TAB * s)); f.setWeight(_Demi)
            btn.setFont(f); btn.update()
        if hasattr(self, "_back_btn"):
            f = QFont(brand.FONT_DISPLAY); f.setPixelSize(int(FS_BACK * s)); f.setWeight(_Demi)
            self._back_btn.setFont(f); self._back_btn.update()
        for frame, base_h in self._blocks:
            frame.setFixedHeight(int(base_h * s))
        for tile, num, base_h in self._stat_tiles:
            tile.setFixedHeight(int(base_h * s))
            num.set_px(int(base_h * STAT_NUM_RATIO * s))

    # ── Theme / team ──────────────────────────────────────────────────────

    def apply_theme(self, theme: str):
        self._pal = brand.palette(theme)
        for card in self._cards:
            card.set_fill(self._pal["surface"])
            card.set_border(self._pal["line"])
            if isinstance(card, _CardButton):
                card._border_rest = self._pal["line"]
        for frame, _h in self._blocks:
            frame.set_fill(self._pal["surface2"])
            for child in frame.findChildren(QLabel):
                child.setStyleSheet(
                    f"color:{self._pal['faint']}; background:transparent;"
                )
        self._footer.set_fill(self._pal["surface2"])
        self._footer.set_border(self._pal["line"])

    def apply_team(self, accent: str):
        self._accent = accent
        for lbl in self._accent_labels:
            lbl.setStyleSheet(f"color:{accent}; background:transparent;")
        for btn in self._tab_buttons:
            btn.set_accent(accent)
        if hasattr(self, "_back_btn"):
            self._back_btn.set_accent(accent)
        for card in self._accent_edges:
            card.set_accent(accent)
            if isinstance(card, _CardButton):
                card._accent_hex = accent
        for chip in self._chips:
            chip.set_accent(accent)
        self._header_line.set_color(accent)
