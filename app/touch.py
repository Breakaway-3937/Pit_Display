"""
Multi-touch routing for the two touch panels.

Why this exists
---------------
Qt does not hand touch to plain QWidgets. Unless a widget opts in with
``WA_AcceptTouchEvents``, Qt *synthesizes* mouse events from the touch stream —
and that synthesis funnels every touchscreen in the pit through the single
application-wide mouse state: one pressed button, one implicit grab, one
release. The pit has two touch panels running at once, the operator's control
screen and the visitor-facing project screen, so two people touching at the
same time is not an edge case. It is Saturday afternoon.

Under synthesis that goes wrong like this:

    finger down on control   → synthetic press  → a control widget grabs
    finger down on project   → synthetic press  → second press, first still down
    first finger lifts       → one release      → the *other* widget stays
                                                  latched: a button drawn
                                                  pressed forever, a slider
                                                  still tracking a finger that
                                                  left the glass.

So both touch windows accept touch events instead, and this module routes them.
Every touch point gets its own target widget and its own press/move/release
lifecycle, keyed by ``(device, point id)``. Two panels are two devices; two
fingers on one panel are two ids. No state is shared between points, so no
point can steal another's.

What is deliberately NOT routed
-------------------------------
* **The CAD viewer.** ``QWebEngineView``'s internal render widget sets
  ``WA_AcceptTouchEvents`` on itself, so Qt targets *it* rather than the window
  and the router never sees those points — Chromium gets the raw multi-touch
  stream and runs its own pinch/pan. That is exactly what we want. Do **not**
  set the attribute recursively down the widget tree: doing so re-targets the
  touch at an ancestor and takes the gestures away from the web view.
* **Dialogs** — colour picker, file picker, password prompt. They are separate
  top-levels, are not registered here, and keep Qt's ordinary synthesis. Single
  touch is all a modal needs.

Drag versus tap
---------------
A finger that lands on a scrollable area and then travels more than
``_DRAG_SLOP`` is a scroll, not a tap. At that point the router releases the
widget it landed on *outside* the widget's own rect — Qt buttons only emit
``clicked`` when the release lands inside, so nothing fires — and drags the
scroll area instead. Without this, dragging to scroll the control screen's
settings column activates whatever happened to be under the fingertip.

Synthesized events carry the real touch device, so a widget can tell a tap from
a click with `is_touch()`. That matters for hover state: a touchscreen sends no
leaveEvent, so anything that paints itself on hover has to undo that itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PyQt6.QtGui import QEventPoint, QInputDevice, QMouseEvent, QPointingDevice
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QAbstractSlider, QApplication, QWidget,
)

# How far a point may travel and still count as a tap. Finger-sized on purpose:
# a real tap on a 27" panel wobbles several pixels.
_DRAG_SLOP = 12

# Local position used for the "release outside" that unlatches a widget when
# its press turns out to be the start of a scroll drag.
_NOWHERE = QPointF(-10000.0, -10000.0)

_TOUCH_TYPES = frozenset({
    QEvent.Type.TouchBegin,
    QEvent.Type.TouchUpdate,
    QEvent.Type.TouchEnd,
    QEvent.Type.TouchCancel,
})

_NO_BUTTON = Qt.MouseButton.NoButton
_LEFT      = Qt.MouseButton.LeftButton
_NO_MODS   = Qt.KeyboardModifier.NoModifier


def is_touch(event) -> bool:
    """True if a mouse event was synthesized from a finger rather than a mouse."""
    try:
        return event.deviceType() == QInputDevice.DeviceType.TouchScreen
    except (AttributeError, RuntimeError):
        return False


@dataclass
class _Point:
    """One finger, from touch-down to lift."""
    widget: QWidget
    press: QPoint
    last: QPoint
    area: QAbstractScrollArea | None = None
    scrolling: bool = False


def _scrollable_ancestor(widget: QWidget) -> QAbstractScrollArea | None:
    """
    The scroll area a drag starting on `widget` should scroll, or None.

    Returns None as soon as a slider is found on the way up — scrollbars, EQ
    faders and volume sliders own their own drags, and a scrollbar *is* a child
    of the scroll area it drives, so this check has to come first.
    """
    w = widget
    while w is not None:
        if isinstance(w, QAbstractSlider):
            return None
        if isinstance(w, QAbstractScrollArea):
            v, h = w.verticalScrollBar(), w.horizontalScrollBar()
            if v.maximum() > v.minimum() or h.maximum() > h.minimum():
                return w
            return None
        w = w.parentWidget()
    return None


class _TouchRouter(QObject):
    """Application event filter: one touch point in, one widget sequence out."""

    def __init__(self):
        super().__init__()
        self._windows: list[QWidget] = []
        self._points: dict[tuple[int, int], _Point] = {}

    # ── Registration ──────────────────────────────────────────────────────

    def register(self, window: QWidget) -> None:
        window.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        if window not in self._windows:
            self._windows.append(window)

    # ── Routing ───────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() not in _TOUCH_TYPES:
            return False
        # Anything that is not one of our registered top-levels — the web
        # view's render widget above all — keeps Qt's own handling.
        if obj not in self._windows:
            return False

        device = event.pointingDevice()
        dev_key = id(device)

        if event.type() == QEvent.Type.TouchCancel:
            self._cancel(dev_key)
            event.accept()
            return True

        for pt in event.points():
            key = (dev_key, pt.id())
            state = pt.state()
            if state == QEventPoint.State.Pressed:
                self._press(key, obj, pt, device)
            elif state == QEventPoint.State.Updated:
                self._move(key, pt, device)
            elif state == QEventPoint.State.Released:
                self._release(key, pt, device)
            # Stationary: nothing to send.

        event.accept()
        return True

    def _press(self, key, window: QWidget, pt, device) -> None:
        gpos = pt.globalPosition().toPoint()
        target = self._widget_at(window, gpos)
        if target is None:
            return
        # A press for a point id we still hold means we missed a release
        # (window hidden mid-touch, device reset). Drop the stale one.
        self._points.pop(key, None)
        accepted = _send(target, QEvent.Type.MouseButtonPress, gpos, device,
                         button=_LEFT, buttons=_LEFT, propagate=True)
        if accepted is None:
            return
        self._points[key] = _Point(
            widget=accepted, press=gpos, last=gpos,
            area=_scrollable_ancestor(accepted),
        )

    def _move(self, key, pt, device) -> None:
        p = self._points.get(key)
        if p is None:
            return
        gpos = pt.globalPosition().toPoint()

        if p.scrolling:
            self._scroll(p, gpos)
            p.last = gpos
            return

        if p.area is not None and (gpos - p.press).manhattanLength() > _DRAG_SLOP:
            _release_outside(p.widget, device)
            p.scrolling = True
            p.last = gpos
            return

        _send(p.widget, QEvent.Type.MouseMove, gpos, device,
              button=_NO_BUTTON, buttons=_LEFT)
        p.last = gpos

    def _release(self, key, pt, device) -> None:
        p = self._points.pop(key, None)
        if p is None:
            return
        if p.scrolling:
            return          # already unlatched when the drag was claimed
        _send(p.widget, QEvent.Type.MouseButtonRelease,
              pt.globalPosition().toPoint(), device,
              button=_LEFT, buttons=_NO_BUTTON)

    def _cancel(self, dev_key: int) -> None:
        """Device gave up on its points — unlatch everything it was holding."""
        for key in [k for k in self._points if k[0] == dev_key]:
            p = self._points.pop(key)
            if not p.scrolling:
                _release_outside(p.widget, None)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _widget_at(window: QWidget, gpos: QPoint) -> QWidget | None:
        local = window.mapFromGlobal(gpos)
        if not window.rect().contains(local):
            return None
        return window.childAt(local) or window

    @staticmethod
    def _scroll(p: _Point, gpos: QPoint) -> None:
        try:
            v, h = p.area.verticalScrollBar(), p.area.horizontalScrollBar()
        except RuntimeError:        # scroll area went away mid-drag
            p.scrolling = False
            return
        dx, dy = gpos.x() - p.last.x(), gpos.y() - p.last.y()
        if v.maximum() > v.minimum():
            v.setValue(v.value() - dy)
        if h.maximum() > h.minimum():
            h.setValue(h.value() - dx)


def _mouse_event(etype, local: QPointF, gpos: QPointF, device,
                 button, buttons) -> QMouseEvent:
    """A mouse event that still knows it came from a finger (see `is_touch`)."""
    if device is None:
        device = QPointingDevice.primaryPointingDevice()
    return QMouseEvent(etype, local, local, gpos, button, buttons, _NO_MODS, device)


def _send(widget: QWidget, etype, gpos: QPoint, device, *,
          button, buttons, propagate: bool = False) -> QWidget | None:
    """
    Deliver a synthesized mouse event, walking up the parent chain the way Qt
    propagates a press. Returns the widget that took it.

    The accept/ignore convention here is Qt's, and it is inverted from what you
    might expect: a mouse event arrives *accepted*, and `QWidget`'s default
    handler calls `ignore()`. So a widget that overrides `mousePressEvent` and
    says nothing about the flag has consumed the press, and propagation stops.
    Pre-clearing the flag instead would deliver the same press to the widget
    *and* every ancestor above it.
    """
    w = widget
    g = QPointF(gpos)
    while w is not None:
        try:
            ev = _mouse_event(etype, QPointF(w.mapFromGlobal(gpos)), g,
                              device, button, buttons)
            QApplication.sendEvent(w, ev)
        except RuntimeError:        # widget deleted mid-gesture
            return None
        if (ev.isAccepted() or not propagate or w.isWindow()
                or w.testAttribute(Qt.WidgetAttribute.WA_NoMousePropagation)):
            return w
        w = w.parentWidget()
    return None


def _release_outside(widget: QWidget, device) -> None:
    """
    Unlatch a widget without activating it: Qt's buttons only emit `clicked`
    when the release lands inside their own rect, so release far outside it.
    """
    try:
        ev = _mouse_event(QEvent.Type.MouseButtonRelease, _NOWHERE, _NOWHERE,
                          device, _LEFT, _NO_BUTTON)
        QApplication.sendEvent(widget, ev)
    except RuntimeError:
        pass


_router: _TouchRouter | None = None


def install(app: QApplication, *windows: QWidget) -> None:
    """
    Route touch for `windows` independently of each other and of the mouse.

    Pass only the top-level touch panels. Everything else in the app keeps
    Qt's default synthesis, which is correct for audience screens and modals.
    """
    global _router
    if _router is None:
        _router = _TouchRouter()
        app.installEventFilter(_router)
    for w in windows:
        _router.register(w)
