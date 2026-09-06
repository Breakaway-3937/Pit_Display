"""
Animated iOS-style toggle switch widget.

**Every dimension comes from the widget's own rect.** The paint code used to
carry the literal geometry of a 52×28 switch — a 52-wide track and a 24px thumb
— so the moment the switch was resized to fit the 220px sidebar it drew a track
wider than itself and a thumb taller than itself, and both were clipped. Nothing
here may be a literal pixel count.

**On is green, not the team accent.** A toggle says "this is on", which is a
*status*, and a panel carrying five of them in the team red spends the whole
red budget (§03) saying that things are normal. The one red on any surface here
belongs to whatever is exceptional. Pass `color_on` only where the colour
itself is the meaning.
"""

from PyQt6.QtWidgets import QAbstractButton
from PyQt6.QtCore import (
    Qt, QAbstractAnimation, QPropertyAnimation, QEasingCurve,
    pyqtProperty, QSize, QRectF,
)
from PyQt6.QtGui import QPainter, QColor

from app import brand


class ToggleSwitch(QAbstractButton):

    def __init__(self, color_on: str = brand.STATUS_ONLINE, parent=None):
        super().__init__(parent)
        self._color_on = QColor(color_on)
        self._color_off = QColor(brand.N600)
        self._thumb_x = 0.0

        self.setCheckable(True)
        # 38×22: the sidebar is 220px wide and every pixel the switch
        # takes comes off a screen name that is already tight.
        self.setFixedSize(38, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"thumb_x", self)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setDuration(150)

        self.toggled.connect(self._on_toggled)

    # ── Geometry, all of it derived ───────────────────────────────────────

    def _inset(self) -> float:
        """Gap between the thumb and the track, ~13% of the switch height."""
        return max(1.5, self.height() * 0.135)

    def _thumb_d(self) -> float:
        return max(4.0, self.height() - 2 * self._inset())

    def _travel(self) -> tuple[float, float]:
        """(off x, on x) for the thumb's left edge."""
        off = self._inset()
        return off, max(off, self.width() - self._inset() - self._thumb_d())

    def _rest_x(self) -> float:
        """Where the thumb belongs for the current checked state."""
        off, on = self._travel()
        return on if self.isChecked() else off

    def _on_toggled(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(float(self._thumb_x))
        self._anim.setEndValue(self._rest_x())
        self._anim.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    @pyqtProperty(float)
    def thumb_x(self) -> float:
        return self._thumb_x

    @thumb_x.setter
    def thumb_x(self, x: float):
        self._thumb_x = float(x)
        self.update()

    def set_color_on(self, hex_color: str):
        self._color_on = QColor(hex_color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        w, h = float(self.width()), float(self.height())
        r = h / 2

        track = self._color_on if self.isChecked() else self._color_off
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)

        d = self._thumb_d()
        off, on = self._travel()
        # The resting position is *derived*, never remembered. Callers set
        # these programmatically with `blockSignals`, so `toggled` — and with
        # it the animation that used to be the only thing that moved the thumb
        # — never fires, and the switch drew a green track with the thumb still
        # sitting on the left. The animation is now only the transition; where
        # the thumb ends up is a function of `isChecked()`.
        if self._anim.state() != QAbstractAnimation.State.Running:
            self._thumb_x = self._rest_x()
        x = min(max(self._thumb_x, off), on)
        p.setBrush(QColor(brand.N50))
        p.drawEllipse(QRectF(x, (h - d) / 2, d, d))
        p.end()

    def sizeHint(self) -> QSize:
        return QSize(38, 22)

    def minimumSizeHint(self) -> QSize:
        return QSize(28, 16)
