"""
Lunch overlay — drawn entirely in paintEvent.

All previous approaches used QLabel inside a layout; Qt's label size-hint
system capped the rendered font regardless of what pixel size was set.
paintEvent bypasses the layout engine entirely — font size is calculated
from the live widget dimensions every frame, guaranteed to fill the screen.
"""

from pathlib import Path

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import (
    QPainter, QPixmap, QFont, QFontMetrics, QColor, QPen, QPolygon,
    QResizeEvent, QShowEvent,
)

from app import brand
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


def _find_logo() -> Path | None:
    for name in _LOGO_NAMES:
        p = _LOGO_DIR / name
        if p.exists():
            return p
    return None


def _fit_font(text: str, weight: QFont.Weight, max_w: int, max_h: int,
              family: str = brand.FONT_DISPLAY) -> QFont:
    """Largest font pixel size where text fits within max_w × max_h."""
    f = QFont(family)
    f.setWeight(weight)
    size = max(8, min(max_h, max_w))
    f.setPixelSize(size)
    fm = QFontMetrics(f)
    while size > 8 and fm.horizontalAdvance(text) > max_w:
        size -= 2
        f.setPixelSize(size)
        fm = QFontMetrics(f)
    return f


def _font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal,
          family: str = brand.FONT_BODY) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(8, pixel_size))
    f.setWeight(weight)
    return f


class LunchOverlay(QWidget):

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(parent)
        self._screen_id = screen_id
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._logo_pixmap: QPixmap | None = None
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
        pal = brand.palette(theme)
        headline_color = brand.WHITE if theme != "light" else pal["ink"]

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
            p.setPen(QPen(QColor(pal["line"]), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(pal["surface"]))
            logo_max_w = min(700, w - 160)
            pr = QRect((w - logo_max_w) // 2, int(h * 0.06), logo_max_w, logo_h)
            p.drawRoundedRect(pr, 12, 12)

        # ── Eyebrow (Chakra Petch, tracked caps, team red) ────────────────
        team = config.active_team
        eyebrow = (f"{team.name} {team.number}" if team.name
                   else f"Team {team.number}").upper()
        accent = QColor(team.primary_color)
        eb_font = _font(max(12, int(h * 0.028)), QFont.Weight.DemiBold, brand.FONT_DISPLAY)
        eb_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 130)
        p.setFont(eb_font)
        p.setPen(accent)
        p.drawText(
            QRect(40, int(h * (_DIVIDER_Y_FRAC - 0.055)), w - 80, int(h * 0.05)),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            eyebrow,
        )

        # ── Accent divider — The Cut chamfered bar ────────────────────────
        div_w = max(160, min(320, w // 6))
        div_bar_h = max(5, h // 120)
        dx = (w - div_w) // 2
        dy = int(h * _DIVIDER_Y_FRAC)
        cut = min(div_bar_h * 2, 14)
        bar = QPolygon([
            QPoint(dx, dy),
            QPoint(dx + div_w - cut, dy),
            QPoint(dx + div_w, dy + cut),
            QPoint(dx + div_w, dy + div_bar_h),
            QPoint(dx + cut, dy + div_bar_h),
            QPoint(dx, dy + div_bar_h - cut),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawPolygon(bar)

        # ── Headline ─────────────────────────────────────────────────────
        headline_rect = QRect(
            40,
            int(h * _HEADLINE_Y_FRAC),
            w - 80,
            int(h * _HEADLINE_H_FRAC),
        )
        headline_font = _fit_font(
            _HEADLINE,
            QFont.Weight.Bold,
            headline_rect.width(),
            headline_rect.height(),
        )
        p.setFont(headline_font)
        p.setPen(QColor(headline_color))
        p.drawText(headline_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, _HEADLINE)

        # ── Sub-line ─────────────────────────────────────────────────────
        sub_px = min(int(h * 0.055), int((w - 160) // 38))
        p.setFont(_font(max(8, sub_px)))
        p.setPen(QColor(pal["muted"]))
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
