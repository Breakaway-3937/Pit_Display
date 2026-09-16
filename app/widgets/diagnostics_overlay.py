"""
Robot Diagnostics — Screen A's board. *Is anything wrong?*

Pinned content, not a slide: it is up for as long as the operator leaves it up,
next to a screen that is still rotating for visitors. So **nothing here cycles,
nothing is promoted, and nothing is hidden behind a beat you have to wait for.**

## The glance, in order

1. **The 150px status block** — colour alone, no reading.
2. **The 108px headline** — the mechanism, by name.
3. **Four vitals**, each carrying its shape across the match.
4. **The subsystem strip.**

A crew member mid-repair stops at step one or two; nobody has to reach step four
to know they are fine.

## Zero red when clean

A clean board has **no red pixel on it at all** — warn is amber, healthy is
`STATUS_ONLINE`, and the traces are grey. That is the whole reason red works
here: red appearing on this surface is an event, not decoration. When a fault
does latch, red marks **the fault and its origin only** — the count block, the
offending tile's dot and trace, and the offending subsystem. A second unrelated
fault raises the count to `2`; it does not paint a second region.

## Motion, almost none

One 6s breathing dot in the header, saying the feed is live. A fault arriving
does **not** flash: the board cross-fades to it and stays. Latched means
latched — the board never animates to get attention twice.

## Layout is banded, not measured

An earlier version measured where a list had landed and hid whatever fell past
the fold; it read geometry that had not settled and hid rows there was room for,
silently, which is the worst way for a diagnostics screen to be wrong. Row
counts come from `_budget()` and column counts from the width. Lists are sorted
worst-first upstream, so what a cap drops is always the healthy end.

Nothing here computes anything. If a figure looks wrong, `app/robot/diagnostics.py`
is where it is wrong.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen

from app import brand
from app.config import config
from app.robot import diagnostics as dg
from app.widgets.chassis import Chassis

_STATUS_COLOR = {
    dg.OK:    brand.STATUS_ONLINE,
    dg.WARN:  brand.STATUS_PENDING,
    dg.FAULT: brand.STATUS_FAULT,
    dg.IDLE:  brand.STATUS_IDLE,
}

# Design measurements at 1920×1080, from the comp.
_HEAD_BLOCK   = 150    # the status square, and the head band's height
_HEAD_PAD     = 44     # air above the head band
_HEAD_GAP     = 40     # square → headline
_STRIP_H      = 150    # the subsystem strip
_STRIP_PAD    = 26     # rule → strip content
_TILE_GAP     = 20
_TILE_PAD_X   = 30
_TILE_PAD_TOP = 26


def _band(height: int) -> str:
    """Which layout band this panel is in. Checked against a render at each."""
    if height >= 900:
        return "large"
    return "medium" if height >= 620 else "small"


class DiagnosticsOverlay(Chassis):
    """Screen A's board, on the shared chassis."""

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._data = dg.Dashboard()
        self.reload()
        # The robot panel emits this after an import or a delete. Reading the
        # board off a signal rather than a timer is what lets it be honest
        # about being a *log* view and not a live feed.
        config.logs_changed.connect(self.reload)
        config.team_changed.connect(self._repaint_on_team)

    def _repaint_on_team(self, *_args):
        # A bound method, not a lambda: this widget is destroyed with its
        # screen, and only a QObject method slot is auto-disconnected.
        self.update()

    def reload(self):
        try:
            self._data = dg.dashboard()
        except Exception:
            # A half-imported log must never take an overhead screen down.
            self._data = dg.Dashboard()
        self.update()

    # ── Chassis hooks ─────────────────────────────────────────────────────

    def header_right(self) -> list[tuple]:
        live = not self._data.empty
        return [("dot", brand.STATUS_ONLINE if live else brand.STATUS_IDLE, live),
                ("mono", "SCREEN A  /  DIAGNOSTICS")]

    def footer_items(self) -> tuple[str, str]:
        d = self._data
        if d.empty:
            return "NO LOG IMPORTED", "CONTROL → PIT SYSTEMS → ROBOT LOGS"

        left = []
        for label, reading in (("LOOP", "Loop time"), ("CAN ERRORS", "CAN errors")):
            r = next((x for x in d.vitals if x.label == reading), None)
            if r is not None:
                left.append(f"{label} {r.value}{(' ' + r.unit) if r.unit else ''}")
        brown = next((x for x in d.vitals if x.label == "Browned out"), None)
        if brown is not None:
            left.append(f"BROWNOUTS {brown.value.upper()}")

        stamp = "LATCHED" if d.worst == dg.FAULT else "UPDATED"
        when = (d.started_at or "")[-8:]
        right = " · ".join(x for x in (d.source_name, f"{stamp} {when}".strip())
                           if x)
        return "  ·  ".join(left), right

    # ── The stage ─────────────────────────────────────────────────────────

    def paint_stage(self, p: QPainter, rect: QRectF):
        if self._data.empty:
            self._paint_empty(p, rect)
            return

        head_h = self.s(_HEAD_PAD + _HEAD_BLOCK + _HEAD_PAD)
        head = QRectF(rect.x(), rect.y(), rect.width(), head_h)
        self._paint_head(p, head)

        strip_h = self.s(_STRIP_H + _STRIP_PAD + 24)
        subs = self._subsystems()
        if not subs:
            strip_h = 0.0

        grid = QRectF(rect.x(), head.bottom(), rect.width(),
                      rect.height() - head_h - strip_h)
        if grid.height() > self.s(120):
            self._paint_vitals(p, grid)

        if subs:
            strip = QRectF(rect.x(), rect.bottom() - strip_h + self.s(24),
                           rect.width(), strip_h - self.s(24))
            self._paint_strip(p, strip, subs)

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()),
                   "NO ROBOT LOG YET")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Nothing to diagnose", self.display(108, 700, -0.02),
                              self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Import a .hoot, .wpilog or Phoenix text export from Control → "
            "Pit Systems → Robot Logs and this board fills itself in.",
            self.body(32), self.body_ink, 1.4)

    # ── The head: status block + headline ─────────────────────────────────

    def _headline(self) -> tuple[str, str, str, int]:
        """(status, headline, sentence, fault count) for the head band."""
        d = self._data
        worst = d.worst
        faults = [f for f in d.faults if f.status == dg.FAULT]
        warns = [f for f in d.faults if f.status == dg.WARN]

        if worst == dg.FAULT and faults:
            first = faults[0]
            who = first.detail.split(", ")[0] if first.detail else ""
            rest = ("Everything else is inside band."
                    if len(faults) == 1 and not warns
                    else f"{len(faults) - 1 + len(warns)} other warning"
                         f"{'' if len(faults) - 1 + len(warns) == 1 else 's'} "
                         f"latched on this log.")
            return (dg.FAULT, first.label,
                    f"{first.label} latched on {first.value} {first.unit}"
                    + (f" — {who}. " if who else ". ") + rest,
                    len(faults))
        if worst == dg.WARN:
            hot = next((v for v in d.vitals if v.status == dg.WARN), None)
            head = warns[0].label if warns else (hot.label if hot else "Watch this")
            detail = (warns[0].detail if warns else (hot.detail if hot else ""))
            return (dg.WARN, head,
                    f"Nothing latched, but this is worth a look before the next "
                    f"match. {detail}".strip(), len(warns))
        return (dg.OK, "All clear",
                f"{d.duration_s / 60:.1f} minutes of match data, nothing "
                f"latched. Battery, current, heat and bus all inside band.", 0)

    def _paint_head(self, p: QPainter, rect: QRectF):
        status, headline, sentence, count = self._headline()
        side = self.s(_HEAD_BLOCK)
        top = rect.y() + self.s(_HEAD_PAD)
        block = QRectF(rect.x(), top, side, side)
        colour = _STATUS_COLOR.get(status, brand.STATUS_IDLE)
        r = self.s(18)

        p.setPen(Qt.PenStyle.NoPen)
        if status == dg.OK:
            # Clean: a bordered tile with a green disc. No fill, because a
            # filled block is the shape that means "something happened".
            p.setBrush(QColor(brand.TILE_DARK if self.dark else brand.N100))
            p.drawRoundedRect(block, r, r)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(colour), max(1.0, self.s(1.5))))
            p.drawRoundedRect(block, r, r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colour))
            d = self.s(56)
            p.drawEllipse(block.center(), d / 2, d / 2)
        else:
            p.setBrush(QColor(colour))
            p.drawRoundedRect(block, r, r)
            p.setFont(self.display(78, 700))
            p.setPen(QColor(brand.WHITE))
            p.drawText(block, int(Qt.AlignmentFlag.AlignCenter), str(count or 1))

        text_x = block.right() + self.s(_HEAD_GAP)
        text_w = rect.right() - text_x
        f_head = self.display(108, 700, -0.02)
        f_body = self.body(32)
        head_h = self.draw_wrapped(p, text_x, 0, text_w, headline, f_head,
                                   self.ink, 1.0, measure_only=True)
        body_h = self.draw_wrapped(p, text_x, 0, text_w, sentence, f_body,
                                   self.body_ink, 1.4, measure_only=True)
        y = block.center().y() - (head_h + self.s(18) + body_h) / 2
        y = self.draw_wrapped(p, text_x, y, text_w, headline, f_head,
                              self.ink, 1.0)
        self.draw_wrapped(p, text_x, y + self.s(18), text_w, sentence, f_body,
                          self.body_ink, 1.4)

    # ── Vitals ────────────────────────────────────────────────────────────

    def _vital_columns(self, width: float) -> int:
        """Four across on a pit panel; fewer only when the tile would elide."""
        return max(1, min(4, int(width // self.s(320)) or 1))

    def _paint_vitals(self, p: QPainter, rect: QRectF):
        # Four across is the cap, not the count: a log that only yielded three
        # vitals shows three full-width tiles rather than three-quarters of a
        # row and a hole where a fourth would have been.
        cols = min(self._vital_columns(rect.width()), len(self._data.vitals))
        rows = self._data.vitals[:cols]
        if not rows:
            return
        gap = self.s(_TILE_GAP)
        w = (rect.width() - gap * (cols - 1)) / cols
        for i, reading in enumerate(rows):
            self._paint_tile(p, QRectF(rect.x() + i * (w + gap), rect.y(),
                                       w, rect.height()), reading)

    def _paint_tile(self, p: QPainter, rect: QRectF, reading):
        faulted = reading.status == dg.FAULT
        r = self.s(14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.tile))
        p.drawRoundedRect(rect, r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(brand.FAULT_EDGE if faulted else self.rule),
                      max(1.0, self.s(1.5))))
        p.drawRoundedRect(rect, r, r)

        pad_x = self.s(_TILE_PAD_X)
        x = rect.x() + pad_x
        w = rect.width() - 2 * pad_x
        y = rect.y() + self.s(_TILE_PAD_TOP)

        # Label + status dot.
        p.setFont(self.display(22, 600, 0.14))
        p.setPen(QColor(self.muted))
        fm = p.fontMetrics()
        p.drawText(QPointF(x, y + fm.ascent()), reading.label.upper())
        dot = self.s(16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_STATUS_COLOR.get(reading.status, brand.STATUS_IDLE)))
        p.drawEllipse(QPointF(rect.right() - pad_x - dot / 2,
                              y + fm.ascent() / 2), dot / 2, dot / 2)
        y += fm.height() + self.s(16)

        # Figure + unit, on one baseline.
        f_val = self.display(92, 700, -0.02)
        p.setFont(f_val)
        vfm = p.fontMetrics()
        baseline = y + vfm.ascent()
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(x, baseline), reading.value)
        if reading.unit:
            p.setFont(self.mono(32, 500))
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(x + vfm.horizontalAdvance(reading.value)
                               + self.s(12), baseline), reading.unit)
        y = baseline + vfm.descent() + self.s(8)

        if reading.detail:
            f_cap = self.body(21)
            p.setFont(f_cap)
            cap_fm = p.fontMetrics()
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(x, y + cap_fm.ascent()),
                       cap_fm.elidedText(reading.detail,
                                         Qt.TextElideMode.ElideRight, int(w)))
            y += cap_fm.height()

        # The trace, bled to the tile's edges: the figure's shape across the
        # match, which is what tells "dipped once" from "sat low all match".
        trace_top = y + self.s(12)
        if reading.shape and rect.bottom() - trace_top > self.s(20):
            self._paint_trace(
                p, QRectF(rect.x(), trace_top, rect.width(),
                          rect.bottom() - trace_top),
                reading.shape,
                brand.RED if faulted else (brand.N600 if self.dark
                                           else brand.N300),
                r)

    def _paint_trace(self, p: QPainter, rect: QRectF, values, colour: str,
                     radius: float):
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        n = len(values)
        path = QPainterPath()
        for i, v in enumerate(values):
            px = rect.x() + rect.width() * i / (n - 1)
            py = rect.bottom() - (v - lo) / span * rect.height()
            path.moveTo(px, py) if i == 0 else path.lineTo(px, py)

        # Clipped to the tile so the bleed stops at the rounded corner rather
        # than drawing over it.
        clip = QPainterPath()
        clip.addRoundedRect(rect.adjusted(0, -radius, 0, 0), radius, radius)
        p.save()
        p.setClipPath(clip)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(colour), max(1.0, self.s(2.5)),
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
        p.restore()

    # ── Subsystem strip ───────────────────────────────────────────────────

    # A subsystem column holds a name and two figures; narrower than this and
    # the name starts eliding, which is the one part that matters.
    _MIN_SUB_W = 260

    def _subsystems(self):
        cap = {"large": 5, "medium": 4, "small": 3}[_band(self.height())]
        # Bounded by width as well as by band: an ultrawide panel has room for
        # five and a 4:3 cart monitor does not, and the band only knows height.
        width = self.content_rect().width()
        fits = int(width // self.s(self._MIN_SUB_W)) or 1
        return self._data.subsystems[:max(1, min(cap, fits))]

    def _paint_strip(self, p: QPainter, rect: QRectF, subs):
        p.setPen(QPen(QColor(self.rule), self.m("rule")))
        p.drawLine(QPointF(rect.x(), rect.y()),
                   QPointF(rect.right(), rect.y()))

        top = rect.y() + self.s(_STRIP_PAD)
        gap = self.s(24)
        w = (rect.width() - gap * (len(subs) - 1)) / len(subs)
        for i, sub in enumerate(subs):
            self._paint_sub(p, QRectF(rect.x() + i * (w + gap), top, w,
                                      rect.bottom() - top), sub)

    def _paint_sub(self, p: QPainter, rect: QRectF, sub):
        faulted = sub.status in (dg.FAULT, dg.WARN)
        p.setPen(QPen(QColor(brand.DIVIDER_DARK if self.dark else brand.N200),
                      self.m("rule")))
        p.drawLine(QPointF(rect.x(), rect.y()),
                   QPointF(rect.x(), rect.bottom()))

        x = rect.x() + self.s(22)
        w = rect.width() - self.s(22)
        dot = self.s(16)
        y = rect.y()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_STATUS_COLOR.get(sub.status, brand.STATUS_IDLE)))
        p.drawEllipse(QPointF(x + dot / 2, y + dot / 2), dot / 2, dot / 2)

        f = self.display(24, 600, 0.10)
        p.setFont(f)
        fm = p.fontMetrics()
        p.setPen(QColor(self.ink if faulted else
                        (brand.N300 if self.dark else brand.N600)))
        p.drawText(QPointF(x + dot + self.s(12), y + fm.ascent() * 0.92),
                   fm.elidedText(sub.name.upper(), Qt.TextElideMode.ElideRight,
                                 int(w - dot - self.s(12))))

        y += fm.height() + self.s(14)
        f_val = self.display(40, 700)
        f_unit = self.mono(20, 500)
        p.setFont(f_val)
        vfm = p.fontMetrics()
        baseline = y + vfm.ascent()
        cx = x
        for value, unit in ((sub.stator_a, "A"), (sub.temp_f, "°C")):
            if value is None:
                continue
            text = f"{value:,.0f}"
            p.setFont(f_val)
            p.setPen(QColor(self.ink))
            p.drawText(QPointF(cx, baseline), text)
            cx += vfm.horizontalAdvance(text) + self.s(6)
            p.setFont(f_unit)
            p.setPen(QColor(self.faint))
            p.drawText(QPointF(cx, baseline), unit)
            cx += p.fontMetrics().horizontalAdvance(unit) + self.s(20)
