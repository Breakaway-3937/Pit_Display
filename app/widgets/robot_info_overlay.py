"""
Robot Info — Screen B's companion to the diagnostics board on Screen A.

## Why two screens and not one big one

A pit has two overhead panels and a crew that reads them from different places.
Splitting the same data across both is how you end up with two half-boards
nobody can use, so the split is by **question**, not by column count:

| | asks | answers with |
|---|---|---|
| **A — Diagnostics** | *is anything wrong right now?* | vitals, subsystem status |
| **B — Robot Info** | *what is wrong, on which motor, and what was this log?* | the fault list by name, the per-motor table, provenance |

A is glanceable from ten feet. B is the thing you walk up to when A has gone
amber, and it is dense on purpose — the fault names and CAN ids are what you
take to the robot.

## Same chassis, different question

It runs the plate, the header band and the footer ledger from
`app/widgets/chassis.py`, exactly as A and the slide rotation do. The design
canvas comps A; carrying its chassis here is the point of having one — two
overhead panels in the same glance showing two different visual languages is
the failure the shared plate exists to prevent.

**Red still means a latched fault and nothing else.** The fault list spends it
on the rows that latched; the motor table spends it on their dots. A clean
robot puts no red on this surface either.

**The motor table shows the operator's names, not CAN ids** — `MotorRow.label`
falls back to `TalonFX 11` only where nobody has typed one. Naming motors in
Control → Pit Systems → Robot Logs is what turns this screen from a list of
addresses into a list of mechanisms.

Both boards read `app.robot.diagnostics`; neither computes anything.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from app import brand
from app.config import config
from app.robot import diagnostics as dg
from app.widgets.chassis import Chassis
from app.widgets.diagnostics_overlay import _STATUS_COLOR, _band


# Hard caps, not a measured fit. An earlier version measured where the table had
# landed and hid whatever fell past the fold; it read geometry that had not
# settled, so it hid rows there was room for — silently. Both lists are sorted
# worst-first upstream, so what a cap drops is always the healthy end.
def _budget(height: int) -> tuple[int, int, bool]:
    """(fault rows, motor rows, show the CAN id) for a panel this tall."""
    return {
        "large":  (6, 9, True),
        "medium": (5, 6, True),
        "small":  (4, 4, False),
    }[_band(height)]


class RobotInfoOverlay(Chassis):
    """Screen B's board, on the shared chassis."""

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._data = dg.Dashboard()
        self.reload()
        config.logs_changed.connect(self.reload)
        config.team_changed.connect(lambda _t: self.update())

    def reload(self):
        try:
            self._data = dg.dashboard()
        except Exception:
            self._data = dg.Dashboard()
        self.update()

    # ── Chassis hooks ─────────────────────────────────────────────────────

    def header_right(self) -> list[tuple]:
        live = not self._data.empty
        return [("dot", brand.STATUS_ONLINE if live else brand.STATUS_IDLE, live),
                ("mono", "SCREEN B  /  ROBOT INFO")]

    def footer_items(self) -> tuple[str, str]:
        d = self._data
        if d.empty:
            return "NO LOG IMPORTED", "CONTROL → PIT SYSTEMS → ROBOT LOGS"
        left = f"{len(d.motors)} DEVICES  ·  {d.duration_s / 60:.1f} MIN"
        if d.match_key:
            left = f"{d.match_key.upper()}  ·  {left}"
        # Two files usually make one match — name both rather than showing
        # whichever was imported last.
        return left, "  ·  ".join(list(d.sources) or [d.source_name])

    # ── The stage ─────────────────────────────────────────────────────────

    def paint_stage(self, p: QPainter, rect: QRectF):
        if self._data.empty:
            self._paint_empty(p, rect)
            return

        head = QRectF(rect.x(), rect.y(), rect.width(), self.s(150))
        self._paint_head(p, head)

        body = QRectF(rect.x(), head.bottom() + self.s(34), rect.width(),
                      rect.bottom() - head.bottom() - self.s(34))
        gap = self.s(48)
        left_w = (body.width() - gap) * 0.40
        self._paint_faults(p, QRectF(body.x(), body.y(), left_w, body.height()))
        self._paint_motors(p, QRectF(body.x() + left_w + gap, body.y(),
                                     body.width() - left_w - gap, body.height()))

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()),
                   "NO ROBOT LOG YET")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Nothing to report", self.display(108, 700, -0.02),
                              self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Import a log from Control → Pit Systems → Robot Logs. Name the CAN "
            "ids there too, and this table lists mechanisms instead of addresses.",
            self.body(32), self.body_ink, 1.4)

    # ── Head ──────────────────────────────────────────────────────────────

    def _paint_head(self, p: QPainter, rect: QRectF):
        d = self._data
        latched = [f for f in d.faults if f.status == dg.FAULT]
        warns = [f for f in d.faults if f.status == dg.WARN]

        if latched:
            eyebrow, headline = "What tripped", latched[0].label
            colour = brand.STATUS_FAULT
        elif warns:
            eyebrow, headline = "Worth a look", warns[0].label
            colour = brand.STATUS_PENDING
        else:
            eyebrow, headline = "What tripped", "Nothing latched"
            colour = brand.STATUS_ONLINE

        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), rect.y() + p.fontMetrics().ascent()),
                   eyebrow.upper())

        y = rect.y() + self.s(38)
        f = self.display(84, 700, -0.02)
        p.setFont(f)
        p.setPen(QColor(self.ink))
        fm = p.fontMetrics()
        p.drawText(QPointF(rect.x(), y + fm.ascent()),
                   fm.elidedText(headline, Qt.TextElideMode.ElideRight,
                                 int(rect.width() * 0.62)))

        # The count block sits right-flush: the number of things to fix.
        count = len(latched) or len(warns)
        if count:
            side = self.s(84)
            block = QRectF(rect.right() - side, y, side, side)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colour))
            p.drawRoundedRect(block, self.s(14), self.s(14))
            p.setFont(self.display(46, 700))
            p.setPen(QColor(brand.WHITE))
            p.drawText(block, int(Qt.AlignmentFlag.AlignCenter), str(count))

    # ── The fault list ────────────────────────────────────────────────────

    def _paint_faults(self, p: QPainter, rect: QRectF):
        max_rows, _, _ = _budget(self.height())
        y = self._section(p, rect, "Latched faults",
                          f"{len(self._data.faults)}")
        # The band is the cap; the space actually left under the heading is the
        # count. Without this second bound a 720p panel drew its fifth row
        # straight through the footer rule — the cap alone cannot know how much
        # room the head band took.
        rows = self._data.faults[:self._fit_rows(
            rect.bottom() - y, max_rows, self.s(58), self.s(14))]
        if not rows:
            p.setFont(self.body(28))
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent() + self.s(18)),
                       "Clean log — nothing to carry to the robot.")
            return

        gap = self.s(14)
        h = min(self.s(104), (rect.bottom() - y - gap * (len(rows) - 1))
                / len(rows))
        for i, reading in enumerate(rows):
            self._paint_fault_row(
                p, QRectF(rect.x(), y + i * (h + gap), rect.width(), h), reading)

    def _paint_fault_row(self, p: QPainter, rect: QRectF, reading):
        colour = _STATUS_COLOR.get(reading.status, brand.STATUS_IDLE)
        r = self.s(14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.tile))
        p.drawRoundedRect(rect, r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(brand.FAULT_EDGE if reading.status == dg.FAULT
                             else self.rule), max(1.0, self.s(1.5))))
        p.drawRoundedRect(rect, r, r)

        pad = self.s(24)
        x = rect.x() + pad
        dot = self.s(16)
        # Two lines when the row is tall enough for both, one centred line when
        # it is not — the cap decides how many rows fit, not how they read.
        two_line = rect.height() >= self.s(62) and bool(reading.detail)
        cy = rect.y() + rect.height() * (0.34 if two_line else 0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(colour))
        p.drawEllipse(QPointF(x + dot / 2, cy), dot / 2, dot / 2)

        name_x = x + dot + self.s(14)
        count = f"{reading.value} {reading.unit}".strip()
        p.setFont(self.mono(22, 500, 0.10))
        cfm = p.fontMetrics()
        count_w = cfm.horizontalAdvance(count)
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.right() - pad - count_w,
                           cy + cfm.ascent() / 2 - cfm.descent() / 2), count)

        p.setFont(self.display(34, 700))
        fm = p.fontMetrics()
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(name_x, cy + fm.ascent() / 2 - fm.descent() / 2),
                   fm.elidedText(reading.label, Qt.TextElideMode.ElideRight,
                                 int(rect.right() - pad - count_w - name_x
                                     - self.s(16))))

        if two_line:
            p.setFont(self.body(21))
            dfm = p.fontMetrics()
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(name_x, rect.y() + rect.height() * 0.88),
                       dfm.elidedText(reading.detail,
                                      Qt.TextElideMode.ElideRight,
                                      int(rect.right() - pad - name_x)))

    # ── The motor table ───────────────────────────────────────────────────

    def _paint_motors(self, p: QPainter, rect: QRectF):
        _, max_rows, show_id = _budget(self.height())
        y = self._section(p, rect, "Motors",
                          f"{len(self._data.motors)} ON THE BUS")
        header_h = self.s(46)
        rows = self._data.motors[:self._fit_rows(
            rect.bottom() - y - header_h, max_rows, self.s(34))]
        if not rows:
            return

        # Columns from the right: peak amps, min volts, temperature. The name
        # takes whatever is left, because the name is the part that matters.
        num_w = self.s(150)
        cols = [("TEMP", num_w), ("PEAK A", num_w), ("MIN V", num_w)]
        self._paint_motor_row(p, QRectF(rect.x(), y, rect.width(), header_h),
                              None, cols, show_id, header=True)
        y += header_h
        h = min(self.s(72), max(self.s(34),
                                (rect.bottom() - y) / max(1, len(rows))))
        for i, motor in enumerate(rows):
            self._paint_motor_row(
                p, QRectF(rect.x(), y + i * h, rect.width(), h),
                motor, cols, show_id, index=i)

    def _paint_motor_row(self, p: QPainter, rect: QRectF, motor, cols,
                         show_id: bool, header: bool = False, index: int = 0):
        if not header and index % 2:
            # Zebra: the table is read across, and a row stripe is the cheapest
            # thing that keeps an eye on one line at fifteen feet.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(brand.TILE_DARK if self.dark else brand.N100))
            p.drawRoundedRect(rect, self.s(8), self.s(8))

        pad = self.s(16)
        x = rect.x() + pad
        cy = rect.center().y()

        if header:
            p.setFont(self.display(20, 600, 0.14))
            fm = p.fontMetrics()
            p.setPen(QColor(self.faint))
            p.drawText(QPointF(x, cy + fm.ascent() / 2 - fm.descent() / 2),
                       "MOTOR")
            cx = rect.right() - pad
            for name, w in reversed(cols):
                p.drawText(QRectF(cx - w, rect.y(), w, rect.height()),
                           int(Qt.AlignmentFlag.AlignRight |
                               Qt.AlignmentFlag.AlignVCenter), name)
                cx -= w
            p.setPen(QPen(QColor(self.rule), self.m("rule")))
            p.drawLine(QPointF(rect.x(), rect.bottom()),
                       QPointF(rect.right(), rect.bottom()))
            return

        dot = self.s(14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_STATUS_COLOR.get(motor.status, brand.STATUS_IDLE)))
        p.drawEllipse(QPointF(x + dot / 2, cy), dot / 2, dot / 2)
        x += dot + self.s(14)

        # The CAN id in mono, then the operator's name. The id is what you look
        # for on the robot; the name is what you look for on the board.
        if show_id:
            p.setFont(self.mono(22, 500))
            ifm = p.fontMetrics()
            id_text = f"{motor.can_id:>2}"
            p.setPen(QColor(self.faint))
            p.drawText(QPointF(x, cy + ifm.ascent() / 2 - ifm.descent() / 2),
                       id_text)
            x += ifm.horizontalAdvance("00") + self.s(16)

        name_w = rect.right() - pad - sum(w for _, w in cols) - x - self.s(10)
        p.setFont(self.body(28, 500))
        nfm = p.fontMetrics()
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(x, cy + nfm.ascent() / 2 - nfm.descent() / 2),
                   nfm.elidedText(motor.label, Qt.TextElideMode.ElideRight,
                                  int(max(40.0, name_w))))

        values = (
            "—" if motor.temp_c is None else f"{motor.temp_c:,.0f}°C",
            "—" if motor.stator_a is None else f"{abs(motor.stator_a):,.0f}",
            "—" if motor.supply_v is None else f"{motor.supply_v:,.2f}",
        )
        p.setFont(self.mono(26, 500))
        p.setPen(QColor(self.body_ink))
        cx = rect.right() - pad
        for (_, w), value in zip(reversed(cols), reversed(values)):
            p.drawText(QRectF(cx - w, rect.y(), w, rect.height()),
                       int(Qt.AlignmentFlag.AlignRight |
                           Qt.AlignmentFlag.AlignVCenter), value)
            cx -= w

    # ── Shared ────────────────────────────────────────────────────────────

    @staticmethod
    def _fit_rows(available: float, cap: int, min_h: float,
                  gap: float = 0.0) -> int:
        """How many rows of at least `min_h` fit in `available`, capped."""
        if available <= 0 or min_h <= 0:
            return 0
        fits = int((available + gap) // (min_h + gap))
        return max(0, min(cap, fits))

    def _section(self, p: QPainter, rect: QRectF, title: str,
                 note: str = "") -> float:
        """Eyebrow + rule heading a column. Returns the y its content starts at."""
        p.setFont(self.display(22, 600, 0.16))
        fm = p.fontMetrics()
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), rect.y() + fm.ascent()), title.upper())
        if note:
            p.setFont(self.mono(20, 500, 0.10))
            nfm = p.fontMetrics()
            p.setPen(QColor(self.faint))
            p.drawText(QPointF(rect.right() - nfm.horizontalAdvance(note),
                               rect.y() + fm.ascent()), note)
        y = rect.y() + fm.height() + self.s(14)
        p.setPen(QPen(QColor(self.rule), self.m("rule")))
        p.drawLine(QPointF(rect.x(), y), QPointF(rect.right(), y))
        return y + self.s(20)
