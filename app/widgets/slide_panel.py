"""
SlidePanel — reusable rotating-slide container for standard mode screens.

Brand: Chakra Petch display title over a Roboto body, one idea per slide,
generous whitespace, a single Trace leading line beneath the headline, and
a red active pill in the footer indicator.

Usage:
    slides = [("Title A", "Body text"), ("Title B", "More text")]
    panel = SlidePanel(slides)
    rotation.advance.connect(panel.next_slide)
    panel.reset()  # jump back to slide 0
"""

from PyQt6.QtWidgets import (
    QWidget, QStackedWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QColor

from app import brand
from app.config import config
from app.widgets.brand_widgets import Trace, eyebrow


# ── Dot indicator ─────────────────────────────────────────────────────────────

class _SlideDots(QWidget):

    _DOT_R  = 5
    _DOT_GAP = 14

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self._count  = count
        self._active = 0
        self._accent = QColor(config.active_team.primary_color)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_active(self, index: int):
        self._active = index
        self.update()

    def set_accent(self, hex_color: str):
        self._accent = QColor(hex_color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r        = self._DOT_R
        gap      = self._DOT_GAP
        diameter = r * 2
        bar_w    = diameter * 3          # the active slide reads as a wide bar

        # Centre the row: every dot is `diameter` wide except the active bar.
        total_w = (self._count - 1) * diameter + bar_w + (self._count - 1) * gap
        x  = (self.width() - total_w) // 2
        cy = self.height() // 2

        idle = QColor(brand.N400)
        for i in range(self._count):
            p.setPen(Qt.PenStyle.NoPen)
            if i == self._active:
                # Active slide reads as a wide rounded pill (Rounded, §5.1).
                p.setBrush(self._accent)
                p.drawRoundedRect(QRectF(x, cy - r, bar_w, diameter), r, r)
                x += bar_w + gap
            else:
                p.setBrush(idle)
                p.drawEllipse(x, cy - r, diameter, diameter)
                x += diameter + gap

        p.end()


# ── Individual slide ──────────────────────────────────────────────────────────

class _Slide(QWidget):
    """One slide: eyebrow, Chakra headline, Trace leading line, Roboto body."""

    def __init__(self, index: int, title: str, body: str, parent=None):
        super().__init__(parent)
        self._index = index
        accent = config.active_team.primary_color

        layout = QVBoxLayout(self)
        # Centre with stretches, NOT layout.setAlignment(AlignCenter): that
        # collapses the layout to its minimum size, so word-wrapped labels get
        # their narrowest width and a long body is clipped instead of wrapping
        # into the space that is actually available.
        layout.setSpacing(18)
        layout.setContentsMargins(80, 40, 80, 40)
        layout.addStretch(1)

        self._eyebrow = eyebrow(self._eyebrow_text(), accent)
        self._eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._eyebrow)

        self._title = QLabel(title)
        self._title.setObjectName("screen_title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setWordWrap(True)
        # Own-stylesheet size wins over the app QSS #screen_title rule (22px)
        # while family/weight/themed color still come from that rule.
        self._title.setStyleSheet("font-size: 46px;")
        layout.addWidget(self._title)

        # Trace — one leading line per slide, centered under the headline
        self._trace = Trace(color=accent, stroke=6)
        self._trace.setMaximumWidth(320)
        self._trace.setMinimumWidth(220)
        trace_row = QHBoxLayout()
        trace_row.setContentsMargins(0, 0, 0, 0)
        trace_row.addStretch(1)
        trace_row.addWidget(self._trace)
        trace_row.addStretch(1)
        layout.addLayout(trace_row)

        self._body = QLabel(body)
        self._body.setObjectName("stat_label")
        self._body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._body.setWordWrap(True)
        self._body.setStyleSheet("font-size: 18px;")  # overrides #stat_label 12px
        self._body.setMaximumWidth(720)
        self._body.setMinimumWidth(520)
        # Word-wrapped labels compute height from width, so the layout has to be
        # told to ask. Without this a multi-line body is given a single line.
        body_policy = self._body.sizePolicy()
        body_policy.setHeightForWidth(True)
        self._body.setSizePolicy(body_policy)
        # Centre the body with a stretch row rather than addWidget(alignment=…).
        # Passing an alignment makes Qt lay the widget out at its sizeHint, which
        # for a word-wrapped label is one line tall — so a multi-line body gets
        # clipped. Inside a row it receives a real width and heightForWidth works.
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.addStretch(1)
        body_row.addWidget(self._body)
        body_row.addStretch(1)
        layout.addLayout(body_row)
        layout.addStretch(1)

    def _eyebrow_text(self) -> str:
        team = config.active_team
        label = f"{team.name} {team.number}" if team.name else f"Team {team.number}"
        return f"{label}  ·  {self._index:02d}"

    def apply_team(self, accent: str):
        self._eyebrow.setText(self._eyebrow_text().upper())
        self._eyebrow.setStyleSheet(f"color: {accent}; background: transparent;")
        self._trace.set_color(accent)


# ── SlidePanel ────────────────────────────────────────────────────────────────

class SlidePanel(QWidget):
    """
    Stacked slides with a dot-indicator footer.
    Connect rotation.advance → next_slide().
    Call reset() when returning from another mode.

    `slide_changed` fires on every move, however it was caused — the rotation
    timer or an operator jumping from the control screen — so a picker can stay
    in sync without polling.
    """

    slide_changed = pyqtSignal(int)

    def __init__(self, slides: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._index = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._slides: list[_Slide] = []
        self._stack = QStackedWidget()
        for i, (title, body) in enumerate(slides, start=1):
            slide = _Slide(i, title, body)
            self._slides.append(slide)
            self._stack.addWidget(slide)
        outer.addWidget(self._stack, stretch=1)

        self._dots = _SlideDots(len(slides))
        outer.addWidget(self._dots, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addSpacing(24)

    def next_slide(self):
        self.set_slide(self._index + 1)

    def previous_slide(self):
        self.set_slide(self._index - 1)

    def set_slide(self, index: int):
        """
        Jump to a slide. Wraps in both directions, so the control screen's
        prev/next need no bounds checking. Idempotent — re-selecting the
        current slide emits nothing, which is what stops the control screen and
        the presentation screen echoing each other through config.
        """
        count = self._stack.count()
        if count == 0:
            return
        index %= count
        if index == self._index:
            return
        self._index = index
        self._stack.setCurrentIndex(index)
        self._dots.set_active(index)
        self.slide_changed.emit(index)

    def reset(self):
        self.set_slide(0)

    def titles(self) -> list[str]:
        return [self._stack.widget(i)._title.text()
                for i in range(self._stack.count())]

    @property
    def count(self) -> int:
        return self._stack.count()

    def apply_team(self, team):
        """Re-brand every slide to the newly active team."""
        for slide in self._slides:
            slide.apply_team(team.primary_color)
        self._dots.set_accent(team.primary_color)

    @property
    def current_index(self) -> int:
        return self._index
