"""
SlidePanel — reusable rotating-slide container for standard mode screens.

Brand: Chakra Petch display title over a Roboto body, one idea per slide,
generous whitespace, a single Break Line signature beneath the headline, and
a red active dot in the footer indicator.

Usage:
    slides = [("Title A", "Body text"), ("Title B", "More text")]
    panel = SlidePanel(slides)
    rotation.advance.connect(panel.next_slide)
    panel.reset()  # jump back to slide 0
"""

from PyQt6.QtWidgets import QWidget, QStackedWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPainter, QColor, QFont, QPolygonF

from app import brand
from app.config import config
from app.widgets.brand_widgets import BreakLine, eyebrow


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
                # Active dot is a chamfered bar — a nod to The Cut.
                p.setBrush(self._accent)
                pts = brand.cut_polygon(bar_w, diameter, cut=3)
                p.drawPolygon(QPolygonF([QPointF(x + px, cy - r + py) for px, py in pts]))
                x += bar_w + gap
            else:
                p.setBrush(idle)
                p.drawEllipse(x, cy - r, diameter, diameter)
                x += diameter + gap

        p.end()


# ── Individual slide ──────────────────────────────────────────────────────────

class _Slide(QWidget):
    """One slide: eyebrow, Chakra headline, Break Line signature, Roboto body."""

    def __init__(self, index: int, title: str, body: str, parent=None):
        super().__init__(parent)
        self._index = index
        accent = config.active_team.primary_color

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(18)
        layout.setContentsMargins(80, 40, 80, 40)

        self._eyebrow = eyebrow(self._eyebrow_text(), accent)
        self._eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._eyebrow)

        self._title = QLabel(title)
        self._title.setObjectName("screen_title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setWordWrap(True)
        tf = QFont(brand.FONT_DISPLAY)
        tf.setPixelSize(46)
        tf.setWeight(QFont.Weight.Bold)
        self._title.setFont(tf)
        layout.addWidget(self._title)

        # The Break Line — one signature per slide, centered under the headline
        self._break_line = BreakLine(color=accent, diameter=40)
        self._break_line.setMaximumWidth(320)
        layout.addWidget(self._break_line, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._body = QLabel(body)
        self._body.setObjectName("stat_label")
        self._body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._body.setWordWrap(True)
        bf = QFont(brand.FONT_BODY)
        bf.setPixelSize(18)
        self._body.setFont(bf)
        self._body.setMaximumWidth(720)
        layout.addWidget(self._body, alignment=Qt.AlignmentFlag.AlignHCenter)

    def _eyebrow_text(self) -> str:
        team = config.active_team
        label = f"{team.name} {team.number}" if team.name else f"Team {team.number}"
        return f"{label}  ·  {self._index:02d}"

    def apply_team(self, accent: str):
        self._eyebrow.setText(self._eyebrow_text().upper())
        self._eyebrow.setStyleSheet(f"color: {accent}; background: transparent;")
        self._break_line.set_color(accent)


# ── SlidePanel ────────────────────────────────────────────────────────────────

class SlidePanel(QWidget):
    """
    Stacked slides with a dot-indicator footer.
    Connect rotation.advance → next_slide().
    Call reset() when returning from another mode.
    """

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
        self._index = (self._index + 1) % self._stack.count()
        self._stack.setCurrentIndex(self._index)
        self._dots.set_active(self._index)

    def reset(self):
        self._index = 0
        self._stack.setCurrentIndex(0)
        self._dots.set_active(0)

    def apply_team(self, team):
        """Re-brand every slide to the newly active team."""
        for slide in self._slides:
            slide.apply_team(team.primary_color)
        self._dots.set_accent(team.primary_color)

    @property
    def current_index(self) -> int:
        return self._index
