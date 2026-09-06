"""
Putting a pit screen on a pit monitor.

Every audience surface in this app is designed at a fixed size — 1920×1080 for
the two overhead panels, 1080×1920 for the pit-front panel — and everything on
them is scaled by a contain fit against that. **A window that is not filling a
monitor is therefore not showing the design**; it is showing a proportionally
smaller copy of it in whatever rectangle the window manager happened to choose.

That was the state of things: `show()` and nothing else, so each window opened
at its size hint, clamped to the primary display, stacked on top of the control
panel. On the pit machine that is wrong; on a one-monitor laptop it is the only
thing that can work, because a full-screen audience window with no chrome would
bury the control panel with no way back.

So the policy is per screen and stored in `config`:

    display     which monitor, by index. -1 = "wherever it opens"
    fullscreen  fill that monitor, or float as a windowed 16:9

and `default_fullscreen()` decides what a fresh install gets: **fill only when
there is somewhere else to put it.** One monitor means windowed, always, so an
operator can never lose the control panel behind a slide rotation.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QGuiApplication, QScreen

# What a windowed audience screen opens at, when it is not filling a monitor.
# 16:9 and 3:4-ish respectively, so the contain fit is 1.0 on both axes and the
# operator is previewing the real layout rather than a squashed one.
_WINDOWED = {
    "presentation_a": (1280, 720),
    "presentation_b": (1280, 720),
    "project":        (540, 960),
}


def screens() -> list[QScreen]:
    return list(QGuiApplication.screens())


def screen_names() -> list[str]:
    """Human labels for the display picker, in `screens()` order."""
    out = []
    for i, s in enumerate(screens()):
        g = s.geometry()
        name = s.name() or f"Display {i + 1}"
        out.append(f"{i + 1}. {name} — {g.width()}×{g.height()}")
    return out


def default_fullscreen() -> bool:
    """
    Fill the monitor only when there is more than one.

    On a single-display machine a full-screen audience window covers the
    control panel, and the control panel is the only way to turn it off again.
    """
    return len(screens()) > 1


def default_display(screen_id: str) -> int:
    """
    Which monitor a screen lands on before anyone chooses.

    The control panel keeps the primary display; the audience screens spread
    across whatever else exists, in the order the pit would hang them.
    """
    available = len(screens())
    if available <= 1:
        return 0
    order = ["presentation_a", "presentation_b", "project"]
    try:
        return min(order.index(screen_id) + 1, available - 1)
    except ValueError:
        return 0


def target_geometry(index: int) -> QRect | None:
    """The full geometry of monitor `index`, or None if it is gone."""
    all_screens = screens()
    if not all_screens:
        return None
    if index < 0 or index >= len(all_screens):
        # A monitor that was unplugged between sessions must not strand a
        # window off-canvas: fall back to the primary rather than to nothing.
        index = 0
    return all_screens[index].geometry()


def _show_fullscreen(window) -> None:
    """
    `showFullScreen()` without the `activateWindow()` it ends with.

    Qt's `QWidget::showFullScreen()` is three lines — clear the minimised and
    maximised bits, set the full-screen bit, show — and then it calls
    `activateWindow()` unconditionally. The two audience screens carry
    `WindowDoesNotAcceptFocus` on purpose, so that last line hit
    `QWindow::requestActivate()`'s guard and printed

        requestActivate() called for QWidgetWindow(…) which has
        Qt::WindowDoesNotAcceptFocus set.

    every single time one was powered on. Both halves are right — an audience
    panel must never take the keyboard from the operator, and it must fill its
    monitor — so this does the state change and leaves the activation out.
    """
    window.setWindowState(
        (window.windowState() & ~(Qt.WindowState.WindowMinimized
                                  | Qt.WindowState.WindowMaximized))
        | Qt.WindowState.WindowFullScreen)
    window.show()


def place(window, screen_id: str, index: int, fullscreen: bool) -> None:
    """
    Show `window` on monitor `index`, filling it or as a windowed 16:9.

    Order matters: the window has to be moved onto the target monitor *before*
    it goes full-screen, or Qt fills whichever monitor it was already on.
    """
    geo = target_geometry(index)
    if geo is None:
        window.show()
        return

    if window.isFullScreen():
        # Leaving full-screen first, so the move actually takes effect.
        window.showNormal()

    if fullscreen:
        window.setGeometry(geo)
        _show_fullscreen(window)
        handle = window.windowHandle()
        if handle is not None:
            handle.setScreen(screens()[max(0, min(index, len(screens()) - 1))])
        return

    w, h = _WINDOWED.get(screen_id, (1280, 720))
    w, h = min(w, geo.width() - 80), min(h, geo.height() - 80)
    window.showNormal()
    window.setGeometry(
        geo.x() + (geo.width() - w) // 2,
        geo.y() + (geo.height() - h) // 2,
        w, h)
    window.show()
