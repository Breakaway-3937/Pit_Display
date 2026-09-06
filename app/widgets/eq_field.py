"""
The equaliser as an instrument — one continuous response curve with handles.

Ten vertical sliders in a row is a *list of ten numbers*, and nobody tuning a
room thinks in ten numbers. They think in a shape: the bottom is pulled down,
the mud is scooped, presence is lifted. So the ten bands are drawn as the curve
they describe, over a rail per band, and you read the shape of the room in one
look. The numbers are still there underneath — this replaces how they are shown,
not what they are.

    GAIN dB ─────────────────────────────── PREAMP  −2.0
    ┌──────────────────────────────────────────────────┐
    │  ╷    ╷    ╷    ╷    ╷    ╷    ╷    ╷    ╷    ╷  │
    │  ╷    ╷    ╷    ╷    ╷    ╷   ╭─◯───◯╮   ╷    ╷  │
    │ ╌╷╌╌╌╌╷╌╌╌╌╷╌╌╌╌╷╌╌╌╌╷╌╌╌╌◯╌╌╌╯╷    ╷╰──◯────◯╌╌ │  0 dB
    │  ╷    ╷   ╭◯────◯────◯   ╷    ╷    ╷    ╷    ╷  │
    │  ╷   ╭◯───╯╷    ╷    ╷    ╷    ╷    ╷    ╷    ╷  │
    │  ◯───╯╷    ╷    ╷    ╷    ╷    ╷    ╷    ╷    ╷  │
    └──────────────────────────────────────────────────┘
     -12   -8   -3   -4   -2    0   +2   +3   +1    0
      31   62  125  250  500   1K   2K   4K   8K  16K

**±20 dB maps to the full height**, so 0 dB is the centre line and the dashed
rule through it is the thing you are reading the curve against.

Interaction is direct: press anywhere and the nearest band's handle goes to
where you pressed, then follows the finger. That is the same gesture as dragging
a slider, minus the part where you have to hit a 13px handle — this panel is
touched, and the design's floor is a 44px target.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from app import brand
from app.brand import FONT_MONO_STACK
from app.music import eq as eq_module

# The design's own geometry, in its 760×200 field. Everything is a ratio of the
# live widget, so the instrument keeps its proportions at any panel width.
_FIELD_H = 200.0
_RAIL_INSET = 8.0 / _FIELD_H     # rails stop this far from the field's edges
_HANDLE_R = 9.0 / _FIELD_H
_CURVE_W = 3.0 / _FIELD_H
_RULE_W = 2.0 / _FIELD_H


def _mono(px: int, weight: int = 500, track: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(FONT_MONO_STACK)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setWeight(QFont.Weight(weight))
    f.setPixelSize(max(8, px))
    if track:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing,
                           100.0 + track * 100.0)
    return f


class EQField(QWidget):
    """
    Ten bands as one response curve. Emits `band_changed(index, gain_db)`.

    Painted rather than laid out for the same reason the audience surfaces are:
    a curve through ten handles is not something Qt's layout system can express,
    and the whole point is that the ten values are one object.
    """

    band_changed = pyqtSignal(int, float)

    _FIELD_MIN_H = 150
    _LABELS_H = 46           # the two label rows under the field

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gains = [0.0] * eq_module.N_BANDS
        self._dragging: int | None = None
        self._theme_dark = True
        self.setMinimumHeight(self._FIELD_MIN_H + self._LABELS_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        # Every band is reachable by dragging across the field, so the widget
        # itself is the 44px target rather than ten small ones.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── State ─────────────────────────────────────────────────────────────

    def set_gains(self, gains):
        gains = [float(g) for g in gains][:eq_module.N_BANDS]
        if gains != self._gains:
            self._gains = gains
            self.update()

    def gains(self) -> list[float]:
        return list(self._gains)

    # ── Geometry ──────────────────────────────────────────────────────────

    def _field_rect(self) -> QRectF:
        return QRectF(0, 0, self.width(),
                      max(1.0, self.height() - self._LABELS_H))

    def _band_x(self, index: int, field: QRectF) -> float:
        """Band centres, evenly spaced — the design's 38, 114, 190 … 722."""
        step = field.width() / eq_module.N_BANDS
        return field.x() + step * (index + 0.5)

    def _gain_y(self, gain: float, field: QRectF) -> float:
        """±GAIN_MAX spans the full field; 0 dB is the centre line."""
        span = field.height() / 2
        frac = max(-1.0, min(1.0, gain / eq_module.GAIN_MAX))
        return field.center().y() - frac * span

    def _y_gain(self, y: float, field: QRectF) -> float:
        span = field.height() / 2
        frac = (field.center().y() - y) / span if span else 0.0
        return max(eq_module.GAIN_MIN,
                   min(eq_module.GAIN_MAX, frac * eq_module.GAIN_MAX))

    def _band_at(self, x: float, field: QRectF) -> int:
        step = field.width() / eq_module.N_BANDS
        if step <= 0:
            return 0
        return max(0, min(eq_module.N_BANDS - 1,
                          int((x - field.x()) // step)))

    # ── Interaction ───────────────────────────────────────────────────────

    def _grab(self, pos):
        field = self._field_rect()
        index = self._band_at(pos.x(), field)
        gain = round(self._y_gain(pos.y(), field))
        self._dragging = index
        if self._gains[index] != gain:
            self._gains[index] = gain
            self.update()
            self.band_changed.emit(index, gain)
        else:
            self.update()

    def mousePressEvent(self, event):
        self._grab(event.position())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging is not None:
            field = self._field_rect()
            # Stay on the band the press landed on: dragging up and down should
            # not smear across neighbours the way it would if the band were
            # re-picked from x on every move.
            gain = round(self._y_gain(event.position().y(), field))
            if self._gains[self._dragging] != gain:
                self._gains[self._dragging] = gain
                self.update()
                self.band_changed.emit(self._dragging, gain)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = None
        self.update()
        event.accept()

    # ── Painting ──────────────────────────────────────────────────────────

    def set_dark(self, dark: bool):
        self._theme_dark = dark
        self.update()

    @property
    def _ink(self) -> str:
        return brand.N50 if self._theme_dark else brand.CARBON

    @property
    def _ground(self) -> str:
        return brand.CARBON_BG if self._theme_dark else brand.WHITE

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        field = self._field_rect()
        h = field.height()
        rail_pad = h * _RAIL_INSET
        r = max(3.0, h * _HANDLE_R)
        curve_w = max(1.5, h * _CURVE_W)
        rule_w = max(1.0, h * _RULE_W)

        # 0 dB, dashed — the line the whole curve is read against.
        pen = QPen(QColor(brand.CARBON_LINE), rule_w)
        pen.setDashPattern([3.0, 4.0])
        p.setPen(pen)
        p.drawLine(QPointF(field.x(), field.center().y()),
                   QPointF(field.right(), field.center().y()))

        # One rail per band.
        p.setPen(QPen(QColor(brand.DIVIDER_DARK), rule_w,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(eq_module.N_BANDS):
            x = self._band_x(i, field)
            p.drawLine(QPointF(x, field.y() + rail_pad),
                       QPointF(x, field.bottom() - rail_pad))

        points = [QPointF(self._band_x(i, field), self._gain_y(g, field))
                  for i, g in enumerate(self._gains)]

        # The response curve.
        path = QPainterPath()
        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(self._ink), curve_w, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)

        # Handles: hollow, so the curve reads through them.
        for i, pt in enumerate(points):
            p.setBrush(QColor(self._ground))
            p.setPen(QPen(QColor(self._ink), curve_w))
            rr = r * (1.25 if i == self._dragging else 1.0)
            p.drawEllipse(pt, rr, rr)

        self._paint_labels(p, field)
        p.end()

    def _paint_labels(self, p: QPainter, field: QRectF):
        step = field.width() / eq_module.N_BANDS
        gain_font = _mono(12)
        hz_font = _mono(12)

        row_h = self._LABELS_H / 2
        gain_row = QRectF(0, field.bottom(), self.width(), row_h)
        hz_row = QRectF(0, field.bottom() + row_h, self.width(), row_h)

        p.setFont(gain_font)
        for i, g in enumerate(self._gains):
            cell = QRectF(field.x() + step * i, gain_row.y(), step,
                          gain_row.height())
            value = int(round(g))
            # A band at 0 is doing nothing, and saying so quietly is what makes
            # the ones that are doing something legible.
            p.setPen(QColor(brand.GRAPHITE if value == 0 else self._ink))
            p.drawText(cell, int(Qt.AlignmentFlag.AlignCenter),
                       f"{value:+d}" if value else "0")

        p.setPen(QPen(QColor(brand.RAISED_DARK), 1))
        p.drawLine(QPointF(0, hz_row.y()), QPointF(self.width(), hz_row.y()))

        p.setFont(hz_font)
        p.setPen(QColor(brand.N500))
        for i, name in enumerate(eq_module.BAND_LABELS):
            cell = QRectF(field.x() + step * i, hz_row.y(), step,
                          hz_row.height())
            p.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), name)
