"""
The alert banner along the bottom of both overhead screens.

    ┌──────────────────────────────────────────────────────────────────────┐
    │                       (whatever the screen is showing)               │
    │ ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄ │
    │  FIRST QUEUE        Qualification 24                    RED  ·  2    │
    └──────────────────────────────────────────────────────────────────────┘

A full-width band, 150 design px tall, filled in the alliance colour with
white type: the call on the left, the match in the middle, the driver station
on the right at a size a crew member reads from the workbench. Green for
inspection. It sits over whatever the screen is showing — a slide, the
checklist, the judges artwork — because being called to queue outranks all
of it, and it is the one thing on an overhead screen that may be red on
Breakaway's own screens: an exceptional state, which is what the budget is for.

It is a child of the window rather than a page in the stack, positioned by
`resizeEvent` and raised above the stack, so every page gets it without
knowing. `design_scale` sizes it exactly as the chassis sizes everything else.

Nothing animates. A band the colour of the alliance appearing along the bottom
of a 55" panel is the attention; a flash would be a second thing asking for it.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QWidget

from app import brand
from app.nexus.alerts import Banner, alerts
from app.widgets.chassis import design_scale

DESIGN_H = 150.0          # the band, at 1920×1080


class QueueBanner(QWidget):

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._banner: Banner | None = alerts.banner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        alerts.banner_changed.connect(self._on_banner)
        self.setVisible(self._banner is not None)

    def _on_banner(self, banner) -> None:
        self._banner = banner
        self.setVisible(banner is not None)
        if banner is not None:
            self.raise_()
            self.update()

    def place(self, window_w: int, window_h: int) -> None:
        """Called by the owning window's resizeEvent."""
        scale = design_scale(window_w, window_h)
        h = int(round(DESIGN_H * scale))
        self.setGeometry(0, window_h - h, window_w, h)
        self.raise_()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        b = self._banner
        if b is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        scale = h / DESIGN_H

        def s(v: float) -> float:
            return v * scale

        p.fillRect(self.rect(), QColor(b.color))
        # A hairline above the band, so it reads as laid on rather than cut in.
        p.fillRect(QRectF(0, 0, w, max(1.0, s(3))), QColor(255, 255, 255, 70))

        p.setPen(QColor(brand.WHITE))
        pad = s(56)

        # The call, left: Chakra Petch 700, tracked.
        head = QFont(brand.FONT_DISPLAY)
        head.setWeight(QFont.Weight.Bold)
        head.setPixelSize(int(s(64)))
        head.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        p.setFont(head)
        head_rect = QRectF(pad, 0, w * 0.34, h)
        p.drawText(head_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   b.headline)

        # The station, right: the biggest thing on the band.
        station_txt = ""
        rest = b.detail
        if "  ·  " in b.detail:
            rest, station_txt = b.detail.rsplit("  ·  ", 1)
        right_w = 0.0
        if station_txt:
            big = QFont(brand.FONT_DISPLAY)
            big.setWeight(QFont.Weight.Bold)
            big.setPixelSize(int(s(84)))
            p.setFont(big)
            right_w = p.fontMetrics().horizontalAdvance(station_txt.upper()) + pad
            p.drawText(QRectF(w - pad - right_w + pad, 0, right_w - pad, h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       station_txt.upper())

        # The match, middle: medium weight, so the call and the station lead.
        mid = QFont(brand.FONT_DISPLAY)
        mid.setWeight(QFont.Weight.Medium)
        mid.setPixelSize(int(s(48)))
        p.setFont(mid)
        left_edge = head_rect.right() + s(24)
        right_edge = w - pad - right_w - s(24) if station_txt else w - pad
        p.drawText(QRectF(left_edge, 0, max(0.0, right_edge - left_edge), h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   rest)
        p.end()
