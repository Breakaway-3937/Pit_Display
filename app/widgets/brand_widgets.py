"""
Breakaway brand devices as reusable Qt widgets.

  ChamferFrame  — a container painted with The Cut (§6.1): two diagonally
                  opposite corners sheared at 45°. The core brand container.
  ChamferButton — a button with The Cut clip + the brand button system (§7):
                  primary / secondary / ghost variants that recolor on the fly.
  BreakLine     — The Break Line signature (§6.3): a leader that breaks to
                  horizontal and runs into the split roundel. Used once per
                  surface as a hero flourish / divider.
  eyebrow()     — Chakra Petch 600, uppercase, tracked — the brand eyebrow.

Qt has no CSS clip-path, so these paint themselves. Keep child content inside
generous margins so the sheared corners read cleanly.
"""

from PyQt6.QtWidgets import QFrame, QPushButton, QLabel, QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPolygonF, QPen, QBrush, QFont, QPainterPath,
)

from app import brand


# ── The Cut container ───────────────────────────────────────────────────────

class ChamferFrame(QFrame):
    """
    Panel painted with The Cut. `accent_edge` draws a short red marker along the
    top-left run — a restrained nod to the Break Line without stealing focus.
    """

    def __init__(
        self,
        fill: str = brand.CARBON_SURF,
        border: str | None = brand.CARBON_LINE,
        cut: int = brand.CUT_MEDIUM,
        accent_edge: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._fill = QColor(fill)
        self._border = QColor(border) if border else None
        self._cut = cut
        self._accent = QColor(accent_edge) if accent_edge else None

    def set_fill(self, hex_color: str):
        self._fill = QColor(hex_color)
        self.update()

    def set_border(self, hex_color: str | None):
        self._border = QColor(hex_color) if hex_color else None
        self.update()

    def set_accent(self, hex_color: str | None):
        self._accent = QColor(hex_color) if hex_color else None
        self.update()

    def _path(self) -> QPolygonF:
        pts = brand.cut_polygon(self.width(), self.height(), self._cut)
        return QPolygonF([QPointF(x, y) for x, y in pts])

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        poly = self._path()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._fill)
        p.drawPolygon(poly)

        if self._border:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self._border, 1))
            p.drawPolygon(poly)

        # Accent marker: short vertical bar just inside the top-left corner.
        if self._accent:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._accent)
            bar_h = max(18, min(self.height() - self._cut * 2, 46))
            p.drawRect(0, self._cut, 3, bar_h)

        p.end()


# ── The Cut button (brand button system §7) ─────────────────────────────────

class ChamferButton(QPushButton):
    """
    Button with The Cut and the brand's three variants.

      primary   — red fill, white text (default CTA)
      secondary — white/surface fill, red text + 1.5px red border
      ghost     — transparent, ink text

    `accent` overrides the red used for primary fill / secondary border — pass
    the active team color so buttons follow the team.
    """

    def __init__(
        self,
        text: str = "",
        variant: str = "primary",
        accent: str = brand.RED,
        cut: int = brand.CUT_SMALL,
        parent=None,
    ):
        super().__init__(text, parent)
        self._variant = variant
        self._accent = QColor(accent)
        self._cut = cut
        self._hover = False
        self._active = False   # sticky "selected" state (for toggle-style rows)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(38)
        f = QFont(brand.FONT_DISPLAY)
        f.setWeight(QFont.Weight.DemiBold)
        f.setPixelSize(14)
        self.setFont(f)

    def set_accent(self, hex_color: str):
        self._accent = QColor(hex_color)
        self.update()

    def set_variant(self, variant: str):
        self._variant = variant
        self.update()

    def set_active(self, active: bool):
        self._active = active
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def _colors(self):
        """Return (fill, text, border) QColors for the current state."""
        red = self._accent
        red_hover = QColor(brand.RED_HOVER)
        ember = QColor(brand.EMBER)
        pressed = self.isDown()

        if self._variant == "primary" or self._active:
            fill = ember if pressed else (red_hover if self._hover else red)
            return fill, QColor(brand.WHITE), None
        if self._variant == "secondary":
            if self._hover or pressed:
                return red, QColor(brand.WHITE), red
            return QColor(0, 0, 0, 0), red, red
        # ghost
        if self._hover or pressed:
            return QColor(red.red(), red.green(), red.blue(), 28), red, None
        return QColor(0, 0, 0, 0), QColor(brand.MUTED_DARK), None

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        cut = self._cut
        pts = brand.cut_polygon(self.width(), self.height(), cut)
        poly = QPolygonF([QPointF(x, y) for x, y in pts])

        fill, text_color, border = self._colors()

        p.setPen(Qt.PenStyle.NoPen)
        if fill.alpha() > 0:
            p.setBrush(fill)
            p.drawPolygon(poly)

        if border is not None:
            p.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(border, 1.5)
            p.setPen(pen)
            p.drawPolygon(poly)

        if not self.isEnabled():
            text_color = QColor(brand.N400)

        p.setPen(text_color)
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


# ── The Break Line signature (§6.3) ─────────────────────────────────────────

class BreakLine(QWidget):
    """
    A thin leader that enters at 45°, breaks to horizontal, and terminates in
    the split roundel. Ascending by default (momentum). Use once per surface as
    a hero flourish or section divider. Purely decorative — no interaction.
    """

    def __init__(
        self,
        color: str = brand.RED,
        diameter: int = 44,
        direction: str = "asc",
        parent=None,
    ):
        super().__init__(parent)
        self._color = QColor(color)
        self._d = diameter
        self._dir = direction
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(diameter + 24)

    def set_color(self, hex_color: str):
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        D = self._d
        y0 = h / 2                                    # level line == roundel split
        stroke = max(2.0, 0.11 * D)                   # matches ring weight
        cx = w - D / 2 - 2                             # roundel enters from right
        level_end = cx - D / 2
        break_x = max(level_end - D * 1.6, w * 0.32)  # elbow
        run = min(D * 1.3, break_x - 8)
        entry_x = break_x - run
        entry_y = y0 + run if self._dir == "asc" else y0 - run  # 45°

        pen = QPen(self._color, stroke, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(entry_x, entry_y)
        path.lineTo(break_x, y0)
        path.lineTo(level_end, y0)
        p.drawPath(path)

        # Split roundel: ring with a gap on the level line (the mark = destination)
        ring = stroke
        r = (D - ring) / 2
        rect = QRectF(cx - r, y0 - r, 2 * r, 2 * r)
        p.setPen(QPen(self._color, ring))
        # two arcs leaving a gap where the level line runs through
        p.drawArc(rect, int(8 * 16), int(344 * 16))
        p.end()


# ── Eyebrow label ───────────────────────────────────────────────────────────

def eyebrow(text: str, color: str = brand.RED) -> QLabel:
    """Chakra Petch 600, uppercase, ~0.2em tracking — the brand eyebrow."""
    lbl = QLabel(text.upper())
    f = QFont(brand.FONT_DISPLAY)
    f.setWeight(QFont.Weight.DemiBold)
    f.setPixelSize(12)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    return lbl
