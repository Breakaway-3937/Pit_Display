"""
Lunch overlay — drawn entirely in paintEvent.

All previous approaches used QLabel inside a layout; Qt's label size-hint
system capped the rendered font regardless of what pixel size was set.
paintEvent bypasses the layout engine entirely — font size is calculated
from the live widget dimensions every frame, guaranteed to fill the screen.
"""

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import (
    QPainter, QPixmap, QFont, QColor, QPen, QResizeEvent, QShowEvent,
)

from app.config import config

_LOGO_DIR   = Path(__file__).parent.parent.parent / "assets" / "logos"
_LOGO_NAMES = ["2026 Wordmark.png", "breakaway_logo.png", "breakaway_logo.jpg"]
_LOGO_MAX_W = 700
_LOGO_MAX_H = 300

_HEADLINE = "We'll Be Right Back"
_SUB      = "Our team is on a lunch break and will return shortly.\nThank you for stopping by our pit!"

# Heights as fraction of widget height
_LOGO_H_FRAC      = 0.22
_DIVIDER_Y_FRAC   = 0.34
_HEADLINE_Y_FRAC  = 0.40
_HEADLINE_H_FRAC  = 0.24   # bounding box height given to headline
_SUB_Y_FRAC       = 0.66
_SUB_H_FRAC       = 0.20


def _find_logo() -> Optional[Path]:
    for name in _LOGO_NAMES:
        p = _LOGO_DIR / name
        if p.exists():
            return p
    return None


def _font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setPixelSize(max(8, pixel_size))
    f.setWeight(weight)
    return f


class LunchOverlay(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._logo_pixmap: Optional[QPixmap] = None
        self._load_logo()
        config.team_changed.connect(self.update)   # repaint on team change

    def _load_logo(self):
        path = _find_logo()
        if path:
            raw = QPixmap(str(path))
            self._logo_pixmap = raw.scaled(
                _LOGO_MAX_W, _LOGO_MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor("#080808"))

        # ── Logo ─────────────────────────────────────────────────────────
        logo_h = int(h * _LOGO_H_FRAC)
        if self._logo_pixmap:
            pm = self._logo_pixmap
            # Scale to fit the logo zone height while respecting aspect ratio
            scaled = pm.scaled(
                w - 160, logo_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lx = (w - scaled.width()) // 2
            ly = int(h * 0.06)
            p.drawPixmap(lx, ly, scaled)
        else:
            # Placeholder outline
            p.setPen(QPen(QColor("#2a2a2a"), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor("#0f0f0f"))
            pr = QRect((w - _LOGO_MAX_W) // 2, int(h * 0.06), _LOGO_MAX_W, logo_h)
            p.drawRoundedRect(pr, 12, 12)

        # ── Accent divider ───────────────────────────────────────────────
        div_color = QColor(config.active_team.primary_color)
        div_w, div_h = 300, 8
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(div_color)
        p.drawRect((w - div_w) // 2, int(h * _DIVIDER_Y_FRAC), div_w, div_h)

        # ── Headline ─────────────────────────────────────────────────────
        headline_px = int(h * _HEADLINE_H_FRAC)
        p.setFont(_font(headline_px, QFont.Weight.Black))
        p.setPen(QColor("#ffffff"))
        headline_rect = QRect(
            40,
            int(h * _HEADLINE_Y_FRAC),
            w - 80,
            int(h * _HEADLINE_H_FRAC) + 20,
        )
        p.drawText(headline_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, _HEADLINE)

        # ── Sub-line ─────────────────────────────────────────────────────
        sub_px = int(h * 0.065)
        p.setFont(_font(sub_px))
        p.setPen(QColor("#606060"))
        sub_rect = QRect(
            80,
            int(h * _SUB_Y_FRAC),
            w - 160,
            int(h * _SUB_H_FRAC),
        )
        p.drawText(
            sub_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            _SUB,
        )

        p.end()

    # ── Keep repaint on resize ────────────────────────────────────────────

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.update()

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self.update()

    # ── Public ───────────────────────────────────────────────────────────

    def apply_fonts(self):
        """No-op — paintEvent handles sizing. Kept for call-site compatibility."""
        self.update()

    def reload_logo(self):
        self._load_logo()
        self.update()
