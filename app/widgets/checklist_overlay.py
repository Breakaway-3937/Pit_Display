"""
The pit checklist as the overhead screen shows it.

Written and ticked from the control panel, never from here — the overhead
screens are audience-facing and out of reach, and nobody walks over to a
monitor above the workbench to tap an item. Every tick arrives through the
`checklist` singleton's signals like every other cross-window command.

**On the same chassis as everything else** (`app/widgets/chassis.py`): the
plate, the header band, the footer ledger. It used to be a bespoke layout with
a red eyebrow and a red progress bar — red type on carbon is 2.8:1 and
forbidden, and a progress bar that is red the whole way says "fault" rather
than "getting there".

**Done is green (`STATUS_ONLINE`), not the accent.** On Breakaway the team
accent *is* red, and a red tick at ten feet reads "fault", not "finished".

**This surface spends no red at all**, which the budget allows: a checklist
working normally is the opposite of an exceptional state, and a red remaining
track was the largest field of colour on the panel saying so.

**Everything is sized from the live widget height** (`_row_metrics`), including
an explicit height per row: without that, six items huddle at the top of a 55"
panel with half the screen empty. A long list stops growing at a floor and the
overflow is dropped with a count, rather than running off the bottom where
nobody can see that it was cut.

**Content is the team's to write.** The app seeds one empty list; the empty
state points at the control panel rather than showing an empty box to a pit
full of visitors.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen

from app import brand
from app.checklist import checklist
from app.config import SCREEN_LABELS, config
from app.widgets.chassis import Chassis

# Design sizes at 1920×1080.
_TITLE = 92
_EYEBROW = 24
_ROW_MAX = 96
_ROW_MIN = 40
_ROW_GAP = 14
_TICK_RATIO = 0.52     # tick box as a fraction of the row height
_TEXT_RATIO = 0.42     # item type as a fraction of the row height


class ChecklistOverlay(Chassis):
    """The overhead checklist, on the shared chassis."""

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._list_id: int | None = None

        checklist.items_changed.connect(self._on_items_changed)
        checklist.item_toggled.connect(self._on_item_toggled)
        checklist.lists_changed.connect(self._on_lists_changed)
        config.team_changed.connect(lambda *_: self.update())

    # ── Which list ────────────────────────────────────────────────────────

    def set_list(self, list_id: int | None):
        if list_id == self._list_id:
            return
        self._list_id = list_id
        self.update()

    def _items(self):
        return checklist.items(self._list_id) if self._list_id else []

    def _on_items_changed(self, list_id: int):
        if list_id == self._list_id:
            self.update()

    def _on_item_toggled(self, list_id: int, _item_id: int, _done: bool):
        if list_id == self._list_id:
            self.update()

    def _on_lists_changed(self):
        # The list this screen points at may have just been deleted. Fall back
        # to whatever list still exists rather than going blank in front of a
        # pit full of visitors — the screen heals itself without anyone having
        # to walk back to the control panel and re-point it.
        if self._list_id is None or checklist.get_list(self._list_id) is None:
            self._list_id = checklist.default_list_id()
        self.update()

    # ── Chassis hooks ─────────────────────────────────────────────────────

    @property
    def _screen_letter(self) -> str:
        return "B" if self.screen_id.endswith("_b") else "A"

    def header_right(self) -> list[tuple]:
        done, total = self._progress()
        colour = (brand.STATUS_ONLINE if total and done == total
                  else brand.STATUS_PENDING if total else brand.STATUS_IDLE)
        return [("dot", colour, False),
                ("mono", f"SCREEN {self._screen_letter}  /  CHECKLIST")]

    def _progress(self) -> tuple[int, int]:
        return checklist.progress(self._list_id) if self._list_id else (0, 0)

    def paint_footer(self, p: QPainter, rect: QRectF):
        c = self.content_rect()
        rule_y = self.height() - self.m("stage_bottom")
        p.setPen(QPen(QColor(self.rule), self.m("rule")))
        p.drawLine(QPointF(c.x(), rule_y), QPointF(c.right(), rule_y))

        done, total = self._progress()
        left = f"{done} / {total} DONE" if total else "NOTHING ON THIS LIST"
        f = self.mono(20, 500, 0.12)
        p.setFont(f)
        p.setPen(QColor(self.faint))
        fm = p.fontMetrics()
        p.drawText(QRectF(c.x(), rect.y(), fm.horizontalAdvance(left),
                          rect.height()),
                   int(Qt.AlignmentFlag.AlignLeft |
                       Qt.AlignmentFlag.AlignVCenter), left)

        # The rail's track is the neutral rule and its fill is green, exactly
        # like the rotation's dwell rail. A red remaining-track was tried and
        # is wrong twice over: it is the largest field of colour on the panel,
        # and on a board where red means "fault" it says the checklist itself
        # is broken. **A working checklist carries no red at all** — a surface
        # is allowed zero.
        rail_h = self.s(6)
        rail_x = c.x() + fm.horizontalAdvance(left) + self.s(26)
        rail_w = c.right() - rail_x
        if rail_w <= 0 or not total:
            return
        rail = QRectF(rail_x, rect.center().y() - rail_h / 2, rail_w, rail_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.rule))
        p.drawRoundedRect(rail, rail_h / 2, rail_h / 2)
        filled = rail_w * done / total
        if filled > 0:
            p.setBrush(QColor(brand.STATUS_ONLINE))
            p.drawRoundedRect(QRectF(rail.x(), rail.y(),
                                     max(rail_h, filled), rail_h),
                              rail_h / 2, rail_h / 2)

    # ── The stage ─────────────────────────────────────────────────────────

    def paint_stage(self, p: QPainter, rect: QRectF):
        cl = checklist.get_list(self._list_id) if self._list_id else None
        items = self._items()

        y = rect.y()
        p.setFont(self.display(_EYEBROW, 600, 0.16))
        p.setPen(QColor(self.muted))
        # Not "PIT CHECKLIST": the list's own name is already the headline
        # under it, and an eyebrow that repeats the title says nothing.
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()),
                   "BEFORE THE NEXT MATCH")
        y += self.s(_EYEBROW) + self.s(14)

        y = self.draw_wrapped(p, rect.x(), y, rect.width(),
                              cl.name if cl else "No checklist selected",
                              self.display(_TITLE, 700, -0.018), self.ink, 1.0)
        y += self.s(34)

        if not items:
            self._paint_empty(p, QRectF(rect.x(), y, rect.width(),
                                        rect.bottom() - y))
            return
        self._paint_items(p, QRectF(rect.x(), y, rect.width(),
                                    rect.bottom() - y), items)

    def _paint_empty(self, p: QPainter, rect: QRectF):
        screen = SCREEN_LABELS.get(self.screen_id, self.screen_id)
        self.draw_wrapped(
            p, rect.x(), rect.y(), min(self.s(1200), rect.width()),
            "Nothing on this list yet. Add items on the control screen: "
            f"{screen} → Checklist.",
            self.body(34), self.body_ink, 1.5)

    def _row_metrics(self, available: float, count: int) -> tuple[float, int]:
        """(row height, rows that fit). Sized from the panel, capped at both ends."""
        gap = self.s(_ROW_GAP)
        row_h = self.s(_ROW_MAX)
        shown = count
        if count > 0:
            row_h = min(row_h, (available - gap * (count - 1)) / count)
        if row_h < self.s(_ROW_MIN):
            # A checklist nobody can read is worse than a short one, so the row
            # stops shrinking and the list is cut instead — with a count, so
            # the crew can see that it was cut.
            row_h = self.s(_ROW_MIN)
            shown = max(1, int((available + gap) // (row_h + gap)))
        return row_h, min(count, shown)

    def _paint_items(self, p: QPainter, rect: QRectF, items):
        gap = self.s(_ROW_GAP)
        row_h, shown = self._row_metrics(rect.height(), len(items))
        hidden = len(items) - shown
        if hidden > 0:
            # Reserve the last row for the "+n more" line.
            shown = max(1, shown - 1)
            hidden = len(items) - shown

        for i, item in enumerate(items[:shown]):
            self._paint_row(p, QRectF(rect.x(), rect.y() + i * (row_h + gap),
                                      rect.width(), row_h), item)

        if hidden > 0:
            y = rect.y() + shown * (row_h + gap)
            p.setFont(self.mono(24, 500, 0.12))
            p.setPen(QColor(self.faint))
            p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()),
                       f"+{hidden} MORE — SHORTEN THE LIST TO SEE THEM")

    def _paint_row(self, p: QPainter, rect: QRectF, item):
        done = item.done
        r = self.s(14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.tile))
        p.drawRoundedRect(rect, r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(brand.STATUS_ONLINE if done else self.rule),
                      max(1.0, self.s(1.5))))
        p.drawRoundedRect(rect, r, r)

        pad = rect.height() * 0.28
        side = rect.height() * _TICK_RATIO
        box = QRectF(rect.x() + pad, rect.center().y() - side / 2, side, side)

        p.setPen(Qt.PenStyle.NoPen)
        if done:
            p.setBrush(QColor(brand.STATUS_ONLINE))
            p.drawRoundedRect(box, side * 0.28, side * 0.28)
            # The tick, drawn rather than set as a glyph so it scales exactly
            # with the box on any panel.
            pen = QPen(QColor(brand.WHITE), max(1.5, side * 0.13),
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                       Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(
                QPointF(box.x() + side * 0.24, box.y() + side * 0.52),
                QPointF(box.x() + side * 0.43, box.y() + side * 0.71),
                QPointF(box.x() + side * 0.77, box.y() + side * 0.30))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(self.muted), max(1.0, self.s(2))))
            p.drawRoundedRect(box, side * 0.28, side * 0.28)

        text_x = box.right() + pad
        f = self.body(rect.height() * _TEXT_RATIO / max(0.28, self.s(1.0)),
                      500)
        p.setFont(f)
        fm = QFontMetricsF(f)
        p.setPen(QColor(self.muted if done else self.ink))
        p.drawText(QPointF(text_x,
                           rect.center().y() + fm.ascent() / 2 - fm.descent() / 2),
                   fm.elidedText(item.text, Qt.TextElideMode.ElideRight,
                                 int(rect.right() - pad - text_x)))
