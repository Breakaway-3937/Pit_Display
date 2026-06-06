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
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import (
    QPainter, QPixmap, QFont, QFontMetrics, QColor, QPen, QResizeEvent, QShowEvent,
)

from app.config import config

_LOGO_DIR   = Path(__file__).parent.parent.parent / "assets" / "logos"
_LOGO_NAMES = ["2026 Wordmark.png", "breakaway_logo.png", "breakaway_logo.jpg"]

_HEADLINE = "We'll Be Right Back"
_SUB      = "Our team is on a lunch break and will return shortly.\nThank you for stopping by our pit!"

# Layout fractions of widget height
_LOGO_H_FRAC      = 0.22
_DIVIDER_Y_FRAC   = 0.34
_HEADLINE_Y_FRAC  = 0.40
_HEADLINE_H_FRAC  = 0.20
_SUB_Y_FRAC       = 0.64
_SUB_H_FRAC       = 0.22

# Dark / light color palettes
_DARK = {
    "bg":           "#080808",
    "headline":     "#ffffff",
    "sub":          "#606060",
    "ph_border":    "#2a2a2a",
    "ph_fill":      "#0f0f0f",
}
_LIGHT = {
    "bg":           "#f2f2f7",
    "headline":     "#1c1c1e",
    "sub":          "#636366",
    "ph_border":    "#d1d1d6",
    "ph_fill":      "#ffffff",
}


def _find_logo() -> Optional[Path]:
    for name in _LOGO_NAMES:
        p = _LOGO_DIR / name
        if p.exists():
            return p
    return None


def _fit_font(text: str, weight: QFont.Weight, max_w: int, max_h: int) -> QFont:
    """Largest font pixel size where text fits within max_w × max_h."""
    f = QFont()
    f.setWeight(weight)
    size = max(8, min(max_h, max_w))
    f.setPixelSize(size)
    fm = QFontMetrics(f)
    while size > 8 and fm.horizontalAdvance(text) > max_w:
        size -= 2
        f.setPixelSize(size)
        fm = QFontMetrics(f)
    return f


def _font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setPixelSize(max(8, pixel_size))
    f.setWeight(weight)
    return f


class LunchOverlay(QWidget):

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(parent)
        self._screen_id = screen_id
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._logo_pixmap: Optional[QPixmap] = None
        self._load_logo()
        config.team_changed.connect(self.update)
        config.screen_setting_changed.connect(self._on_setting_changed)

    def _on_setting_changed(self, screen: str, key: str, _value):
        if screen == self._screen_id and key == "theme":
            self.update()

    def _load_logo(self):
        path = _find_logo()
        if path:
            raw = QPixmap(str(path))
            self._logo_pixmap = raw.scaled(
                1400, 600,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.width(), self.height()

        theme = config.screen_theme(self._screen_id) if self._screen_id else "dark"
        pal = _LIGHT if theme == "light" else _DARK

        # Background
        p.fillRect(0, 0, w, h, QColor(pal["bg"]))

        # ── Logo ─────────────────────────────────────────────────────────
        logo_h = int(h * _LOGO_H_FRAC)
        if self._logo_pixmap:
            scaled = self._logo_pixmap.scaled(
                w - 160, logo_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lx = (w - scaled.width()) // 2
            ly = int(h * 0.06)
            p.drawPixmap(lx, ly, scaled)
        else:
            p.setPen(QPen(QColor(pal["ph_border"]), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(pal["ph_fill"]))
            logo_max_w = min(700, w - 160)
            pr = QRect((w - logo_max_w) // 2, int(h * 0.06), logo_max_w, logo_h)
            p.drawRoundedRect(pr, 12, 12)

        # ── Accent divider ───────────────────────────────────────────────
        div_color = QColor(config.active_team.primary_color)
        div_w = max(200, min(400, w // 5))
        div_bar_h = max(4, h // 135)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(div_color)
        p.drawRect((w - div_w) // 2, int(h * _DIVIDER_Y_FRAC), div_w, div_bar_h)

        # ── Headline ─────────────────────────────────────────────────────
        headline_rect = QRect(
            40,
            int(h * _HEADLINE_Y_FRAC),
            w - 80,
            int(h * _HEADLINE_H_FRAC),
        )
        headline_font = _fit_font(
            _HEADLINE,
            QFont.Weight.Black,
            headline_rect.width(),
            headline_rect.height(),
        )
        p.setFont(headline_font)
        p.setPen(QColor(pal["headline"]))
        p.drawText(headline_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, _HEADLINE)

        # ── Sub-line ─────────────────────────────────────────────────────
        sub_px = min(int(h * 0.055), int((w - 160) // 38))
        p.setFont(_font(max(8, sub_px)))
        p.setPen(QColor(pal["sub"]))
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
