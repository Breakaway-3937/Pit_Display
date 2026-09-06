"""
The Plate — the chassis every full-screen surface is built on.

One architectural inset panel floating on the app ground: the screen gets a
physical edge and the type gets a room to sit in. The presentation slides, both
robot boards and the project screen all sit on this, which is what makes them
read as one instrument when two of them are visible in the same glance.

**Geometry is declared at the design size and scaled by a contain fit.** Every
number in `_M` is a design measurement; `s()` converts it for the live widget,
against `min(w/DESIGN_W, h/DESIGN_H)`. A pit panel is 1080 tall and a dev window
is not, so nothing here may be a literal pixel count — that was how the old
lunch overlay ended up with type sized for one machine. And it fits *both* axes,
because a single-axis rule blows a portrait design up by 1.78× the moment its
window opens wide and short.

    ┌─ 40px inset ─────────────────────────────────────┐
    │  BREAKAWAY 3937              SCREEN A / STANDARD │  header band, top 56
    │ ──────────────────────────────────────────────── │  2px rule at 150
    │                                                  │
    │  the stage — 216 down to height−168              │  subclasses paint here
    │                                                  │
    │ ──────────────────────────────────────────────── │  2px rule
    │  01 / 07   ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔     ROTATION A │  footer ledger
    └──────────────────────────────────────────────────┘

Subclasses override `paint_stage()` and, when they want something other than
two mono strings, `paint_footer()`. The header's right-hand side is data:
`header_right()` returns a list of items the base paints in order, so a board
can put a breathing status dot in front of its label and a telemetry slide can
put a Pocket and a FROM LOG seal after it, without either of them re-painting
the band.

**Why the background is cached.** The ambient light-fall drifts over 34s, so
the ground repaints on a timer; rebuilding the plate gradient and its cast
shadow on every tick would be pure waste. `_ground` holds ground + shadow +
plate and is only rebuilt on resize or theme change.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, QSize
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QPixmap, QLinearGradient, QRadialGradient,
    QPainterPath, QFontMetricsF,
)
from PyQt6.QtWidgets import QWidget, QSizePolicy

from app import brand
from app.brand import FONT_MONO_STACK
from app.config import config
from app.widgets.brand_widgets import pocket_path


# ── Design measurements, at 1920×1080 ───────────────────────────────────────
# Screen-relative, not plate-relative: the plate is inset 40 and the content
# margin is 64, so the content sits 24px inside the plate edge.
_M = {
    "plate_inset":  40,
    "plate_radius": 18,
    "margin":       64,    # content left/right, from the screen edge
    "header_top":   56,
    "header_h":     94,    # rule lands at 150
    "stage_top":    216,
    "stage_bottom": 168,   # stage ends this far up from the screen edge
    "footer_bot":   56,
    "footer_pad":   26,    # gap between the footer rule and its content
    "rule":         2,
    "wordmark":     34,
    "mono":         22,
}


def design_scale(width: int, height: int,
                 design_w: float = 1920.0, design_h: float = 1080.0) -> float:
    """
    How much of the design this widget actually has — a **contain fit**.

    `min(w/dw, h/dh)`, never one axis alone. Scaling by height only is right
    until a window is wide and short — a portrait panel opened at 1920×1000
    then scaled its 1080-wide design by 1.78 and drew a 1153px CAD stage into a
    1000px window, which is not a clipped edge but a layout collapsing through
    itself. Fitting both axes cannot do that, and on a panel at the design's own
    aspect it is identical to either single-axis rule.

    Clamped at the bottom so a small dev window degrades into "the same layout,
    smaller" rather than into unreadable one-pixel rules.
    """
    return max(0.28, min(width / design_w, height / design_h))


class Chassis(QWidget):
    """The plate, its header band and its footer ledger. Paints itself."""

    # Right-hand mono label in the header band, e.g. "SCREEN A  /  STANDARD".
    HEADER_LABEL = ""

    # The size the design was drawn at. Everything scales as a contain fit
    # against this, so a surface keeps its proportions at any window shape and
    # a wide-but-short window shrinks rather than overflowing. The pit-front
    # panel overrides it to portrait.
    DESIGN_W = 1920.0
    DESIGN_H = 1080.0

    # Distance from the screen edge to the plate. Portrait surfaces sit closer
    # to their edge — 28 rather than 40 — because a tall panel has less room to
    # give away and is looked at from a metre, not from fifteen feet.
    PLATE_INSET = _M["plate_inset"]

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(parent)
        self.screen_id = screen_id
        self._theme = config.screen_theme(screen_id) if screen_id else "dark"
        self._ground: QPixmap | None = None
        self._ground_key: tuple = ()
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        # The light-fall drifts 70px over 34s — about 2px a second, so a 2s
        # tick is smooth enough and costs one blit. It exists to keep a 55"
        # panel of near-black from looking switched off, not to be noticed.
        self._t0 = time.monotonic()
        self._drift = QTimer(self)
        self._drift.setInterval(2000)
        self._drift.timeout.connect(self.update)
        self._drift.start()

        if screen_id:
            config.screen_setting_changed.connect(self._on_setting_changed)

    # ── Theme ─────────────────────────────────────────────────────────────

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen == self.screen_id and key == "theme":
            self.set_theme(value)

    def set_theme(self, theme: str):
        if theme == self._theme:
            return
        self._theme = theme
        self._ground = None
        self.update()

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def dark(self) -> bool:
        return self._theme != "light"

    # Ink roles on the plate. Deliberately *not* the app palette bundle: the
    # plate is a lit surface, so its body ink is brighter than a control's.
    @property
    def ink(self) -> str:
        return brand.WHITE if self.dark else brand.CARBON

    @property
    def body_ink(self) -> str:
        return brand.N300 if self.dark else brand.N600

    @property
    def muted(self) -> str:
        return brand.N400 if self.dark else brand.N500

    @property
    def faint(self) -> str:
        return brand.N500 if self.dark else brand.N400

    @property
    def rule(self) -> str:
        return brand.CARBON_LINE if self.dark else brand.N200

    @property
    def tile(self) -> str:
        return brand.TILE_DARK if self.dark else brand.TILE_LIGHT

    # ── Metrics ───────────────────────────────────────────────────────────

    def scale(self) -> float:
        """This widget's contain fit against the design size."""
        return design_scale(self.width(), self.height(),
                            self.DESIGN_W, self.DESIGN_H)

    def s(self, design_px: float) -> float:
        """Convert a design measurement to this widget's size."""
        return design_px * self.scale()

    def m(self, key: str) -> float:
        return self.s(_M[key])

    def plate_rect(self) -> QRectF:
        i = self.s(self.PLATE_INSET)
        return QRectF(i, i, self.width() - 2 * i, self.height() - 2 * i)

    def content_rect(self) -> QRectF:
        """Left/right bounds of everything the chassis draws inside the plate."""
        x = self.m("margin")
        return QRectF(x, 0, max(1.0, self.width() - 2 * x), self.height())

    def header_rect(self) -> QRectF:
        c = self.content_rect()
        return QRectF(c.x(), self.m("header_top"), c.width(), self.m("header_h"))

    def stage_rect(self) -> QRectF:
        c = self.content_rect()
        top = self.m("stage_top")
        bottom = self.height() - self.m("stage_bottom")
        return QRectF(c.x(), top, c.width(), max(1.0, bottom - top))

    def footer_rect(self) -> QRectF:
        """Below the footer rule: where the ledger content sits."""
        c = self.content_rect()
        rule_y = self.height() - self.m("stage_bottom")
        bottom = self.height() - self.m("footer_bot")
        return QRectF(c.x(), rule_y + self.m("footer_pad"), c.width(),
                      max(1.0, bottom - rule_y - self.m("footer_pad")))

    # ── Fonts ─────────────────────────────────────────────────────────────

    def display(self, px: float, weight: int = 700, track_em: float = 0.0) -> QFont:
        f = QFont(brand.FONT_DISPLAY)
        f.setWeight(QFont.Weight(weight))
        f.setPixelSize(max(1, int(round(self.s(px)))))
        if track_em:
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing,
                               100.0 + track_em * 100.0)
        return f

    def body(self, px: float, weight: int = 400) -> QFont:
        f = QFont(brand.FONT_BODY)
        f.setWeight(QFont.Weight(weight))
        f.setPixelSize(max(1, int(round(self.s(px)))))
        return f

    def mono(self, px: float, weight: int = 500, track_em: float = 0.0) -> QFont:
        f = QFont()
        f.setFamilies(FONT_MONO_STACK)
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setWeight(QFont.Weight(weight))
        f.setPixelSize(max(1, int(round(self.s(px)))))
        if track_em:
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing,
                               100.0 + track_em * 100.0)
        return f

    # ── Painting ──────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self._paint_ground(p)

        plate = self.plate_rect()
        clip = QPainterPath()
        r = self.m("plate_radius")
        clip.addRoundedRect(plate, r, r)
        p.save()
        p.setClipPath(clip)
        self.paint_plate_content(p)
        p.restore()
        p.end()

    def paint_plate_content(self, p: QPainter):
        """Everything inside the plate, clipped to its rounded rect."""
        self.paint_header(p)
        self.paint_stage(p, self.stage_rect())
        self.paint_footer(p, self.footer_rect())

    # ── Ground + plate (cached) ───────────────────────────────────────────

    def _paint_ground(self, p: QPainter):
        key = (self.width(), self.height(), self._theme)
        if self._ground is None or self._ground_key != key:
            self._ground = self._render_ground()
            self._ground_key = key
        p.drawPixmap(0, 0, self._ground)

        if self.dark:
            self._paint_lightfall(p)

    def _render_ground(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(brand.CARBON_BG if self.dark else "#EEEBEA"))

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plate = self.plate_rect()
        r = self.m("plate_radius")

        if not self.dark:
            # The light theme keeps the same depth logic: the ambient wash
            # becomes a cast shadow rather than being swapped for a flat fill.
            # Stacked rounded rects at low alpha — a real blur would need a
            # graphics effect, which cannot be cached this cheaply.
            steps = 14
            spread = self.s(30)
            for i in range(steps, 0, -1):
                grow = spread * i / steps
                a = int(26 * (1 - i / steps) ** 1.6) + 2
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(24, 20, 22, a))
                p.drawRoundedRect(
                    plate.adjusted(-grow, -grow * 0.35, grow, grow * 1.15),
                    r + grow, r + grow)

        stops = brand.PLATE_DARK if self.dark else brand.PLATE_LIGHT
        # 157° clockwise from 12 o'clock: down and slightly to the left.
        grad = QLinearGradient(plate.topRight(), plate.bottomLeft())
        grad.setColorAt(0.0, QColor(stops[0]))
        grad.setColorAt(0.46 if self.dark else 0.50, QColor(stops[1]))
        grad.setColorAt(1.0, QColor(stops[2]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(plate, r, r)

        # The lit top edge on dark; a hairline on light.
        if self.dark:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(243, 241, 240, 18), max(1.0, self.s(1.5))))
            p.drawRoundedRect(plate.adjusted(0.75, 0.75, -0.75, -0.75), r, r)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(brand.N200), max(1.0, self.s(1.5))))
            p.drawRoundedRect(plate.adjusted(0.75, 0.75, -0.75, -0.75), r, r)
        p.end()
        return pm

    def _paint_lightfall(self, p: QPainter):
        """
        The ambient wash, drifting on a 34s alternating cycle.

        It sits *behind* the plate, so what actually shows is a slow glow in the
        margin around the panel — a lit room, not a gradient on the artwork.
        """
        phase = ((time.monotonic() - self._t0) % (2 * brand.LIGHTFALL_SECS))
        t = phase / brand.LIGHTFALL_SECS
        if t > 1.0:
            t = 2.0 - t
        # ease-in-out, matching the comp's timing function
        t = t * t * (3 - 2 * t)

        cx = self.s(450) + self.s(70) * t
        cy = self.s(390) - self.s(34) * t
        radius = self.s(750) * (1.0 + 0.08 * t)

        grad = QRadialGradient(QPointF(cx, cy), radius)
        a = int(255 * brand.LIGHTFALL_ALPHA)
        grad.setColorAt(0.0, QColor(243, 241, 240, a))
        grad.setColorAt(0.62, QColor(243, 241, 240, 0))
        grad.setColorAt(1.0, QColor(243, 241, 240, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

    # ── Header band ───────────────────────────────────────────────────────

    def header_right(self) -> list[tuple]:
        """
        Items painted right-to-left in the header band. Each is a tuple:

            ("mono",   text)                 mono label in the faint ink
            ("dot",    hex, breathing:bool)  status dot
            ("seal",   text, hex)            filled pill, white type — one red
            ("pocket", hex)                  the engineering marker
        """
        return [("mono", self.HEADER_LABEL)] if self.HEADER_LABEL else []

    def paint_header(self, p: QPainter):
        rect = self.header_rect()

        # BREAKAWAY 3937 — the identity is the type. No wordmark artwork: it
        # would need a white media plate, which breaks the plate's surface, and
        # it would spend budget the functional shapes need.
        team = config.active_team
        name = (team.name or "Team").upper()
        number = str(team.number)

        f_name = self.display(_M["wordmark"], 700, 0.02)
        p.setFont(f_name)
        fm = QFontMetricsF(f_name)
        baseline = rect.y() + fm.ascent()
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(rect.x(), baseline), name + " ")
        x = rect.x() + fm.horizontalAdvance(name + " ")

        f_num = self.display(_M["wordmark"], 500, 0.02)
        p.setFont(f_num)
        p.setPen(QColor(self.faint))
        p.drawText(QPointF(x, baseline), number)

        self._paint_header_right(p, rect)

        # The 2px rule closing the band.
        p.setPen(QPen(QColor(self.rule), self.m("rule")))
        y = rect.bottom()
        p.drawLine(QPointF(rect.x(), y), QPointF(rect.right(), y))

    def _paint_header_right(self, p: QPainter, rect: QRectF):
        items = self.header_right()
        if not items:
            return
        f_mono = self.mono(_M["mono"], 500, 0.14)
        fm = QFontMetricsF(f_mono)
        gap = self.s(22)
        cy = rect.y() + fm.ascent() * 0.72

        # Measure right-to-left, then paint left-to-right so the run ends flush.
        widths = []
        for item in items:
            if item[0] == "mono":
                widths.append(fm.horizontalAdvance(item[1]))
            elif item[0] == "dot":
                widths.append(self.s(12))
            elif item[0] == "seal":
                widths.append(fm.horizontalAdvance(item[1]) + self.s(40))
            elif item[0] == "pocket":
                widths.append(self.s(40))
        total = sum(widths) + gap * (len(items) - 1)
        x = rect.right() - total

        for item, w in zip(items, widths):
            if item[0] == "mono":
                p.setFont(f_mono)
                p.setPen(QColor(self.faint))
                p.drawText(QPointF(x, cy + fm.ascent() / 2 - fm.descent() / 2),
                           item[1])
            elif item[0] == "dot":
                colour = QColor(item[1])
                if len(item) > 2 and item[2]:
                    # A 6s breathe, the only motion on a board. Says the feed
                    # is live; never used to call attention to a fault.
                    ph = (time.monotonic() - self._t0) % 6.0 / 6.0
                    import math
                    colour.setAlphaF(0.35 + 0.65 * (0.5 - 0.5 * math.cos(
                        2 * math.pi * ph)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(colour)
                d = self.s(12)
                p.drawEllipse(QPointF(x + d / 2, cy), d / 2, d / 2)
            elif item[0] == "seal":
                h = fm.height() + self.s(18)
                r = QRectF(x, cy - h / 2, w, h)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(item[2]))
                p.drawRoundedRect(r, h / 2, h / 2)
                p.setFont(f_mono)
                p.setPen(QColor(brand.WHITE))
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), item[1])
            elif item[0] == "pocket":
                d = self.s(40)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(item[1]))
                p.drawPath(pocket_path(x + d / 2, cy, d / 2, rot=132))
            x += w + gap

    # ── Stage / footer hooks ──────────────────────────────────────────────

    def paint_stage(self, p: QPainter, rect: QRectF):
        """Subclasses paint their content here. `rect` is the shared stage."""

    def footer_items(self) -> tuple[str, str]:
        """(left, right) mono strings for the default ledger."""
        return "", ""

    def paint_footer(self, p: QPainter, rect: QRectF):
        rule_y = self.height() - self.m("stage_bottom")
        c = self.content_rect()
        p.setPen(QPen(QColor(self.rule), self.m("rule")))
        p.drawLine(QPointF(c.x(), rule_y), QPointF(c.right(), rule_y))

        left, right = self.footer_items()
        if not (left or right):
            return
        f = self.mono(20, 500, 0.12)
        p.setFont(f)
        p.setPen(QColor(self.faint))
        if left:
            p.drawText(rect, int(Qt.AlignmentFlag.AlignLeft |
                                 Qt.AlignmentFlag.AlignBottom), left)
        if right:
            p.drawText(rect, int(Qt.AlignmentFlag.AlignRight |
                                 Qt.AlignmentFlag.AlignBottom), right)

    # ── Shared drawing helpers for subclasses ─────────────────────────────

    def draw_rail(self, p: QPainter, rect: QRectF, fraction: float):
        """The dwell rail: a filled track, `fraction` complete."""
        h = rect.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.rule))
        p.drawRoundedRect(rect, h / 2, h / 2)
        w = max(0.0, min(1.0, fraction)) * rect.width()
        if w > 0:
            fill = QColor(brand.N50 if self.dark else brand.CARBON)
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(rect.x(), rect.y(), max(h, w), h),
                              h / 2, h / 2)

    def paint_ground_under(self, p: QPainter, child: QWidget) -> None:
        """
        Paint the plate as it appears beneath `child`.

        A child widget on the plate has no background of its own, and giving it
        a flat one shows a seam across the gradient. The ground is already
        cached as a pixmap, so the honest answer is to draw that pixmap offset
        by the child's position and let the clip do the rest.
        """
        if self._ground is None:
            self._ground = self._render_ground()
            self._ground_key = (self.width(), self.height(), self._theme)
        pos = child.mapTo(self, child.rect().topLeft())
        p.save()
        p.translate(-pos.x(), -pos.y())
        p.drawPixmap(0, 0, self._ground)
        p.restore()


    def draw_text_block(self, p: QPainter, rect: QRectF, text: str, font: QFont,
                        colour: str, flags=None) -> QRectF:
        """Word-wrapped text, returning the rect it actually occupied."""
        p.setFont(font)
        p.setPen(QColor(colour))
        if flags is None:
            flags = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop |
                     Qt.TextFlag.TextWordWrap)
        bounds = p.boundingRect(rect, int(flags), text)
        p.drawText(rect, int(flags), text)
        return bounds

    def draw_wrapped(self, p: QPainter, x: float, y: float, max_w: float,
                     text: str, font: QFont, colour: str,
                     line_height: float = 1.0, align: str = "left",
                     measure_only: bool = False) -> float:
        """
        Word-wrapped text with an explicit line height, returning the y of the
        bottom of the last line.

        `QPainter.drawText()` spaces lines by the font's own leading, which for
        a 140px display face is far looser than the design's 1.0 — so the
        headline that fits in the comp runs off the stage. `QTextLayout` is the
        only way to set the line box directly, so every multi-line block on
        these surfaces goes through here.
        """
        from PyQt6.QtGui import QTextLayout, QTextOption

        layout = QTextLayout(text, font)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(opt)

        step = font.pixelSize() * line_height
        lines = []
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max_w)
            lines.append(line)
        layout.endLayout()

        cursor = y
        for i, line in enumerate(lines):
            # Position by the design's line box, not by the font's ascent, so a
            # block's first baseline lands where the comp puts it.
            lx = x
            if align == "right":
                lx = x + max_w - line.naturalTextWidth()
            elif align == "center":
                lx = x + (max_w - line.naturalTextWidth()) / 2
            line.setPosition(QPointF(lx, cursor))
            cursor += step
        if not measure_only and lines:
            p.setPen(QColor(colour))
            layout.draw(p, QPointF(0, 0))
        if not lines:
            return y
        # Return the **ink** bottom, not the next line box's top. At a line
        # height of 1.0 the box is shorter than the glyphs, so returning the
        # box bottom put the caller's next block through this one's descenders
        # — "Bridge Brownout" and its sentence collided on any panel small
        # enough to compress the band.
        return max(cursor, cursor - step + QFontMetricsF(font).height())

    def sizeHint(self) -> QSize:
        return QSize(int(self.DESIGN_W), int(self.DESIGN_H))


class SmoothRail(QWidget):
    """
    A dwell rail that repaints on its own, ~20× a second.

    It is a child widget rather than part of the stage's `paintEvent` for one
    reason: the stage is a full-screen painted surface, and repainting all of
    it — a 140px headline, its layout, the plate — fast enough for a rail to
    look smooth is absurd. So the panel ticks at 500ms and the rail ticks at
    50ms, over its own few hundred square pixels.

    At 45 seconds and ~1700px the old 500ms tick stepped the fill nineteen
    pixels at a time, which reads as a stutter rather than as time passing.
    """

    _TICK_MS = 50

    def __init__(self, chassis: "Chassis", fraction, parent=None):
        super().__init__(parent or chassis)
        self._chassis = chassis
        self._fraction = fraction          # callable → 0..1
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._last = -1.0

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        """Only repaint when the fill has actually moved by a visible amount."""
        f = self._fraction()
        if abs(f - self._last) * max(1, self.width()) < 0.5:
            return
        self._last = f
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chassis.paint_ground_under(p, self)
        self._chassis.draw_rail(
            p, QRectF(0, 0, self.width(), self.height()), self._fraction())
        p.end()


class PlatePanel(Chassis):
    """
    The plate as a *container* rather than a painted surface.

    Same ground, same gradient, same depth logic — but the inside is a Qt
    layout instead of a `paint_stage()`. The pit-front panel needs that: it
    hosts a live `QWebEngineView` for the CAD and a scrollable card grid that
    fingers drag, neither of which can be a painted rect.

    `content_layout()` is the column inside the plate; its margins track the
    plate's padding as the panel resizes.
    """

    # Drawn at 1080×1920 portrait, not 1920×1080. Inheriting the landscape
    # size made this window open ~1920 wide on a real display, which is where
    # the 1.78× blow-up came from.
    DESIGN_W = 1080.0
    DESIGN_H = 1920.0
    PLATE_INSET = 28
    PAD_X = 40
    PAD_Y = 34

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        from PyQt6.QtWidgets import QVBoxLayout
        self._column = QVBoxLayout(self)
        self._column.setSpacing(20)
        self._sync_margins()

    def content_layout(self):
        return self._column

    def _sync_margins(self):
        i = int(round(self.s(self.PLATE_INSET)))
        self._column.setContentsMargins(
            i + int(round(self.s(self.PAD_X))),
            i + int(round(self.s(self.PAD_Y))),
            i + int(round(self.s(self.PAD_X))),
            i + int(round(self.s(self.PAD_Y))))
        self._column.setSpacing(int(round(self.s(20))))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_margins()

    # Children paint the content; the base only owes them the plate.
    def paint_plate_content(self, p: QPainter):
        pass
