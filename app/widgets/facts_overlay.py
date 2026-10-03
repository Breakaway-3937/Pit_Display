"""
"Did you know?": home's fun facts on an overhead screen.

The facts are `tba_fact` rows (home/REQUESTS.md R5), finished sentences home
writes from TBA's award history: Breakaway's on the left, the league's on the
right (Arkansas's, from home today), each in home's order (`app/tba_facts.py`). A per-screen content
setting (`content = "facts"`) and one stop in the standard rotation once any
have arrived.

* **Every word is home's.** Nothing here composes or rounds a fact.
* **A column pages, never shrinks.** It shows the facts that fit at a readable
  size (36 px body at 1920×1080, over the brand's 24 px slide minimum) and turns
  to the next ones every `PAGE_S` seconds, only while the face is on screen.
* **Zero red.** No fact is a fault or the one focus of the screen; rules are
  the plate's own.
* **The credit is in the ledger**, as the Next Match board's is: "Powered by
  The Blue Alliance" (`app/attribution.py`), a condition of the data's use.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen

from app import brand, tba_facts
from app.widgets.chassis import Chassis

PAGE_S = 12
_FACT_PX = 36
_COL_GAP = 96
_ROW_GAP = 34


class FactsOverlay(Chassis):

    def __init__(self, screen_id: str = "", parent=None):
        self._facts: list[tba_facts.Fact] = []
        # Per column: where this page starts and how many fit last paint.
        self._start = {"3937": 0, "league": 0}
        self._shown = {"3937": 0, "league": 0}
        super().__init__(screen_id=screen_id, parent=parent)
        self._pager = QTimer(self)
        self._pager.setInterval(PAGE_S * 1000)
        self._pager.timeout.connect(self._next_page)
        self.reload()
        try:
            from app.db.sync.service import sync
            # A bound method: this face dies with its screen; the service doesn't.
            sync.facts_changed.connect(self.reload)
        except RuntimeError:
            pass                        # no sync service (a test): reload by hand

    def reload(self) -> None:
        self._facts = tba_facts.facts()
        self._start = {"3937": 0, "league": 0}
        self.update()

    def _column(self, which: str) -> list[tba_facts.Fact]:
        return [f for f in self._facts if (f.category == "3937") == (which == "3937")]

    def _next_page(self) -> None:
        for which in self._start:
            n = len(self._column(which))
            if n and self._shown[which] < n:
                self._start[which] = (self._start[which] + self._shown[which]) % n
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._pager.start()

    def hideEvent(self, event):
        self._pager.stop()
        super().hideEvent(event)

    # ── Chassis hooks ─────────────────────────────────────────────────────

    def header_right(self) -> list[tuple]:
        side = "A" if self.screen_id.endswith("_a") else "B"
        live = bool(self._facts)
        return [("dot", brand.STATUS_ONLINE if live else brand.STATUS_IDLE, live),
                ("mono", f"SCREEN {side}  /  DID YOU KNOW")]

    def footer_items(self) -> tuple[str, str]:
        return "DID YOU KNOW", tba_facts.CREDIT.upper()

    def paint_stage(self, p: QPainter, rect: QRectF):
        if not self._facts:
            self._paint_empty(p, rect)
            return
        ours, league = self._column("3937"), self._column("league")
        # A matched set (app/overhead.py): Screen A shows Breakaway's facts,
        # Screen B the others (Arkansas's), each full width.
        if self.screen_id.endswith("_a"):
            league = []
        elif self.screen_id.endswith("_b"):
            ours = []
        if not (ours or league):
            self._paint_empty(p, rect)
            return
        if ours and league:
            w = (rect.width() - self.s(_COL_GAP)) / 2
            self._paint_column(p, QRectF(rect.x(), rect.y(), w, rect.height()),
                               "Breakaway", "3937", ours)
            x = rect.x() + w + self.s(_COL_GAP)
            p.setPen(QPen(QColor(self.rule), self.m("rule")))
            gx = rect.x() + w + self.s(_COL_GAP) / 2
            p.drawLine(QPointF(gx, rect.y()), QPointF(gx, rect.bottom()))
            self._paint_column(p, QRectF(x, rect.y(), w, rect.height()),
                               tba_facts.title(league[0].category), "league", league)
        else:
            which = "3937" if ours else "league"
            self._paint_column(p, rect, tba_facts.title((ours or league)[0].category),
                               which, ours or league)

    def _paint_column(self, p: QPainter, rect: QRectF, title: str, which: str,
                      facts: list[tba_facts.Fact]) -> None:
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), rect.y() + p.fontMetrics().ascent()), title.upper())
        y = rect.y() + self.s(24 + 30)
        font = self.body(_FACT_PX)
        start = self._start[which] % len(facts)
        order = facts[start:] + facts[:start]
        shown = 0
        for fact in order:
            bottom = self.draw_wrapped(p, rect.x(), y, rect.width(), fact.text, font,
                                       self.body_ink, 1.3, measure_only=True)
            if bottom > rect.bottom() and shown:
                break                     # the rest wait for the next page
            p.setPen(QPen(QColor(self.rule), self.m("rule")))
            p.drawLine(QPointF(rect.x(), y), QPointF(rect.right(), y))
            y = self.draw_wrapped(p, rect.x(), y + self.s(_ROW_GAP / 2), rect.width(),
                                  fact.text, font, self.ink, 1.3) + self.s(_ROW_GAP)
            shown += 1
        self._shown[which] = shown

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()), "DID YOU KNOW")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Facts are on their way", self.display(108, 700, -0.02),
                              self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Breakaway's award history and the league's records arrive from home "
            "with team sync, and appear here.", self.body(32), self.body_ink, 1.4)
