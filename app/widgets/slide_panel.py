"""
SlidePanel — reusable rotating-slide container for standard mode screens.

Usage:
    slides = [("Title A", "Body text"), ("Title B", "More text")]
    panel = SlidePanel(slides)
    rotation.advance.connect(panel.next_slide)
    panel.reset()  # jump back to slide 0
"""

from PyQt6.QtWidgets import QWidget, QStackedWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor


# ── Dot indicator ─────────────────────────────────────────────────────────────

class _SlideDots(QWidget):

    _DOT_R  = 5
    _DOT_GAP = 14

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self._count  = count
        self._active = 0
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_active(self, index: int):
        self._active = index
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r   = self._DOT_R
        gap = self._DOT_GAP
        diameter = r * 2
        total_w  = self._count * diameter + (self._count - 1) * gap
        x = (self.width() - total_w) // 2
        cy = self.height() // 2

        for i in range(self._count):
            color = QColor("#ffffff") if i == self._active else QColor("#444444")
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(x, cy - r, diameter, diameter)
            x += diameter + gap

        p.end()


# ── Individual slide ──────────────────────────────────────────────────────────

def _make_slide(title: str, body: str) -> QWidget:
    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.setSpacing(16)
    layout.setContentsMargins(60, 40, 60, 40)

    title_lbl = QLabel(title)
    title_lbl.setObjectName("screen_title")
    title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title_lbl.setWordWrap(True)
    layout.addWidget(title_lbl)

    body_lbl = QLabel(body)
    body_lbl.setObjectName("stat_label")
    body_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    body_lbl.setWordWrap(True)
    layout.addWidget(body_lbl)

    return w


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

        self._stack = QStackedWidget()
        for title, body in slides:
            self._stack.addWidget(_make_slide(title, body))
        outer.addWidget(self._stack, stretch=1)

        self._dots = _SlideDots(len(slides))
        outer.addWidget(self._dots, alignment=Qt.AlignmentFlag.AlignHCenter)

    def next_slide(self):
        self._index = (self._index + 1) % self._stack.count()
        self._stack.setCurrentIndex(self._index)
        self._dots.set_active(self._index)

    def reset(self):
        self._index = 0
        self._stack.setCurrentIndex(0)
        self._dots.set_active(0)

    @property
    def current_index(self) -> int:
        return self._index
