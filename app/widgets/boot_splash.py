"""
The boot screen: proof of life from the first second, and real progress after.

Starting on a pit machine is two waits, and only the second one is ours:

1. **Before Python runs.** Windows loads ~2,100 files and Defender scans every
   new DLL the first time it runs — after an update, that is all of them.
   Nothing in Qt can draw then. PyInstaller's launcher splash covers it: a PNG
   of *this widget*, rendered by `tools/make_splash.py`, shown by the
   bootloader before the interpreter starts (`packaging/pit_display.spec`).
2. **Our startup** (~0.5 s on the Mac): imports, the database, the services,
   building the control screen. This widget covers it with determinate steps.

The two are the same picture at the same size, so the hand-off (`main.py`
closes the launcher splash the moment this one is up) reads as one screen
whose status line starts moving, not two screens.

**Progress is honest.** Each `step()` names what is about to run and paints
before it runs; the bar is steps completed, never a timer. A step that hangs
leaves its own name on screen, which is exactly the thing somebody needs to
read out over the phone. There is no idle animation: the main thread is busy
between steps, so anything "animated" would freeze mid-motion and look like
a crash.

**The one red is the Trace**, drawn on as progress advances: the brand's
accent device doubling as the progress indicator, instead of a red bar next
to it. The bar itself is white on carbon, as every bar in this app is.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from app import brand
from app.widgets.brand_widgets import mono_font, paint_trace

W, H = 640, 360          # also the launcher PNG's size — keep them equal
PAD = 40
STATUS_Y = 292           # baseline of the status line; the launcher's text sits here too


def _font(family: str, px: int, weight: QFont.Weight, spacing: float = 0.0) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


class BootSplash(QWidget):

    def __init__(self, version: str, total_steps: int = 1):
        super().__init__(None, Qt.WindowType.SplashScreen
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self._version = version
        self._total = max(1, total_steps)
        self._done = 0
        self._status = "Starting…"
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - W // 2, geo.center().y() - H // 2)

    # ── Progress ─────────────────────────────────────────────────────────

    def set_total(self, total: int) -> None:
        self._total = max(1, total)

    def step(self, status: str) -> None:
        """Name the step about to run, paint it, then return so it can run."""
        self._status = status
        self.repaint()
        QApplication.processEvents()

    def advance(self) -> None:
        """The step that was named has finished."""
        self._done = min(self._total, self._done + 1)
        self.repaint()
        QApplication.processEvents()

    @property
    def fraction(self) -> float:
        return self._done / self._total

    # ── Painting ─────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        paint(p, self._version, self._status, self.fraction,
              f"{self._done} / {self._total}")
        p.end()


def paint(p: QPainter, version: str, status: str, fraction: float,
          counter: str, *, launcher: bool = False) -> None:
    """
    The whole picture. A free function so `tools/make_splash.py` renders the
    launcher PNG from exactly this code: `launcher=True` leaves the status line
    and counter empty, because the bootloader draws its own text there.
    """
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    # The plate: one rounded shape, banner radius, carbon with a hairline.
    plate = QPainterPath()
    plate.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), brand.R_BANNER, brand.R_BANNER)
    p.fillPath(plate, QColor(brand.CARBON_BG))
    p.setPen(QPen(QColor(brand.CARBON_LINE), 1))
    p.drawPath(plate)

    # Eyebrow, then the name — the same lockup every screen's header uses.
    p.setPen(QColor(brand.MUTED_DARK))
    p.setFont(_font(brand.FONT_DISPLAY, 12, QFont.Weight.DemiBold, 1.9))
    p.drawText(PAD, 62, "PIT DISPLAY")
    p.setPen(QColor(brand.WHITE))
    p.setFont(_font(brand.FONT_DISPLAY, 52, QFont.Weight.Bold))
    p.drawText(PAD - 2, 128, "BREAKAWAY 3937")

    # The Trace, drawn on as the boot advances. Its terminal circle is there
    # from the start: the line visibly heads for it, which is the progress.
    # Its run spans the name, so it lands where the lockup ends.
    paint_trace(p, PAD, 146, 440, brand.RED, max(0.0, min(1.0, fraction)))

    # Status line and step counter.
    if not launcher:
        p.setPen(QColor(brand.INK_DARK))
        p.setFont(_font(brand.FONT_BODY, 15, QFont.Weight.Normal))
        p.drawText(PAD, STATUS_Y, status)
        p.setPen(QColor(brand.FAINT_DARK))
        p.setFont(mono_font(12))
        p.drawText(QRectF(PAD, STATUS_Y - 14, W - 2 * PAD, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, counter)

    # The rail: a hairline track, white fill for steps done.
    rail = QRectF(PAD, 312, W - 2 * PAD, 2)
    p.fillRect(rail, QColor(brand.CARBON_LINE))
    if fraction > 0:
        p.fillRect(QRectF(rail.x(), rail.y(), rail.width() * fraction, rail.height()),
                   QColor(brand.WHITE))

    # Version, bottom right, the quietest thing on the plate.
    p.setPen(QColor(brand.FAINT_DARK))
    p.setFont(mono_font(11))
    p.drawText(QRectF(PAD, 322, W - 2 * PAD, 18),
               Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, version)
