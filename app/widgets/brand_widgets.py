"""
Breakaway brand devices as reusable Qt widgets (Brand System v2.0, §5).

  RoundedFrame  — the container for everything (§5.1): a rounded-rect panel,
                  filled (red/carbon, no border) or white with a hairline.
                  Optional `bracket` draws the Bracket accent on the ONE focal
                  element per surface.
  RoundedButton — a button with the Rounded shell + the brand button system
                  (§7): primary / secondary / ghost variants that recolor live.
  Bracket       — the focus accent (§5.2): four OPEN L-ticks at the corners of
                  one region; never closes into a box. `paint_bracket()` is the
                  painter helper RoundedFrame reuses.
  Pocket        — engineering scope (§5.3): a soft filleted triangle that
                  signals robot / CAD / machine content. Engineering surfaces
                  only. `pocket_path()` is the geometry helper.
  Trace         — the leading line (§5.4): a 45° diagonal entry that bends once
                  to flat and lands in a rectangular pad, leading the eye to a
                  headline / score. Use once per surface.
  eyebrow()     — Chakra Petch 600, uppercase, tracked — the brand eyebrow.

Qt has no CSS radius/clip on custom paints, so these paint themselves. One
accent device per surface (Bracket, Pocket, or Trace) — never stacked; Rounded
is the shell underneath. The logo is never a device.
"""

import math

from PyQt6.QtWidgets import QFrame, QPushButton, QLabel, QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath

from app import brand
from app.brand import FONT_MONO_STACK


# ── Bracket — focus accent (§5.2) ────────────────────────────────────────────

def paint_bracket(p: QPainter, x: float, y: float, w: float, h: float,
                  color: str = brand.RED, stroke: float = 3.5) -> None:
    """
    Four OPEN corner L-ticks framing the (x, y, w, h) region. Tick length is
    12–16% of the short side, clamped 12–28px. They never close into a box.
    """
    t = max(12.0, min(28.0, 0.14 * min(w, h)))
    pen = QPen(QColor(color), stroke, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def corner(ax, ay, bx, by, cx, cy):
        path = QPainterPath()
        path.moveTo(ax, ay)
        path.lineTo(bx, by)
        path.lineTo(cx, cy)
        p.drawPath(path)

    corner(x, y + t, x, y, x + t, y)                       # top-left
    corner(x + w - t, y, x + w, y, x + w, y + t)           # top-right
    corner(x, y + h - t, x, y + h, x + t, y + h)           # bottom-left
    corner(x + w - t, y + h, x + w, y + h, x + w, y + h - t)  # bottom-right


class Bracket(QWidget):
    """Transparent overlay that brackets its own rect — frame a hero photo/stat."""

    def __init__(self, color: str = brand.RED, stroke: float = 3.5, parent=None):
        super().__init__(parent)
        self._color = color
        self._stroke = stroke
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_color(self, hex_color: str):
        self._color = hex_color
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self._stroke
        paint_bracket(p, m, m, self.width() - 2 * m, self.height() - 2 * m,
                      self._color, self._stroke)
        p.end()


# ── Rounded — the container (§5.1) ───────────────────────────────────────────

class RoundedFrame(QFrame):
    """
    The default shell. Filled (red/carbon, no border) or a white/surface fill
    with a 1.5px hairline. Pass `bracket=<color>` to frame this element with the
    Bracket accent — reserve it for the single focal card/stat on the surface.
    """

    def __init__(
        self,
        fill: str = brand.CARBON_SURF,
        border: str | None = brand.CARBON_LINE,
        radius: int = brand.R_CARD,
        bracket: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._fill = QColor(fill)
        self._border = QColor(border) if border else None
        self._radius = radius
        self._bracket = bracket

    def set_fill(self, hex_color: str):
        self._fill = QColor(hex_color)
        self.update()

    def set_border(self, hex_color: str | None):
        self._border = QColor(hex_color) if hex_color else None
        self.update()

    def set_bracket(self, hex_color: str | None):
        self._bracket = hex_color
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        inset = 0.75 if self._border else 0.0
        rect = QRectF(inset, inset,
                      self.width() - 2 * inset, self.height() - 2 * inset)
        r = self._radius

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._fill)
        p.drawRoundedRect(rect, r, r)

        if self._border:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self._border, 1.5))
            p.drawRoundedRect(rect, r, r)

        if self._bracket:
            m = 6.0
            paint_bracket(p, m, m, self.width() - 2 * m, self.height() - 2 * m,
                          self._bracket, 3.5)
        p.end()


# ── Rounded button (brand button system §7) ──────────────────────────────────

class RoundedButton(QPushButton):
    """
    Button with the Rounded shell (10px) and the brand's three variants.

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
        radius: int = brand.R_BTN,
        parent=None,
    ):
        super().__init__(text, parent)
        self._variant = variant
        self._accent = QColor(accent)
        self._radius = radius
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
        accent = self._accent
        pressed = self.isDown()

        if self._variant == "primary" or self._active:
            # Hover/pressed shades derive from the accent so non-red team
            # colors darken instead of snapping to brand red.
            fill = (accent.darker(130) if pressed
                    else accent.darker(112) if self._hover
                    else accent)
            return fill, QColor(brand.WHITE), None
        if self._variant == "secondary":
            if self._hover or pressed:
                return accent, QColor(brand.WHITE), accent
            return QColor(0, 0, 0, 0), accent, accent
        # ghost
        if self._hover or pressed:
            return QColor(accent.red(), accent.green(), accent.blue(), 28), accent, None
        return QColor(0, 0, 0, 0), QColor(brand.MUTED_DARK), None

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        fill, text_color, border = self._colors()
        inset = 0.75 if border is not None else 0.0
        rect = QRectF(inset, inset,
                      self.width() - 2 * inset, self.height() - 2 * inset)
        r = self._radius

        p.setPen(Qt.PenStyle.NoPen)
        if fill.alpha() > 0:
            p.setBrush(fill)
            p.drawRoundedRect(rect, r, r)

        if border is not None:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(border, 1.5))
            p.drawRoundedRect(rect, r, r)

        if not self.isEnabled():
            text_color = QColor(brand.N400)

        p.setPen(text_color)
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


# ── Pocket — engineering scope (§5.3) ────────────────────────────────────────

def pocket_path(cx: float, cy: float, R: float, rot: float = 0.0,
                soft: float = 0.30) -> QPainterPath:
    """
    Rounded equilateral triangle (~30% edge fillet, never sharp). rot=0 → point
    up. Signals robot / CAD / machine content — engineering surfaces only.
    """
    pts = []
    for i in range(3):
        a = math.radians(rot + 120 * i - 90)
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))

    def unit(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        return dx / L, dy / L, L

    A, B = [], []
    for i in range(3):
        V, Pr, Nx = pts[i], pts[(i + 2) % 3], pts[(i + 1) % 3]
        upx, upy, lp = unit(V, Pr)
        unx, uny, ln = unit(V, Nx)
        t = soft * min(lp, ln)
        A.append((V[0] + upx * t, V[1] + upy * t))
        B.append((V[0] + unx * t, V[1] + uny * t))

    path = QPainterPath()
    path.moveTo(*B[0])
    for i in range(1, 3):
        path.lineTo(*A[i])
        path.quadTo(pts[i][0], pts[i][1], B[i][0], B[i][1])
    path.lineTo(*A[0])
    path.quadTo(pts[0][0], pts[0][1], B[0][0], B[0][1])
    path.closeSubpath()
    return path


class Pocket(QWidget):
    """
    A soft filleted triangle — the engineering marker. Solid red for a focus
    shape, or a neutral outline. Point-up by default. Engineering scope only
    (robot reveals, CAD/design, subsystem callouts) — never community/hype.
    """

    def __init__(self, color: str = brand.RED, rot: float = 0.0,
                 filled: bool = True, parent=None):
        super().__init__(parent)
        self._color = color
        self._rot = rot
        self._filled = filled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_color(self, hex_color: str):
        self._color = hex_color
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        R = min(w, h) / 2 - 2
        path = pocket_path(w / 2, h / 2, R, self._rot)
        if self._filled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self._color))
        else:
            p.setPen(QPen(QColor(self._color), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


# ── Trace — the leading line (§5.4) ──────────────────────────────────────────

class Trace(QWidget):
    """
    A leading line: a 45° diagonal enters from the bottom corner, bends ONCE to
    horizontal, and terminates in a rectangular pad — pointing the eye at the
    headline / score. Use once per surface. Purely decorative — no interaction.

      direction "left"  → enters bottom-left, pad on the right (default)
      direction "right" → mirrored (enters bottom-right, pad on the left)
    """

    def __init__(
        self,
        color: str = brand.RED,
        stroke: float = 6.0,
        direction: str = "left",
        parent=None,
    ):
        super().__init__(parent)
        self._color = QColor(color)
        self._stroke = stroke
        self._dir = direction
        self._drop = max(20.0, 4.0 * stroke)   # 45° vertical travel of the entry
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(int(2 * stroke + self._drop))

    def set_color(self, hex_color: str):
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        s = self._stroke
        drop = min(self._drop, h - s)
        y0 = s                                  # flat leg near the top
        pad_w, pad_h = 2.5 * s, 1.5 * s

        pen = QPen(self._color, s, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        if self._dir == "right":
            entry_x, bend_x = w - s / 2, w - s / 2 - drop   # enter bottom-right
            flat_end = s / 2 + pad_w
            path.moveTo(entry_x, y0 + drop)
            path.lineTo(bend_x, y0)
            path.lineTo(flat_end, y0)
            pad_x = s / 2
        else:
            entry_x, bend_x = s / 2, s / 2 + drop           # enter bottom-left
            flat_end = w - s / 2 - pad_w
            path.moveTo(entry_x, y0 + drop)
            path.lineTo(bend_x, y0)
            path.lineTo(flat_end, y0)
            pad_x = w - s / 2 - pad_w
        p.drawPath(path)

        # Rectangular pad terminal (radius 2) just past the headline.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawRoundedRect(QRectF(pad_x, y0 - pad_h / 2, pad_w, pad_h), 2, 2)
        p.end()


# ── Mono font (data / numbers) ──────────────────────────────────────────────

def mono_font(px: int | None = None, tabular: bool = True) -> QFont:
    """
    The brand mono role (§2) with its full fallback stack.

    Always set the whole stack rather than a bare family name: if Qt cannot
    resolve the family it sweeps every font alias on the system to look for it,
    which is slow and noisy. `tabular` turns on lining figures so columns of
    digits line up — the reason this role exists.
    """
    f = QFont()
    f.setFamilies(FONT_MONO_STACK)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(px if px is not None else brand.TYPE["mono"]["px"])
    if tabular:
        f.setStyleStrategy(QFont.StyleStrategy.PreferDefault)
        try:
            f.setFeature("tnum", 1)          # Qt 6.7+
        except (AttributeError, TypeError):
            pass                              # older Qt: lining figures anyway
    return f


# ── Eyebrow label ───────────────────────────────────────────────────────────

def eyebrow(text: str, color: str = brand.RED) -> QLabel:
    """Chakra Petch 600, uppercase, +0.16em tracking — the brand eyebrow (§2)."""
    lbl = QLabel(text.upper())
    f = QFont(brand.FONT_DISPLAY)
    f.setWeight(QFont.Weight.DemiBold)
    f.setPixelSize(brand.TYPE["eyebrow"]["px"])
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing,
                       brand.TYPE["eyebrow"]["track"])
    lbl.setFont(f)
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    return lbl
