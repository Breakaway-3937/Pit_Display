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

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QLinearGradient, QPainter,
                         QPainterPath, QPen)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from app import brand
from app.brand import FONT_MONO_STACK
from app.music import eq as eq_module
from app.music.analyser import normalise as analyser_normalise

# The level ramp, bottom to top: quiet and cool to hot. Every stop is a brand
# colour from the chart span (`breakaway_branding.md` §03), so a meter that has
# to be rainbow-ish to be readable still cannot introduce a colour the system
# does not own. Red is the last eighth only — it means a band is running out of
# headroom, which is the exceptional state the brand reserves red for.
_LEVEL_RAMP = (
    (0.00, brand.HARBOR),        # #2B3A67 deep blue — barely there
    (0.22, brand.SKY),           # #3F6FB5
    (0.42, brand.SPRUCE),        # #2E8B7F teal
    (0.60, "#7FB98F"),           # Sprout — the light end of the green ramp
    (0.74, "#C9A227"),           # Ochre
    (0.87, brand.STATUS_PENDING),  # #E08A1E amber
    (1.00, brand.STATUS_FAULT),  # #BA141A — out of headroom
)


def _level_colour(frac: float) -> str:
    """The ramp colour at a height, for a tick or a readout to match its bar."""
    frac = max(0.0, min(1.0, frac))
    previous = _LEVEL_RAMP[0]
    for stop in _LEVEL_RAMP:
        if frac <= stop[0]:
            return stop[1] if stop[0] == previous[0] else (
                previous[1] if frac - previous[0] < stop[0] - frac else stop[1])
        previous = stop
    return _LEVEL_RAMP[-1][1]


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
        # The live analyser, or None. Attached by the music panel when the
        # operator turns the display on; the field draws bars behind the curve
        # only while it is there, and is exactly what it always was without it.
        self._analyser = None
        self._tick = QTimer(self)
        # **33 ms, chosen against the data rate rather than picked.** The
        # shadow decoder delivers a 50 ms block roughly every 42 ms, so a
        # 50 ms repaint was slower than the thing it was drawing and a block
        # could sit unshown. 30 fps clears that with room to spare; 60 would
        # cost 9.3% of a core (measured, at 1.54 ms a repaint) to show 24 Hz
        # of data, which is paying double for nothing.
        self._tick.setInterval(33)
        self._tick.timeout.connect(self.update)
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

    def set_analyser(self, analyser) -> None:
        """
        Show live band levels behind the curve, or pass None to stop.

        The repaint timer runs **only while an analyser is attached**. A
        response curve does not move on its own, so an EQ with the display off
        repaints when a finger moves it and at no other time.
        """
        self._analyser = analyser
        if analyser is None:
            self._tick.stop()
        else:
            self._tick.start()
        self.update()

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

        # ── The graticule ────────────────────────────────────────────────
        # Horizontal rules every 10 dB, the way a console's EQ screen is read.
        # Without them a curve is a shape; with them it is a set of values.
        # ±10 only. ±20 *is* the field's own edge, so ruling it draws a line
        # on the border and labelling it puts type outside the widget.
        p.setFont(_mono(10))
        for db in (10, -10):
            y = self._gain_y(db, field)
            p.setPen(QPen(QColor(brand.DIVIDER_DARK), rule_w))
            p.drawLine(QPointF(field.x(), y), QPointF(field.right(), y))
            p.setPen(QColor(brand.N600 if self._theme_dark else brand.N400))
            p.drawText(QRectF(field.x() + 3, y - 13, 34, 12),
                       int(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignBottom), f"{db:+d}")

        # 0 dB, dashed — the line the whole curve is read against.
        pen = QPen(QColor(brand.CARBON_LINE), rule_w)
        pen.setDashPattern([3.0, 4.0])
        p.setPen(pen)
        p.drawLine(QPointF(field.x(), field.center().y()),
                   QPointF(field.right(), field.center().y()))

        # ── The live analyser, behind everything ─────────────────────────
        # **Only ever real measurements.** A spectrum display is the easiest
        # thing in this app to fake convincingly, and one that invents a
        # dancing graph is lying to everyone who looks at it. No analyser
        # attached means no bars, not idle animation.
        self._paint_analyser(p, field)

        # One rail per band.
        p.setPen(QPen(QColor(brand.DIVIDER_DARK), rule_w,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(eq_module.N_BANDS):
            x = self._band_x(i, field)
            p.drawLine(QPointF(x, field.y() + rail_pad),
                       QPointF(x, field.bottom() - rail_pad))

        points = [QPointF(self._band_x(i, field), self._gain_y(g, field))
                  for i, g in enumerate(self._gains)]

        # The response curve. **The sum of ten peaking filters, sampled across
        # the field** — not straight lines between the handles. A graphic EQ's
        # bands overlap by an octave, so two neighbours both lifted by 6 dB
        # produce more than 6 dB between them, and a join-the-dots curve draws
        # a flat shelf where the audio has a bump. This is what the pit
        # actually hears, and it is why the handles can sit off the line.
        path = QPainterPath()
        span = max(2, int(field.width() / 2))
        for step_i in range(span + 1):
            t = step_i / span
            x = field.x() + field.width() * t
            y = self._gain_y(self._response_at(t), field)
            path.moveTo(x, y) if step_i == 0 else path.lineTo(x, y)
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

        self._paint_analyser_values(p, field)
        self._paint_labels(p, field)
        p.end()

    def _response_at(self, t: float) -> float:
        """
        Combined gain in dB at position `t` (0..1) across the band span.

        Each band contributes a peaking bell one octave wide, which is the
        spacing of the bands themselves — the same assumption the analyser's
        filters make, so the curve and the bars describe one system.
        """
        # Band centres sit at the same x the handles do, in band units.
        pos = t * eq_module.N_BANDS - 0.5
        total = 0.0
        for i, gain in enumerate(self._gains):
            if gain == 0.0:
                continue
            d = pos - i
            # A bell in octave units: unity at the centre, ~a quarter one
            # octave away, negligible by two.
            total += gain * math.exp(-(d * d) / 0.72)
        return total

    def _paint_analyser(self, p: QPainter, field: QRectF):
        """Live band levels as bars behind the curve, with peak ticks."""
        analyser = self._analyser
        if analyser is None:
            return
        try:
            levels = analyser.levels()
            peaks = analyser.peaks()
        except Exception:
            return

        step = field.width() / eq_module.N_BANDS
        width = step * 0.44
        floor_y = field.bottom()
        p.setPen(Qt.PenStyle.NoPen)
        # **Colour encodes level, so it is anchored to the field and not to
        # the bar.** A gradient painted across each bar's own height would
        # make every band end in the same colour no matter how loud it is,
        # which is exactly backwards: the whole reason to colour a meter is
        # that a glance tells you *how hot* a band is without reading the
        # scale. Anchored here, a given height is always the same colour, so
        # two bands at the same level match and a hot one stands out.
        #
        # Every stop is a brand colour. It is the console ramp — cool and
        # quiet at the bottom, hot at the top — built out of the chart span
        # rather than an invented rainbow, and the red at the very top is the
        # brand's own fault colour doing the job it is defined for.
        ramp = QLinearGradient(0.0, floor_y, 0.0, field.y())
        for stop, colour in _LEVEL_RAMP:
            ramp.setColorAt(stop, QColor(colour))
        # **Readable first, behind second.** An earlier version was a 30%
        # wash, on the argument that the curve is the subject — but the whole
        # point of this element is telling an operator what each band is
        # doing, and a bar you have to squint at does not do that. It is still
        # behind the curve and still unsaturated, so the white line reads over
        # it; it is simply no longer apologetic.
        for i, db in enumerate(levels[:eq_module.N_BANDS]):
            frac = analyser_normalise(db)
            if frac <= 0.001:
                continue
            x = self._band_x(i, field)
            top = floor_y - field.height() * frac
            p.setBrush(ramp)
            p.drawRoundedRect(QRectF(x - width / 2, top, width, floor_y - top),
                              width * 0.22, width * 0.22)

        # Peak hold: a tick, not a filled bar. It marks where the band has
        # just been, which is the thing a bar cannot show.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i, db in enumerate(peaks[:eq_module.N_BANDS]):
            frac = analyser_normalise(db)
            if frac <= 0.001:
                continue
            x = self._band_x(i, field)
            y = floor_y - field.height() * frac
            # The tick spans its own bar and no more. Wider reads as a cap on
            # the bar rather than as a mark on the scale, and it collided with
            # the readout above.
            tick = QColor(_level_colour(frac))
            p.setPen(QPen(tick, max(1.5, field.height() * 0.010)))
            p.drawLine(QPointF(x - width / 2, y), QPointF(x + width / 2, y))


    def _paint_analyser_values(self, p: QPainter, field: QRectF):
        """
        The live level per band, in decibels. **Drawn last, over everything.**

        A bar says "louder than that one"; an operator reaching for a band
        wants to know by how much, and the graticule cannot be read to a
        decibel. It has to be the last thing painted because two other
        elements sit exactly where it does: the band rail runs vertically
        through the centre of each bar — which turned "−15" into "−.5", worse
        than no number at all because it is wrong rather than absent — and the
        peak tick lands on the same height whenever a band is steady.
        """
        analyser = self._analyser
        if analyser is None:
            return
        try:
            levels = analyser.levels()
            peaks = analyser.peaks()
        except Exception:
            return

        step = field.width() / eq_module.N_BANDS
        floor_y = field.bottom()
        p.setFont(_mono(10))
        for i, db in enumerate(levels[:eq_module.N_BANDS]):
            frac = analyser_normalise(db)
            if frac <= 0.02:
                continue
            top = max(frac, analyser_normalise(peaks[i])
                      if i < len(peaks) else frac)
            x = self._band_x(i, field)
            y = floor_y - field.height() * top
            p.setPen(QColor(_level_colour(frac)))
            p.drawText(QRectF(x - step / 2, y - 17, step, 13),
                       int(Qt.AlignmentFlag.AlignCenter), f"{db:.0f}")

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
