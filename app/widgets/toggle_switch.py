"""Animated iOS-style toggle switch widget."""

from PyQt6.QtWidgets import QAbstractButton
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, QSize
from PyQt6.QtGui import QPainter, QColor


class ToggleSwitch(QAbstractButton):

    def __init__(self, color_on: str = "#C82027", parent=None):
        super().__init__(parent)
        self._color_on = QColor(color_on)
        self._color_off = QColor("#443F3D")
        self._thumb_x = 3

        self.setCheckable(True)
        self.setFixedSize(52, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"thumb_x", self)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setDuration(150)

        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(27 if checked else 3)
        self._anim.start()

    @pyqtProperty(int)
    def thumb_x(self) -> int:
        return self._thumb_x

    @thumb_x.setter
    def thumb_x(self, x: int):
        self._thumb_x = x
        self.update()

    def set_color_on(self, hex_color: str):
        self._color_on = QColor(hex_color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # Track
        track = self._color_on if self.isChecked() else self._color_off
        p.setBrush(track)
        p.drawRoundedRect(0, 4, 52, 20, 10, 10)

        # Thumb
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(self._thumb_x, 2, 24, 24)
        p.end()

    def sizeHint(self) -> QSize:
        return QSize(52, 28)
