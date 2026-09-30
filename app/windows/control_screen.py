"""
Control Screen — operator panel.

Layout:
  Top bar  : app title + active team selector (color reflects active team)
  Left col : screen cards — each has a name (click to configure) + power toggle
  Right col: settings panel for the selected screen

Only the control screen boots. Other screens are opened/closed via power toggles.
"""

from pathlib import Path
from typing import Callable

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QComboBox, QSizePolicy, QScrollArea,
    QApplication,
)
from PyQt6.QtCore import Qt, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap

from app import brand
from app.admin import admin
from app.cad_assets import cad_assets
from app import display
from app.config import config, MODES, MODE_LABELS, SCREENS, SCREEN_LABELS
from app.judges_slides import judges_slides
from app.rotation import rotation
from app.slides import Slide, coerce
from app.teams import all_teams
from app.theme import apply_theme
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, SelectableChip, eyebrow, mono_font,
)
from app.widgets.cad_upload_panel import CADSettingsPanel
from app.widgets.checklist_panel import ChecklistPanel
from app.widgets.admin_bar import AdminBar
from app.widgets.helpers import clear_layout, divider, label
from app.widgets.led_panel import LEDPanel
from app.widgets.music_panel import MusicPanel
from app.widgets.battery_panel import BatteryPanel
from app.widgets.network_panel import NetworkPanel
from app.widgets.robot_panel import RobotLogPanel
from app.widgets.update_panel import UpdatePanel
from app.widgets.nexus_panel import NexusPanel
from app.widgets.toggle_switch import ToggleSwitch
from app.leds import leds
from app.webcast import lan_address, webcast
from app.webcast import settings as webcast_settings

# Pit-wide subsystems. Unlike SCREENS these are not windows — they are hardware
# the pit owns, so they get their own sidebar group and their own panels.
# Robot Logs is not its own entry any more: it is a section of Telemetry,
# which is the home of every kind of telemetry the pit has — robot and link.
SYSTEMS = ["leds", "music", "batteries", "network", "nexus", "updates"]

# A window kept alive only for the pit network is laid out and painted but
# never mapped to a display. Measured: a presentation screen renders its whole
# chassis correctly this way with no monitor attached, which is the entire
# basis of publishing a screen from a machine that has no spare video output.
_NO_SCREEN = Qt.WidgetAttribute.WA_DontShowOnScreen
SYSTEM_LABELS = {"leds": "LED Strips", "music": "Music", "batteries": "Batteries",
                 "network": "Telemetry", "robot": "Robot Logs",
                 "nexus": "Event Feed", "updates": "Software Updates"}

# One muted line under each panel's title — where the thing physically is and
# what it is doing, which is what an operator standing at the panel needs
# before they need any of the controls.
_SCREEN_BLURBS = {
    "presentation_a": "Overhead left · 1920×1080 · the glanceable board",
    "presentation_b": "Overhead right · 1920×1080 · the board you walk up to",
    "project":        "Pit front · 1080×1920 portrait · touched by visitors",
    "control":        "This panel. Always on.",
}


# ── Mode button ──────────────────────────────────────────────────────────────

class ModeButton(SelectableChip):
    """
    One of the three mode selector buttons in the top bar.

    **Standard active is white; Judges and Lunch active are red.** Red here
    then means "the installation is in an exceptional state" — which is true
    about five percent of the day and worth noticing from across the pit. The
    rotation running normally is the opposite of that, so it does not get to
    spend the surface's one red; nor does the brand chip, nor the selected
    sidebar row. One red, always, and it is on the thing that is unusual.
    """

    # Standard is the resting state of the whole installation.
    _RESTING = "standard"

    def __init__(self, mode: str, team_color: str):
        super().__init__(MODE_LABELS.get(mode, mode.title()),
                         radius=brand.R_BTN)
        self.mode = mode
        self.setFixedSize(124, 46)
        f = self.font()
        f.setPixelSize(15)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
        self.setFont(f)
        self.setText(self.text().upper())

    def _colors(self):
        # The one place a selected chip *is* red: Judges and Lunch are the
        # exceptional states, and that is exactly what the budget is for.
        if self._active and self.mode != self._RESTING:
            return (QColor(brand.EMBER if self.isDown() else brand.RED),
                    QColor(brand.WHITE), None)
        return super()._colors()


# ── Screen card (sidebar row) ─────────────────────────────────────────────────

class ScreenCard(QFrame):
    """
    One row in the left sidebar.
    Left side: clickable name that selects the settings panel.
    Right side: power ToggleSwitch that shows/hides the managed window.
    Control screen gets no power toggle (it's always on).
    """

    selected = pyqtSignal(str)       # screen_id — user clicked the name area
    power_toggled = pyqtSignal(str, bool)  # screen_id, on

    def __init__(self, screen_id: str, team_color: str,
                 label_text: str | None = None, show_toggle: bool = True):
        super().__init__()
        self.screen_id = screen_id
        self._active = False
        self._team_color = team_color
        self._label_text = label_text or SCREEN_LABELS.get(screen_id, screen_id)
        self._show_toggle = show_toggle
        self._build(team_color)

    def _build(self, team_color: str):
        self.setFixedHeight(56)
        self.setObjectName("nav_row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(11, 0, 12, 0)
        row.setSpacing(10)

        # The selected row is marked with a white bar, not the team accent —
        # see ModeButton: the surface's one red belongs to the mode, not to
        # which panel the operator happens to be looking at.
        self._bar = QFrame()
        self._bar.setObjectName("nav_bar")
        self._bar.setFixedWidth(3)
        row.addWidget(self._bar)

        # Name button (left — fills remaining space)
        self._name_btn = QPushButton(self._label_text)
        self._name_btn.setFlat(True)
        self._name_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name_btn.clicked.connect(lambda: self.selected.emit(self.screen_id))
        row.addWidget(self._name_btn)

        # Power toggle (right) — hidden for the control screen itself
        if not self._show_toggle:
            self._toggle = None
        elif self.screen_id != "control":
            # Green, not the team accent: a powered screen is a *status*, and
            # eight red pills down the sidebar would spend the whole budget on
            # "things are normal".
            self._toggle = ToggleSwitch(color_on=brand.STATUS_ONLINE)
            self._toggle.setChecked(False)
            self._toggle.toggled.connect(
                lambda on: self.power_toggled.emit(self.screen_id, on)
            )
            row.addWidget(self._toggle, alignment=Qt.AlignmentFlag.AlignVCenter)
        else:
            always_on = label("ALWAYS ON", "stat_label")
            always_on.setFont(mono_font(10))
            always_on.setStyleSheet(
                f"color: {brand.STATUS_ONLINE}; background: transparent;")
            row.addWidget(always_on, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._toggle = None

        self._apply_style()

    def set_active(self, active: bool):
        self._active = active
        self._apply_style()

    def apply_team_color(self, hex_color: str):
        self._team_color = hex_color
        self._apply_style()

    def set_checked(self, on: bool):
        """Reflect state changed elsewhere, without re-emitting power_toggled."""
        if self._toggle is not None and self._toggle.isChecked() != on:
            self._toggle.blockSignals(True)
            self._toggle.setChecked(on)
            self._toggle.blockSignals(False)

    def _apply_style(self):
        base = (
            "background: transparent; border: none; text-align: left;"
            # The app-wide QPushButton rule carries 16px of horizontal padding,
            # which ate the end of every screen name in a 220px sidebar.
            " padding: 0;"
            f' font-family: "{brand.FONT_DISPLAY}"; font-size: 15px;'
            " font-weight: 600;"
        )
        if self._active:
            self.setStyleSheet(
                f"QFrame#nav_row {{ background-color: {brand.RAISED_DARK};"
                f" border-radius: {brand.R_BTN}px; }}"
                f"QFrame#nav_bar {{ background-color: {brand.WHITE};"
                f" border-radius: 2px; margin: 15px 0; }}")
            self._name_btn.setStyleSheet(
                f"QPushButton {{ {base} color: {brand.WHITE}; }}")
        else:
            self.setStyleSheet(
                "QFrame#nav_row { background-color: transparent;"
                f" border-radius: {brand.R_BTN}px; }}"
                "QFrame#nav_bar { background-color: transparent; }")
            self._name_btn.setStyleSheet(
                f"QPushButton {{ {base} color: {brand.N400}; }}")


# ── Setting row ───────────────────────────────────────────────────────────────

class SettingRow(QWidget):
    """
    One line of the settings column: label left, control right, ruled below.

    The column is a **ruled list**, which is a different object from the EQ
    (an instrument) and the CAN table (a form) even though all three are built
    from the same tokens. A list is scanned, so every row is the same height
    and closed by the same hairline; nothing in it is a card.

    64px is the floor and not the height — a row whose description runs to two
    lines grows, because losing the sentence to keep the grid is the wrong
    trade on a panel an operator reads under a six-minute clock.
    """

    _MIN_H = 64

    def __init__(self, label_text: str, description: str, control: QWidget):
        super().__init__()
        self.setObjectName("setting_row")
        # Without WA_StyledBackground a plain QWidget ignores a stylesheet
        # border entirely — the rule simply never appears, silently.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(self._MIN_H)
        self.setStyleSheet(
            f"QWidget#setting_row {{ border-bottom: 1px solid"
            f" {brand.RAISED_DARK}; }}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(20)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        name = label(label_text, "stat_value")
        name.setStyleSheet("font-size: 15px;")
        text_col.addWidget(name)
        if description:
            desc = label(description, "stat_label")
            desc.setWordWrap(True)
            # Wrapped or not, a QLabel still asks for its full text width until
            # it is told the layout may squeeze it.
            desc.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Preferred)
            desc.setMinimumWidth(0)
            text_col.addWidget(desc)
        layout.addLayout(text_col, stretch=1)
        layout.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)


def section(title: str, blurb: str = "") -> QWidget:
    """
    A heading inside the settings column: the name, then one wrapped line.

    Prose goes through here rather than through `helpers.label()`, which does
    not word-wrap: an unwrapped sentence asks the layout for its whole text
    width, and a long one made the settings column wider than the window and
    pushed the judges deck off the right edge.
    """
    host = QWidget()
    col = QVBoxLayout(host)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(4)
    name = label(title, "screen_title")
    name.setStyleSheet("font-size: 18px;")
    col.addWidget(name)
    if blurb:
        text = label(blurb, "stat_label")
        text.setWordWrap(True)
        text.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        text.setMinimumWidth(0)
        col.addWidget(text)
    return host


class PanelHeader(QWidget):
    """
    The settings column's masthead: what you are configuring, and what it is.

    An eyebrow naming the *kind* of thing, the name at 30px, and one muted line
    of orientation — then a 2px rule. Every panel opens the same way, so an
    operator who has switched panels knows where they are without reading.
    """

    def __init__(self, kind: str, title: str, subtitle: str = ""):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        eb = eyebrow(kind, brand.N500)
        eb.setStyleSheet(f"color: {brand.N500}; background: transparent;")
        outer.addWidget(eb)
        outer.addSpacing(10)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        name = label(title, "screen_title")
        # Set the font on the widget, not only in its stylesheet: a QSS
        # font-size does not always reach sizeHint() before the first layout
        # pass, so the row was allocated a 22px line for 30px type and clipped
        # the ascenders.
        nf = QFont(brand.FONT_DISPLAY)
        nf.setPixelSize(30)
        nf.setWeight(QFont.Weight.Bold)
        name.setFont(nf)
        name.setStyleSheet(f"color: {brand.WHITE}; background: transparent;")
        name.setMinimumHeight(nf.pixelSize() + 8)
        row.addWidget(name)
        if subtitle:
            note = label(subtitle, "stat_label")
            note.setWordWrap(True)
            note.setStyleSheet(f"font-size: 14px; color: {brand.N500};")
            note.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Preferred)
            note.setMinimumWidth(0)
            row.addWidget(note, stretch=1,
                          alignment=Qt.AlignmentFlag.AlignBottom)
        else:
            row.addStretch(1)
        outer.addLayout(row)
        outer.addSpacing(16)
        outer.addWidget(divider())


# ── Judges slide thumbnail ────────────────────────────────────────────────────

class _Thumbnail(QFrame):
    """Clickable slide thumbnail used inside _SlidePicker."""

    _W, _H = 96, 54  # 16:9 display size

    def __init__(self, index: int, path: Path):
        super().__init__()
        self._index = index
        self._down = False
        self.setFixedWidth(self._W + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(2)

        img_lbl = QLabel()
        img_lbl.setFixedSize(self._W, self._H)
        img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = QPixmap(str(path)).scaled(
            self._W, self._H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        img_lbl.setPixmap(pm)
        layout.addWidget(img_lbl)

        num = QLabel(str(index + 1))
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setStyleSheet("font-size: 10px; color: #888888; border: none;")
        layout.addWidget(num)

        self.set_active(False)

    def set_active(self, active: bool):
        accent = config.active_team.primary_color
        border = accent if active else brand.CARBON_LINE
        self.setStyleSheet(
            f"QFrame {{ border: 2px solid {border}; border-radius: 4px;"
            f" background-color: {brand.CARBON_SURF}; }}"
        )

    # Fires on release-inside, not on press. On the touch panel a press is also
    # how a drag-scroll of the thumbnail strip begins, and the touch router
    # unlatches that press with a release *outside* the rect (see app/touch.py)
    # — so a swipe along the strip scrolls it instead of jumping the slide.
    def mousePressEvent(self, event):
        self._down = True
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._down and self.rect().contains(event.position().toPoint()):
            judges_slides.go_to(self._index)
        self._down = False
        event.accept()


# ── Judges slide picker ───────────────────────────────────────────────────────

class _SlidePicker(QWidget):
    """
    Thumbnail strip + nav buttons for judges slides.
    Shown in the settings panel for presentation screens.
    """

    def __init__(self):
        super().__init__()
        self._thumbs: list[_Thumbnail] = []
        self._build_ui()
        judges_slides.slides_reloaded.connect(self._rebuild)
        judges_slides.slide_changed.connect(self._on_slide_changed)
        config.team_changed.connect(lambda _: self._refresh_active())
        self._rebuild()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # Folder path hint so students know where to drop files
        hint = label("Drop images in:  assets/judges_slides/", "stat_label")
        hint.setStyleSheet("font-family: Roboto; font-size: 11px;")
        outer.addWidget(hint)

        # Scrollable thumbnail strip
        self._scroll = QScrollArea()
        self._scroll.setFixedHeight(96)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._strip = QWidget()
        self._strip.setStyleSheet("background: transparent;")
        self._thumb_layout = QHBoxLayout(self._strip)
        self._thumb_layout.setContentsMargins(0, 4, 0, 4)
        self._thumb_layout.setSpacing(8)
        self._scroll.setWidget(self._strip)
        outer.addWidget(self._scroll)

        # Nav row: Prev | counter | Next | Reload
        nav = QWidget()
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(8)

        self._prev_btn = QPushButton("◀  Prev")
        self._prev_btn.setObjectName("btn_primary")
        self._prev_btn.clicked.connect(judges_slides.prev)
        nav_row.addWidget(self._prev_btn)

        self._counter_lbl = label("— / —", "stat_label", Qt.AlignmentFlag.AlignCenter)
        nav_row.addWidget(self._counter_lbl, stretch=1)

        self._next_btn = QPushButton("Next  ▶")
        self._next_btn.setObjectName("btn_primary")
        self._next_btn.clicked.connect(judges_slides.next)
        nav_row.addWidget(self._next_btn)

        reload_btn = QPushButton("↺  Reload")
        reload_btn.clicked.connect(judges_slides.reload)
        nav_row.addWidget(reload_btn)

        outer.addWidget(nav)

    # ── Rebuild from disk ─────────────────────────────────────────────────

    def _rebuild(self):
        clear_layout(self._thumb_layout)
        self._thumbs.clear()

        paths = judges_slides.paths
        if not paths:
            self._thumb_layout.addWidget(
                label("No images found — add files and click Reload", "stat_label")
            )
        else:
            for i, path in enumerate(paths):
                thumb = _Thumbnail(i, path)
                thumb.set_active(i == judges_slides.index)
                self._thumbs.append(thumb)
                self._thumb_layout.addWidget(thumb)
        self._thumb_layout.addStretch()
        self._update_counter()

    # ── Signal handlers ───────────────────────────────────────────────────

    def _on_slide_changed(self, index: int, _pixmap):
        self._refresh_active(index)
        self._update_counter()

    def _refresh_active(self, index: int | None = None):
        active = index if index is not None else judges_slides.index
        for i, thumb in enumerate(self._thumbs):
            thumb.set_active(i == active)

    def _update_counter(self):
        count = judges_slides.count
        has = count > 0
        self._counter_lbl.setText(f"{judges_slides.index + 1} / {count}" if has else "— / —")
        self._prev_btn.setEnabled(has)
        self._next_btn.setEnabled(has)


# ── Standard slide picker ─────────────────────────────────────────────────────

class _DwellRail(QWidget):
    """
    The live slide's dwell, drawn beside it in the picker.

    So the operator can see where the rotation *is* without looking up at the
    overhead panel — the same rail the audience screen shows in its ledger,
    reading the same `rotation.progress()`, so the two can never disagree.
    """

    def __init__(self):
        super().__init__()
        self.setFixedSize(90, 5)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.update)

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(brand.N600))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), r, r)
        w = rotation.progress() * self.width()
        if w > 0:
            p.setBrush(QColor(brand.N50))
            p.drawRoundedRect(QRectF(0, 0, max(self.height(), w),
                                     self.height()), r, r)
        p.end()


class _StandardSlideRow(QFrame):
    """One row in the standard-slide list: number, title, and a preview line."""

    clicked = pyqtSignal(int)

    _HEIGHT = 58

    def __init__(self, index: int, title: str, body: str, is_fact: bool,
                 kind: str = ""):
        super().__init__()
        self.index = index
        self._active = False
        self._down = False
        self._is_fact = is_fact
        self._kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # QLabel subclasses QFrame, so a bare `QFrame { border-left: … }` rule
        # paints that border on every child label too. Scope it by object name.
        self.setObjectName("slide_row")
        # A fixed row is what makes the list scan as a list. The body line is
        # elided rather than wrapped for the same reason: a two-line row and a
        # one-line row next to each other read as two different kinds of thing.
        self.setFixedHeight(self._HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(11, 0, 16, 0)
        row.setSpacing(14)

        self._bar = QFrame()
        self._bar.setObjectName("slide_row_bar")
        self._bar.setFixedWidth(3)
        row.addWidget(self._bar)

        self._num = label(f"{index + 1:02d}", "stat_label")
        self._num.setFont(mono_font(14))
        self._num.setFixedWidth(24)
        row.addWidget(self._num)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(5)
        self._title = label(title, "stat_value")
        self._title.setStyleSheet("font-size: 15px;")
        self._title_text = title
        text.addWidget(self._title)
        self._body = label(body, "stat_label")
        self._body.setStyleSheet("font-size: 12px;")
        self._body_text = body
        text.addWidget(self._body)
        # A single-line QLabel asks the layout for its whole text width, so a
        # long slide body made the settings column wider than the window and
        # pushed the judges deck off the right edge — the exact failure the
        # CLAUDE.md "helpers.label() does not word-wrap" note describes. These
        # elide instead, so the layout must be told not to ask.
        for lbl in (self._title, self._body):
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                              QSizePolicy.Policy.Preferred)
            lbl.setMinimumWidth(0)
        row.addLayout(text, stretch=1)

        # Three kinds of entry, and the operator needs to tell them apart:
        # authored slides are edited in code, fact slides regenerate from the
        # log, and the board is a live widget rather than a slide at all.
        tag_text, tag_color = "", ""
        if kind == "board":
            tag_text, tag_color = "LIVE BOARD", brand.STATUS_ONLINE
        elif is_fact:
            tag_text, tag_color = "FROM LOG", brand.STATUS_PENDING
        if tag_text:
            tag = label(tag_text, "stat_label")
            tag.setFont(mono_font(10))
            tag.setStyleSheet(
                f"color: {tag_color}; background: transparent;"
                f" border: 1.5px solid {brand.N600};"
                f" border-radius: 13px; padding: 4px 12px;")
            row.addWidget(tag)

        self._rail = _DwellRail()
        self._rail.setVisible(False)
        row.addWidget(self._rail)

        self._apply_style()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        for lbl, text in ((self._title, self._title_text),
                          (self._body, self._body_text)):
            lbl.setText(lbl.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, max(40, lbl.width())))

    def set_active(self, active: bool):
        if active != self._active:
            self._active = active
            self._rail.setVisible(active)
            self._apply_style()

    def apply_team(self, _color: str):
        self._apply_style()

    def _apply_style(self):
        # The live row is marked in **white**, not the team accent: red on this
        # panel means "the installation is in an exceptional state", and the
        # rotation running normally is the opposite of that.
        if self._active:
            self.setStyleSheet(
                f"QFrame#slide_row {{ background-color: {brand.RAISED_DARK};"
                f" border-radius: {brand.R_BTN}px; }}"
                f"QFrame#slide_row_bar {{ background-color: {brand.WHITE};"
                f" border-radius: 2px; }}")
            self._num.setStyleSheet(
                f"color: {brand.N400}; background: transparent;")
            self._title.setStyleSheet(
                f"font-size: 15px; color: {brand.WHITE};")
            self._body.setStyleSheet(
                f"font-size: 12px; color: {brand.N500};")
        else:
            self.setStyleSheet(
                "QFrame#slide_row { background-color: transparent;"
                f" border-radius: {brand.R_BTN}px; }}"
                "QFrame#slide_row_bar { background-color: transparent; }")
            self._num.setStyleSheet(
                f"color: {brand.GRAPHITE}; background: transparent;")
            self._title.setStyleSheet(
                f"font-size: 15px; color: {brand.N300};")
            self._body.setStyleSheet(
                f"font-size: 12px; color: {brand.GRAPHITE};")

    # Release-inside, for the same reason as _Thumbnail above: this row lives in
    # a scroll list that the operator drags with a finger.
    def mousePressEvent(self, event):
        self._down = True
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._down and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.index)
        self._down = False
        event.accept()


# The five faces of Standard mode, in the order an operator would reach for
# them. Values match `PresentationScreen._CONTENT_PAGES` plus "rotation".
_CONTENT_CHOICES = [
    ("rotation",    "Slide rotation"),
    ("next_match",  "Next match"),
    ("checklist",   "Pit checklist"),
    ("diagnostics", "Robot diagnostics"),
    ("robot_info",  "Robot info"),
]


class _StandardSlidePicker(QWidget):
    """
    Every slide in the Standard rotation for one presentation screen, with the
    live one highlighted and clickable to jump.

    Jumps travel through `config.set(screen, "slide_index", n)` rather than a
    direct call, for the same reason everything else here does: the control
    screen never holds a reference to a presentation window.
    """

    def __init__(self, screen_id: str):
        super().__init__()
        self._screen_id = screen_id
        self._rows: list[_StandardSlideRow] = []
        self._build_ui()
        config.screen_setting_changed.connect(self._on_setting_changed)
        config.mode_changed.connect(lambda _m: self._refresh_enabled())
        config.team_changed.connect(self._on_team_changed)
        config.logs_changed.connect(self._rebuild)
        self._rebuild()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._count_lbl = label("", "stat_label")
        head.addWidget(self._count_lbl)
        head.addStretch()
        self._mode_hint = label("", "stat_label")
        head.addWidget(self._mode_hint)
        outer.addLayout(head)

        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidget(self._list_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(260)
        outer.addWidget(scroll)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        prev = RoundedButton("‹ Previous", variant="secondary",
                             accent=config.active_team.primary_color)
        prev.clicked.connect(lambda: self._step(-1))
        nxt = RoundedButton("Next ›", variant="secondary",
                            accent=config.active_team.primary_color)
        nxt.clicked.connect(lambda: self._step(+1))
        first = RoundedButton("↩ First", variant="ghost")
        first.clicked.connect(lambda: self._jump(0))
        reload_btn = RoundedButton("↺ Reload", variant="ghost")
        reload_btn.setToolTip(
            "Rebuild the list after importing a robot log. The presentation "
            "screen picks up new slides when it is powered off and on.")
        reload_btn.clicked.connect(self._rebuild)
        for b in (prev, nxt, first, reload_btn):
            nav.addWidget(b)
        self._nav_buttons = [prev, nxt]
        outer.addLayout(nav)

    # ── Build the list ────────────────────────────────────────────────────

    def _screen_cls(self):
        from app.windows.presentation_a import PresentationScreenA
        from app.windows.presentation_b import PresentationScreenB
        return (PresentationScreenA if self._screen_id == "presentation_a"
                else PresentationScreenB)

    def _slides(self) -> list[Slide]:
        """
        Every stop in the cycle — slides, then this screen's board.

        Deliberately the same call the presentation screen makes, so an index
        written here means the same stop there. If these two ever disagree, a
        click lands on the wrong slide.
        """
        cls = self._screen_cls()
        try:
            return cls.rotation_entries()
        except Exception:
            return coerce(cls.SLIDES)

    def _board_index(self) -> int | None:
        cls = self._screen_cls()
        try:
            return (len(cls.rotation_slides())
                    if cls.board_entry() is not None else None)
        except Exception:
            return None

    def _rebuild(self):
        clear_layout(self._list)
        self._rows.clear()
        slides = self._slides()
        authored = len(self._authored())
        board = self._board_index()
        for i, slide in enumerate(slides):
            is_board = board is not None and i == board
            row = _StandardSlideRow(
                i, slide.title, slide.body,
                is_fact=(not is_board) and i >= authored,
                kind="board" if is_board else "")
            row.clicked.connect(self._jump)
            self._rows.append(row)
            self._list.addWidget(row)
        self._list.addStretch()

        n_slides = len(slides) - (1 if board is not None else 0)
        facts = n_slides - authored
        self._count_lbl.setText(
            f"{n_slides} slides — {authored} authored"
            + (f", {facts} from the robot log" if facts else "")
            + (" · + the live board" if board is not None else ""))
        self._refresh_active()
        self._refresh_enabled()

    def _authored(self) -> list:
        from app.windows.presentation_a import PresentationScreenA
        from app.windows.presentation_b import PresentationScreenB
        cls = (PresentationScreenA if self._screen_id == "presentation_a"
               else PresentationScreenB)
        return cls.SLIDES

    # ── Interaction ───────────────────────────────────────────────────────

    def _current(self) -> int:
        return int(config.get(self._screen_id, "slide_index", 0) or 0)

    def _jump(self, index: int):
        if not self._rows:
            return
        config.set(self._screen_id, "slide_index", index % len(self._rows))

    def _step(self, delta: int):
        self._jump(self._current() + delta)

    def _on_setting_changed(self, screen: str, key: str, _value):
        if screen == self._screen_id and key == "slide_index":
            self._refresh_active()

    def _refresh_active(self):
        current = self._current()
        for row in self._rows:
            row.set_active(row.index == current)

    def _refresh_enabled(self):
        standard = config.mode == "standard"
        self._mode_hint.setText(
            "" if standard else f"{MODE_LABELS.get(config.mode, config.mode)} mode is live")
        self._mode_hint.setStyleSheet(
            f"color: {brand.STATUS_PENDING}; background: transparent;")
        for b in self._nav_buttons:
            b.setEnabled(True)   # jumping while in another mode still sets the
                                 # slide the screen returns to

    def _on_team_changed(self, team):
        for row in self._rows:
            row.apply_team(team.primary_color)
        for b in self._nav_buttons:
            b.set_accent(team.primary_color)


# ── CAD judges picker ─────────────────────────────────────────────────────────

class _CADJudgesPicker(QWidget):
    """
    Subsystem selector shown in the judges settings for presentation screens.
    Activates the CAD page on the presentation screens and drives subsystem focus.
    """

    def __init__(self):
        super().__init__()
        self._sub_btns: dict[str, QPushButton] = {}
        self._build_ui()
        cad_assets.config_changed.connect(self._rebuild)
        cad_assets.subsystem_focused.connect(self._on_focused)
        config.team_changed.connect(lambda _: self._refresh_colors())
        self._rebuild()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        hint = label("Subsystems map to Onshape node names in assets/cad/subsystems.json.", "stat_label")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._btn_area = QWidget()
        self._btn_layout = QVBoxLayout(self._btn_area)
        self._btn_layout.setContentsMargins(0, 0, 0, 0)
        self._btn_layout.setSpacing(4)
        outer.addWidget(self._btn_area)

        nav = QWidget()
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(8)

        back_btn = QPushButton("◀  Back to Slides")
        back_btn.clicked.connect(lambda: cad_assets.activate_cad(False))
        nav_row.addWidget(back_btn)

        full_btn = QPushButton("↩  Full View")
        full_btn.setObjectName("btn_primary")
        full_btn.clicked.connect(lambda: cad_assets.focus_subsystem(""))
        nav_row.addWidget(full_btn)

        outer.addWidget(nav)

    def _rebuild(self):
        clear_layout(self._btn_layout)
        self._sub_btns.clear()

        cfg = cad_assets.load_config()
        subs = cfg.get("subsystems", [])

        if not subs:
            self._btn_layout.addWidget(
                label("No subsystems configured — add them in the Project panel.", "stat_label")
            )
            return

        color = config.active_team.primary_color
        for sub in subs:
            btn = QPushButton(sub["display_name"])
            btn.setFixedHeight(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            accent = sub.get("accent_color", color)
            btn.setProperty("accent", accent)
            sub_id = sub["id"]
            btn.clicked.connect(lambda checked, sid=sub_id: cad_assets.focus_subsystem(sid))
            self._sub_btns[sub_id] = btn
            self._btn_layout.addWidget(btn)

        self._btn_layout.addStretch()
        self._refresh_colors()

    def _on_focused(self, sub_id: str):
        for sid, btn in self._sub_btns.items():
            accent = btn.property("accent") or config.active_team.primary_color
            if sid == sub_id:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {accent};
                        color: #ffffff;
                        border: none;
                        border-radius: 6px;
                        font-size: 13px;
                        font-weight: 700;
                        padding: 0 14px;
                    }}
                """)
            else:
                btn.setStyleSheet("")

    def _refresh_colors(self):
        self._on_focused(cad_assets.focused_id)


# ── Per-screen settings panel ─────────────────────────────────────────────────

class ScreenSettingsPanel(QWidget):
    def __init__(self, screen_id: str):
        super().__init__()
        self._screen_id = screen_id
        self._team_toggles: list[ToggleSwitch] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(4)

        outer.addWidget(PanelHeader(
            "Screen", SCREEN_LABELS.get(screen_id, screen_id),
            _SCREEN_BLURBS.get(screen_id, "")))
        outer.addSpacing(12)

        # ── Appearance ────────────────────────────────────────────────────
        outer.addWidget(section("Appearance"))
        outer.addSpacing(8)

        light = config.screen_theme(screen_id) == "light"
        self._theme_toggle, self._theme_label, theme_row = self._labeled_toggle(
            checked=light, text="Light" if light else "Dark",
        )
        self._theme_toggle.toggled.connect(self._on_theme_toggled)

        outer.addWidget(SettingRow(
            label_text="Dark / Light Mode",
            description="Switch this screen between dark and light display themes.",
            control=theme_row,
        ))

        # ── Where it goes ─────────────────────────────────────────────────
        # Every audience surface is designed at a fixed size and scaled by a
        # contain fit against it, so a window that is not filling a monitor is
        # showing a smaller copy of the design rather than the design. These
        # two rows are what make a pit screen actually be a pit screen.
        if screen_id != "control":
            self._display_combo = QComboBox()
            self._display_combo.setMinimumWidth(240)
            self._populate_display_combo(screen_id)
            self._display_combo.currentIndexChanged.connect(
                self._on_display_selected)
            outer.addWidget(SettingRow(
                label_text="Display",
                description="Which monitor this screen goes to when it is "
                            "powered on.",
                control=self._display_combo,
            ))

            fill = bool(config.get(screen_id, "fullscreen",
                                   display.default_fullscreen()))
            self._fill_toggle, self._fill_label, fill_row = \
                self._labeled_toggle(
                    checked=fill, text="Filling" if fill else "Windowed")
            self._fill_toggle.toggled.connect(self._on_fill_toggled)
            outer.addWidget(SettingRow(
                label_text="Fill the display",
                description="Off leaves it as a window — which is what you "
                            "want while there is only one monitor, so the "
                            "control panel never ends up behind it.",
                control=fill_row,
            ))

            # ── On the pit network ────────────────────────────────────
            # The overhead panels hang across the pit, and the cable to them
            # is the expensive part of hanging them. Published here, a Pi on
            # the Ethernet switch shows this screen in a browser instead —
            # and because the window is then laid out without ever being
            # mapped to a display, this machine needs no video output for it.
            if screen_id in webcast_settings.PUBLISHABLE:
                published = webcast_settings.is_published(screen_id)
                self._web_toggle, self._web_label, web_row = \
                    self._labeled_toggle(
                        checked=published,
                        text="Network" if published else "Monitor")
                self._web_toggle.toggled.connect(self._on_web_toggled)
                outer.addWidget(SettingRow(
                    label_text="Where this screen appears",
                    description="Monitor sends it to a display plugged into "
                                "this machine. Network sends it to a display "
                                "on the pit switch instead, over one Ethernet "
                                "cable. This setting only chooses which — the "
                                "screen's own switch in the sidebar still "
                                "turns it on and off either way. Keep the pit "
                                "network on its own switch; it is not for "
                                "event wifi.",
                    control=web_row,
                ))

                # The address is the one thing here somebody has to reproduce
                # exactly, on another machine, by hand. Reading an IP off a
                # screen and typing it into a Pi is where this goes wrong, so
                # it is selectable *and* there is a button that puts it on the
                # clipboard — the pit machine is a touch panel, and dragging a
                # text selection with a finger is not a real option.
                url_row = QHBoxLayout()
                url_row.setContentsMargins(0, 0, 0, 0)
                url_row.setSpacing(10)
                self._web_url = label("", "stat_label")
                self._web_url.setWordWrap(True)
                self._web_url.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                self._web_url.setCursor(Qt.CursorShape.IBeamCursor)
                url_row.addWidget(self._web_url, stretch=1)
                self._web_copy = RoundedButton("Copy", variant="secondary")
                self._web_copy.setFixedWidth(96)
                self._web_copy.clicked.connect(self._copy_web_url)
                url_row.addWidget(self._web_copy,
                                  alignment=Qt.AlignmentFlag.AlignTop)
                url_holder = QWidget()
                url_holder.setLayout(url_row)
                outer.addWidget(url_holder)
                self._refresh_web_url()

        outer.addWidget(divider())
        outer.addSpacing(12)

        # Judges content — only relevant for presentation screens
        if screen_id in ("presentation_a", "presentation_b"):
            # What this screen shows in Standard mode. Per-screen, not a global
            # mode: the useful arrangement is one overhead screen on the
            # checklist while the other keeps rotating for visitors.
            outer.addWidget(section(
                "Standard Content",
                "What this overhead screen shows in Standard mode. Rotation "
                "cycles the slides and finishes on the diagnostics board; "
                "anything else pins that page and stops the 45-second timer "
                "touching this screen."))
            outer.addSpacing(8)

            # A combo, not a toggle: there are four faces now, and a two-state
            # switch cannot say which of them you meant.
            self._pres_content = QComboBox()
            for value, text in _CONTENT_CHOICES:
                self._pres_content.addItem(text, value)
            current = config.get(screen_id, "content", "rotation")
            idx = self._pres_content.findData(current)
            self._pres_content.setCurrentIndex(idx if idx >= 0 else 0)
            self._pres_content.setFixedWidth(220)
            self._pres_content.currentIndexChanged.connect(
                self._on_pres_content_changed)

            outer.addWidget(SettingRow(
                label_text="Screen content",
                description="Rotation, the next match, the checklist, or a robot board pinned.",
                control=self._pres_content,
            ))
            outer.addSpacing(8)
            outer.addWidget(ChecklistPanel(screen_id))
            outer.addSpacing(16)

            outer.addWidget(divider())
            outer.addSpacing(12)

            outer.addWidget(section(
                "Standard Slides",
                "Everything in this screen's Standard rotation. Click one to "
                "put it on screen now; the 45-second timer carries on from "
                "there."))
            outer.addSpacing(8)
            outer.addWidget(_StandardSlidePicker(screen_id))
            outer.addSpacing(16)
            outer.addWidget(divider())
            outer.addSpacing(12)

            outer.addWidget(section("Judges Slides"))
            outer.addSpacing(8)
            outer.addWidget(_SlidePicker())
            outer.addSpacing(16)

            outer.addWidget(divider())
            outer.addSpacing(12)

            outer.addWidget(section("Judges CAD"))
            outer.addSpacing(8)
            outer.addWidget(_CADJudgesPicker())
            outer.addSpacing(12)

        # Project screen: pick which interactive view is shown, then CAD config.
        if screen_id == "project":
            outer.addWidget(section("Display Content"))
            outer.addSpacing(8)

            board = config.get(screen_id, "content", "cad") == "board"
            self._content_toggle, self._content_label, content_row = self._labeled_toggle(
                checked=board, text="Impact Board" if board else "CAD Viewer",
            )
            self._content_toggle.toggled.connect(self._on_content_toggled)

            outer.addWidget(SettingRow(
                label_text="CAD Viewer / Impact Board",
                description="Choose what the project touchscreen shows.",
                control=content_row,
            ))

            outer.addWidget(divider())
            outer.addSpacing(12)
            outer.addWidget(section("CAD Viewer Config"))
            outer.addSpacing(8)
            outer.addWidget(CADSettingsPanel(), stretch=1)

        outer.addStretch()

        config.team_changed.connect(self._on_team_changed)

    def _labeled_toggle(self, checked: bool, text: str) -> tuple[ToggleSwitch, QLabel, QWidget]:
        """A team-colored ToggleSwitch with a state label beside it."""
        toggle = ToggleSwitch()
        toggle.setChecked(checked)
        self._team_toggles.append(toggle)

        state_lbl = label(text, "stat_value")

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(toggle)
        row_layout.addSpacing(10)
        row_layout.addWidget(state_lbl)
        row_layout.addStretch()
        return toggle, state_lbl, row

    def _on_theme_toggled(self, light: bool):
        self._theme_label.setText("Light" if light else "Dark")
        config.set(self._screen_id, "theme", "light" if light else "dark")

    def _populate_display_combo(self, screen_id: str):
        self._display_combo.blockSignals(True)
        self._display_combo.clear()
        for i, name in enumerate(display.screen_names()):
            self._display_combo.addItem(name, i)
        current = int(config.get(screen_id, "display",
                                 display.default_display(screen_id)))
        # A monitor unplugged between sessions must not leave the picker on an
        # index that no longer exists.
        self._display_combo.setCurrentIndex(
            min(max(0, current), max(0, self._display_combo.count() - 1)))
        self._display_combo.blockSignals(False)

    def _on_display_selected(self, index: int):
        config.set(self._screen_id, "display", index)

    def _on_fill_toggled(self, fill: bool):
        self._fill_label.setText("Filling" if fill else "Windowed")
        config.set(self._screen_id, "fullscreen", fill)

    def _on_web_toggled(self, on: bool):
        """
        Publish or unpublish this screen on the pit LAN.

        The service writes `webcast.json` and restarts its socket; the control
        screen separately reconciles the window, because a published screen
        needs one whether or not a monitor is showing it.
        """
        # One call: the service turns the feature on if it was off, settles
        # the settings, brings the socket up and only then announces the
        # change, so window lifetime is reconciled against the finished state.
        webcast.set_screen_published(self._screen_id, on)
        if on and not webcast.listening:
            self._web_label.setText("Failed")
        else:
            self._web_label.setText("Network" if on else "Monitor")
        self._refresh_web_url()

    def _refresh_web_url(self):
        """The exact address to type into the Pi — never left to be guessed."""
        if not getattr(self, "_web_url", None):
            return
        published = webcast_settings.is_published(self._screen_id)
        self._web_copy.setVisible(published)
        if not published:
            self._web_address = ""
            self._web_url.setText(
                "This screen goes to a monitor plugged into this machine. "
                "Switch it to Network to send it across the pit instead.")
            return
        host = lan_address()
        port = webcast_settings.get("port")
        # Held verbatim, because this is what the Copy button puts on the
        # clipboard: the sentence around it is for reading, the string itself
        # has to survive being pasted into a Pi's address bar unchanged.
        self._web_address = f"http://{host}:{port}/screen/{self._screen_id}"
        powered = self._screen_id in getattr(self.window(), "_powered", set())
        state = ("" if powered else
                 "  This screen is switched off. Turn it on with its switch "
                 "in the sidebar; it will not open a window on this machine.")
        self._web_url.setText(
            f"{self._web_address}\n"
            f"Open that address on the display and put its browser in full "
            f"screen. http://{host}:{port}/ lists every screen on the "
            f"network. Pit Systems \u2192 Telemetry shows what is "
            f"connected.{state}")

    def _copy_web_url(self):
        """Put the bare address on the clipboard — no sentence, no trailing dot."""
        address = getattr(self, "_web_address", "")
        if not address:
            return
        QApplication.clipboard().setText(address)
        self._web_copy.setText("Copied")
        # Back to "Copy" on its own, so the button never sits lying about what
        # it will do next time.
        QTimer.singleShot(1400, lambda: self._web_copy.setText("Copy"))

    def _on_pres_content_changed(self, _index: int):
        config.set(self._screen_id, "content", self._pres_content.currentData())

    def _on_content_toggled(self, board: bool):
        self._content_label.setText("Impact Board" if board else "CAD Viewer")
        config.set(self._screen_id, "content", "board" if board else "cad")

    def _on_team_changed(self, team):
        # Toggles no longer follow the team: "on" is a status and stays green,
        # so the surface's one red is free for whatever is exceptional.
        pass


# ── Pit subsystem panel ───────────────────────────────────────────────────────

class SystemSettingsPanel(QWidget):
    """
    Wrapper giving the LED and Music panels the same header chrome the
    per-screen panels have, so the right-hand column reads consistently.
    """

    _BLURBS = {
        "leds": "Drive the pit LED strips over USB. The controller keeps "
                "running its animation if this app closes.",
        "music": "Local music for the overhead speakers, with a ten-band "
                 "equaliser for tuning the pit.",
        "batteries": "The charging cart, live from each battery's fuel gauge: "
                     "which one to grab next and how long the rest have to go. "
                     "Mock-up.",
        "network": "Every link the pit depends on — the event relay, the "
                   "overhead displays, the LED controller — and the robot's "
                   "own logs. Where to look first when a screen is wrong.",
        "robot": "Import telemetry exported off the robot, and name the CAN "
                 "ids so every screen can say “Front-Left Drive” instead of "
                 "“TalonFX 11”.",
        "nexus": "Live match queuing, the pit map, inspection and alliances "
                 "from frc.nexus — where our next match is, without running "
                 "back from the field.",
        "updates": "What this machine is running, and how it gets the next "
                   "build off GitHub. Never during an event.",
    }

    def __init__(self, system_id: str):
        super().__init__()
        self._system_id = system_id

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(4)

        outer.addWidget(PanelHeader(
            "Pit system", SYSTEM_LABELS.get(system_id, system_id),
            self._BLURBS.get(system_id, "")))
        outer.addSpacing(16)

        self.body = {"leds": LEDPanel, "music": MusicPanel, "batteries": BatteryPanel,
                     "network": NetworkPanel, "robot": RobotLogPanel,
                     "nexus": NexusPanel, "updates": UpdatePanel}[system_id]()
        outer.addWidget(self.body)
        outer.addStretch()


# ── Control screen ────────────────────────────────────────────────────────────

class ControlScreen(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Control")
        self.setMinimumSize(480, 400)
        self._screen_cards: dict[str, ScreenCard] = {}
        self._settings_panels: dict[str, ScreenSettingsPanel] = {}
        self._managed_windows: dict[str, QMainWindow] = {}
        self._window_factories: dict[str, Callable[[], QMainWindow]] = {}
        # Which screens the operator has switched on. Window *existence* used
        # to be the whole answer, and it stopped being one when a screen could
        # also be kept alive headless for the pit network: a published screen
        # that is powered off still has a window, and it must not be placed on
        # a monitor. `_reconcile` is where the two reasons are resolved.
        self._powered: set[str] = set()
        self._webcast = None
        self._mode_buttons: dict[str, ModeButton] = {}
        self._build_ui()
        config.team_changed.connect(self._on_team_changed)
        config.mode_changed.connect(self._sync_mode_buttons)
        config.screen_setting_changed.connect(self._on_screen_setting_changed)
        leds.state_changed.connect(self._on_leds_state_changed)

    def set_window_factories(self, factories: dict[str, "Callable[[], QMainWindow]"]) -> None:
        """
        How to *build* each managed screen, rather than the built screens.

        Powering a screen off closes and destroys it, so there is nothing to
        hold — the window only exists while it is on. That is what an operator
        means by off: the CAD viewer's Chromium process is gone, the drift and
        rotation timers are gone, and a screen that has picked up a stale state
        is fixed by turning it off and on again, which is the first thing
        anybody tries anyway.
        """
        self._window_factories = factories

    def managed_window(self, screen_id: str):
        """
        The live window for a screen, or None when nothing wants one.

        This is what the webcast service renders, so it is deliberately *not*
        "the window if it is powered on": a screen published to the pit
        network has a window whether or not a monitor is showing it.
        """
        return self._managed_windows.get(screen_id)

    def set_webcast(self, service) -> None:
        """
        The pit-network publisher, so window lifetime can account for it.

        Window lifetime has exactly one owner and this is it. The service
        asks for windows and never builds one; in return this has to know
        when a screen is published, so it can keep one alive headless.
        """
        self._webcast = service
        service.published_changed.connect(self._on_published_changed)
        for screen_id in ("presentation_a", "presentation_b"):
            self._reconcile(screen_id)

    def _on_published_changed(self, screen_id: str, _on: bool) -> None:
        self._reconcile(screen_id)

    def _published(self, screen_id: str) -> bool:
        return (self._webcast is not None
                and screen_id in self._webcast.published())

    def _reconcile(self, screen_id: str) -> None:
        """
        Make the window match the power switch, and decide where it goes.

        **The power switch always means the same thing: is this screen on.**
        What changes is where it comes out.

            off                     → no window at all
            on, not published       → a window on its monitor, as always
            on, published           → a window that is never mapped to a
                                      display — laid out and painted for the
                                      browser across the pit and nowhere else

        **A published screen is blind by design.** The pit machine is driving
        a panel on the far side of the pit through a Pi; opening a second copy
        of it on the operator's own monitor is not a preview, it is a window
        in the way, and on a single-display machine it lands on top of the
        control panel. So publishing does not merely *allow* a headless
        window, it *requires* one: on the network is instead of on a monitor,
        never as well as.

        The earlier shape had publishing keep a window alive on its own, so an
        unpowered published screen still streamed. That made the power switch
        mean two different things depending on a setting three rows above it —
        "off" turned the monitor off but left the Pi showing a live screen.
        One switch, one meaning: off is off, whichever wire it was coming out
        of.
        """
        powered = screen_id in self._powered
        published = self._published(screen_id)
        window = self._managed_windows.get(screen_id)

        if not powered:
            self._destroy_window(screen_id)
            return

        if window is None:
            window = self._build_window(screen_id)
            if window is None:
                return

        headless = published
        # Qt only reads this attribute when a widget is mapped, so a window
        # changing between headless and placed has to be hidden across the
        # change or it keeps whichever state it was shown with.
        if bool(window.testAttribute(_NO_SCREEN)) != headless:
            window.hide()
            window.setAttribute(_NO_SCREEN, headless)

        if headless:
            # **This window is the state engine, not a picture.** Nothing
            # renders it any more — the browser draws from the state it
            # publishes — but it still owns the rotation: it advances its own
            # `slide_index` and writes it back to `config`, which is what
            # keeps a monitor and a browser showing the same slide. It is laid
            # out at the design size so every face resolves the geometry it
            # expects, and never mapped to a display.
            window.resize(1920, 1080)
            window.show()
        else:
            self._place(screen_id, window)

    def shutdown_managed(self) -> None:
        """Close every managed screen — called when the control panel closes."""
        for screen_id in list(self._managed_windows):
            self._destroy_window(screen_id)

    def closeEvent(self, event):
        # The audience windows are children of nothing, so closing the control
        # panel has to take them with it or the app keeps running headless with
        # three slide rotations nobody can see.
        self.shutdown_managed()
        super().closeEvent(event)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        main.addWidget(self._top_bar())

        # Admin bar — an inline strip, not a screen and not a modal, so the
        # operator can watch the gated controls appear as they unlock.
        self._admin_bar = AdminBar()
        self._admin_bar.closed.connect(self._close_admin)
        self._admin_bar.setVisible(False)
        main.addWidget(self._admin_bar)

        # The whole body scrolls as one — sidebar and settings column
        # together, under a fixed top bar. The sidebar alone is ten rows and
        # two headers, which is taller than a short laptop window, and a
        # column that does not scroll is a column whose last entries do not
        # exist. The alternative — a second scrollbar just for the sidebar —
        # is two things to drag on a panel driven by a finger.
        body_host = QWidget()
        body = QHBoxLayout(body_host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._left_sidebar(), stretch=0)
        body.addWidget(self._right_panel(), stretch=1)

        self._body_scroll = QScrollArea()
        self._body_scroll.setWidget(body_host)
        self._body_scroll.setWidgetResizable(True)
        self._body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        main.addWidget(self._body_scroll, stretch=1)

    def _top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("top_bar")
        bar.setFixedHeight(76)
        bar.setStyleSheet(
            f"QFrame#top_bar {{ background-color: #1A171A; border: none;"
            f" border-bottom: 2px solid {brand.CARBON_LINE}; }}"
        )

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(26)

        # Left: the brand chip. **White, not red** — the chip is a door, not a
        # focal element, and the one red on this bar belongs to whichever mode
        # is exceptional (see ModeButton).
        brand_row = QHBoxLayout()
        brand_row.setSpacing(14)

        chip_col = QVBoxLayout()
        chip_col.setSpacing(3)
        # 76px is the bar; the chip (44) + the HOLD hairline has to fit inside
        # it with air, so the margins are what give, not the bar's height.
        chip_col.setContentsMargins(0, 6, 0, 6)
        self._brand_chip = RoundedFrame(
            fill=brand.N50, border=None, radius=brand.R_BTN)
        self._brand_chip.setFixedSize(58, 44)
        self._brand_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brand_chip.setToolTip("Admin — LED tuning and equaliser")
        self._brand_chip.mousePressEvent = self._on_brand_clicked
        chip_l = QVBoxLayout(self._brand_chip)
        chip_l.setContentsMargins(0, 0, 0, 0)
        self._chip_num = label(str(config.active_team.number), "",
                               Qt.AlignmentFlag.AlignCenter)
        self._chip_num.setStyleSheet(
            f'color: {brand.CARBON}; background: transparent;'
            f' font-family: "{brand.FONT_DISPLAY}"; font-size: 20px;'
            f' font-weight: 700;')
        chip_l.addWidget(self._chip_num)
        chip_col.addWidget(self._brand_chip)

        # The chip carried no affordance at all. A mono hairline under it is
        # enough for an operator who has been told, and invisible to a visitor.
        hold = label("HOLD", "", Qt.AlignmentFlag.AlignCenter)
        hold.setFont(mono_font(9))
        hold.setStyleSheet(
            f"color: {brand.GRAPHITE}; background: transparent;")
        chip_col.addWidget(hold)
        brand_row.addLayout(chip_col)

        title_col = QVBoxLayout()
        title_col.setSpacing(5)
        title_col.setContentsMargins(0, 12, 0, 12)
        # No `screen_title` object name: that QSS role carries its own 22px
        # font-size, so the label was laid out against a size it does not use.
        wordmark = label("BREAKAWAY")
        wf = QFont(brand.FONT_DISPLAY)
        wf.setPixelSize(20)
        wf.setWeight(QFont.Weight.Bold)
        wf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        wordmark.setFont(wf)
        wordmark.setStyleSheet(
            f"color: {brand.WHITE}; background: transparent;")
        title_col.addWidget(wordmark)
        self._title_label = label("PIT DISPLAY")
        self._title_label.setFont(mono_font(11))
        self._title_label.setStyleSheet(
            f"color: {brand.N500}; background: transparent;")
        title_col.addWidget(self._title_label)
        brand_row.addLayout(title_col)
        layout.addLayout(brand_row)

        # Modes sit next to the identity, not centred: they are the first thing
        # an operator reaches for, and a centred group moves as the bar resizes.
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        team_color = config.active_team.primary_color
        for mode in MODES:
            btn = ModeButton(mode, team_color)
            btn.set_active(mode == config.mode)
            btn.clicked.connect(lambda checked, m=mode: self._on_mode_clicked(m))
            self._mode_buttons[mode] = btn
            mode_row.addWidget(btn)
        layout.addLayout(mode_row)

        layout.addStretch()

        # The wall clock. A pit runs on a six-minute cycle and the operator is
        # standing, not looking at a laptop's menu bar.
        self._clock = label("")
        self._clock.setFont(mono_font(13))
        self._clock.setStyleSheet(
            f"color: {brand.N500}; background: transparent;")
        layout.addWidget(self._clock, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._tick_clock()
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()

        self._team_combo = QComboBox()
        self._team_combo.setObjectName("team_pill")
        self._team_combo.setFixedHeight(46)
        self._team_combo.setMinimumWidth(190)
        self._team_combo.setStyleSheet(
            f"QComboBox#team_pill {{ border: 1.5px solid {brand.N600};"
            f" border-radius: 23px; padding: 0 20px; background: transparent;"
            f' color: {brand.N50}; font-family: "{brand.FONT_DISPLAY}";'
            f" font-size: 14px; font-weight: 600; }}")
        self._populate_team_combo()
        self._team_combo.currentIndexChanged.connect(self._on_team_selected)
        layout.addWidget(self._team_combo,
                         alignment=Qt.AlignmentFlag.AlignVCenter)

        return bar

    def _tick_clock(self):
        from datetime import datetime
        self._clock.setText(datetime.now().strftime("%H:%M:%S"))

    def _populate_team_combo(self):
        self._team_combo.blockSignals(True)
        self._team_combo.clear()
        for i, team in enumerate(all_teams()):
            self._team_combo.addItem(
                f"{team.number} {team.name}".strip(),
                userData=team.number)
            if team.number == config.active_team.number:
                self._team_combo.setCurrentIndex(i)
        self._team_combo.blockSignals(False)

    def _nav_group(self, text: str) -> QLabel:
        """A sidebar group label: mono caps, tracked, quiet."""
        lbl = label(text.upper())
        lbl.setFont(mono_font(11))
        lbl.setStyleSheet(f"color: {brand.GRAPHITE}; background: transparent;")
        lbl.setContentsMargins(10, 6, 0, 10)
        return lbl

    def _left_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(
            f"QFrame#sidebar {{ background-color: #1A171A; border: none;"
            f" border-right: 2px solid {brand.CARBON_LINE}; }}"
        )

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 16)
        layout.setSpacing(6)

        layout.addWidget(self._nav_group("Screens"))

        team_color = config.active_team.primary_color
        for screen_id in SCREENS:
            card = ScreenCard(screen_id, team_color)
            card.selected.connect(self._select_screen)
            card.power_toggled.connect(self._on_power_toggled)
            self._screen_cards[screen_id] = card
            layout.addWidget(card)

        layout.addSpacing(14)
        layout.addWidget(self._nav_group("Pit Systems"))

        for system_id in SYSTEMS:
            # The LED card's toggle is the strip kill switch; music has no
            # equivalent single on/off, so it gets a plain row.
            card = ScreenCard(system_id, team_color,
                              label_text=SYSTEM_LABELS[system_id],
                              show_toggle=(system_id == "leds"))
            card.selected.connect(self._select_screen)
            card.power_toggled.connect(self._on_power_toggled)
            if system_id == "leds":
                card.set_checked(leds.enabled)
            self._screen_cards[system_id] = card
            layout.addWidget(card)

        layout.addStretch()
        return sidebar

    def _right_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        for screen_id in SCREENS:
            panel = ScreenSettingsPanel(screen_id)
            self._settings_panels[screen_id] = panel
            panel.setVisible(False)
            layout.addWidget(panel)

        for system_id in SYSTEMS:
            panel = SystemSettingsPanel(system_id)
            self._settings_panels[system_id] = panel
            panel.setVisible(False)
            layout.addWidget(panel)

        layout.addStretch()

        # No scroll area of its own: the body scrolls as a whole (see
        # _build_ui), so this is just the column.
        self._select_screen(SCREENS[0])
        return container

    # ── Interaction ───────────────────────────────────────────────────────

    def _on_brand_clicked(self, _event):
        """The Breakaway mark is the admin door."""
        if self._admin_bar.isVisible():
            self._close_admin()
        else:
            self._admin_bar.setVisible(True)
            self._admin_bar.focus_password()

    def _close_admin(self):
        """Hiding the bar always re-locks — there is no stay-unlocked option."""
        admin.lock()
        self._admin_bar.setVisible(False)

    def _select_screen(self, screen_id: str):
        for sid, panel in self._settings_panels.items():
            panel.setVisible(sid == screen_id)
        for sid, card in self._screen_cards.items():
            card.set_active(sid == screen_id)

    def _on_power_toggled(self, screen_id: str, on: bool):
        if screen_id == "leds":
            leds.set_enabled(on)
            return
        if on:
            self._powered.add(screen_id)
        else:
            self._powered.discard(screen_id)
        self._reconcile(screen_id)
        # **Tell the pit network, or a screen on it ignores its own switch.**
        # Power is not a `config` key, so nothing else emits anything here;
        # without this the displays hold the last state they were sent and
        # keep rotating a screen the operator has just turned off.
        if self._webcast is not None:
            self._webcast.screen_power_changed(screen_id)
        # The panel's address line says whether the screen is running, so it
        # goes stale the moment the power switch moves without it.
        panel = self._settings_panels.get(screen_id)
        if panel is not None and hasattr(panel, "_refresh_web_url"):
            panel._refresh_web_url()
        if not on:
            return
        # `show()` activates the window it shows, and keystrokes go to the
        # active window. The audience screens never need a keyboard, so letting
        # one take it means the operator's next keystroke — a CAN-id name, a
        # checklist item, the admin password — lands on a slide rotation
        # instead. Hand activation straight back.
        self.activateWindow()

    def _build_window(self, screen_id: str):
        factory = self._window_factories.get(screen_id)
        if factory is None:
            return None
        window = factory()
        self._managed_windows[screen_id] = window
        return window

    def _destroy_window(self, screen_id: str) -> None:
        """
        Close a screen and let it go.

        `hide()` left the whole window alive — its timers, its signal
        subscriptions, and for the project screen an entire Chromium render
        process — for a screen the operator had switched off. `close()` runs
        the normal teardown, and `deleteLater()` drops it; every connection it
        made to `config`, `rotation`, `checklist` and `cad_assets` goes with the
        QObject, so nothing has to be unwired by hand.
        """
        window = self._managed_windows.pop(screen_id, None)
        if window is None:
            return
        window.close()
        window.deleteLater()

    def _place(self, screen_id: str, window) -> None:
        """
        Put an audience window on its monitor, at its monitor's size.

        Every audience surface is designed at a fixed size and scaled by a
        contain fit against it, so a window that is not filling a monitor is
        showing a proportionally smaller copy of the design rather than the
        design. `show()` on its own left them at a size hint on whichever
        display the window manager chose.
        """
        display.place(
            window, screen_id,
            int(config.get(screen_id, "display",
                           display.default_display(screen_id))),
            bool(config.get(screen_id, "fullscreen",
                            display.default_fullscreen())))

    def _on_team_selected(self, index: int):
        team_number = self._team_combo.itemData(index)
        if team_number is not None:
            config.set_team(team_number)

    def _on_mode_clicked(self, mode: str):
        config.set_mode(mode)

    def _sync_mode_buttons(self, mode: str):
        """
        Follow `config.mode_changed`, not the click.

        The buttons used to be updated only by the handler that set the mode,
        so anything else that moved it — the music service following the mode,
        a preset, anything added later — left the top bar claiming a mode that
        was no longer live.
        """
        for m, btn in self._mode_buttons.items():
            btn.set_active(m == mode)

    def _on_team_changed(self, team):
        # Brand chip follows the active team
        # The chip stays white whatever the team is: it is a door, and the
        # bar's one red belongs to an exceptional mode. Only the number moves.
        self._chip_num.setText(str(team.number))
        # Mode buttons recolor
        for btn in self._mode_buttons.values():
            btn.set_accent(team.primary_color)
        # Sidebar cards recolor
        for card in self._screen_cards.values():
            card.apply_team_color(team.primary_color)
        # Keep combo in sync if changed programmatically
        for i in range(self._team_combo.count()):
            if self._team_combo.itemData(i) == team.number:
                self._team_combo.blockSignals(True)
                self._team_combo.setCurrentIndex(i)
                self._team_combo.blockSignals(False)
                break

    def _on_leds_state_changed(self):
        card = self._screen_cards.get("leds")
        if card is not None:
            card.set_checked(leds.enabled)

    def _replace_window(self, screen_id: str) -> None:
        """Re-apply placement to a window that is already up."""
        window = self._managed_windows.get(screen_id)
        if window is not None and window.isVisible():
            self._place(screen_id, window)
            self.activateWindow()

    def _on_screen_setting_changed(self, screen_id: str, key: str, value):
        if key in ("display", "fullscreen"):
            self._replace_window(screen_id)
        # Managed windows subscribe to this signal themselves; the control
        # window is the only one nobody else re-themes.
        if screen_id == "control" and key == "theme":
            apply_theme(self, value)
