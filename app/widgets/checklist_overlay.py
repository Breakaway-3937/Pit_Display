"""
Checklist overlay — the pit checklist as seen on an overhead screen.

Read from across the pit, not from arm's length: everything is sized from the
live widget height rather than a fixed point size, so the same widget is legible
on a 24" monitor on a cart and on a 55" panel over the workbench.

## Colour

The team accent carries the header and the progress bar. **Done items are
`STATUS_ONLINE`, not the accent** — a ticked row has to read as a state from
ten feet away, and on Breakaway the accent *is* red, which at a glance says
"fault", not "finished". That also keeps the brand's one-focal-red budget
intact: the red is the header, the green is status.

## Where the content comes from

Nothing here authors items. The list is whatever the team has typed into
Control → (this screen) → Checklist; with no items the overlay says so and
points at that panel rather than showing an empty box to a pit full of
visitors.
"""

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from app import brand
from app.checklist import checklist
from app.config import SCREEN_LABELS, config


def _clamp(v: float, lo: float, hi: float) -> int:
    return int(max(lo, min(hi, v)))


def _font(family: str, px: int, weight: QFont.Weight) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(8, px))
    f.setWeight(weight)
    return f


# ── Tick box ──────────────────────────────────────────────────────────────────

class _TickBox(QWidget):
    """
    The square at the head of a row: empty outline, or filled with a check.

    Painted rather than assembled from a QCheckBox because it has to scale with
    the screen — and because it carries a `sizeHint`, without which a custom
    painted widget is laid out at zero width (see CLAUDE.md, Qt layout traps).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._side = 28
        self._done = False
        self._line = brand.CARBON_LINE
        self._fill = brand.STATUS_ONLINE
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(self._side, self._side)

    def minimumSizeHint(self):
        return self.sizeHint()

    def set_side(self, px: int):
        self._side = max(12, px)
        self.updateGeometry()
        self.update()

    def set_done(self, done: bool):
        self._done = done
        self.update()

    def set_colors(self, line: str, fill: str):
        self._line, self._fill = line, fill
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height())
        r = QRectF(0.5, 0.5, s - 1, s - 1)
        radius = s * 0.22          # Rounded, one radius per element

        if self._done:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self._fill))
            p.drawRoundedRect(r, radius, radius)
            pen = QPen(QColor(brand.WHITE))
            pen.setWidthF(max(2.0, s * 0.13))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(*[QPointF(x, y) for x, y in (
                (s * 0.26, s * 0.52), (s * 0.44, s * 0.70), (s * 0.76, s * 0.32),
            )])
        else:
            pen = QPen(QColor(self._line))
            pen.setWidthF(max(1.5, s * 0.08))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
        p.end()


# ── Progress bar ──────────────────────────────────────────────────────────────

class _ProgressBar(QWidget):
    """Thin accent bar under the header. Zero items reads as an empty track."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frac = 0.0
        self._track = brand.CARBON_LINE
        self._fill = brand.RED
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_fraction(self, frac: float):
        self._frac = max(0.0, min(1.0, frac))
        self.update()

    def set_colors(self, track: str, fill: str):
        self._track, self._fill = track, fill
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._track))
        p.drawRoundedRect(QRectF(0, 0, self.width(), h), h / 2, h / 2)
        if self._frac > 0:
            p.setBrush(QColor(self._fill))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self._frac, h),
                              h / 2, h / 2)
        p.end()


# ── One row ───────────────────────────────────────────────────────────────────

class _ItemRow(QWidget):

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item_id = item.id
        self._done = item.done

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)

        self._tick = _TickBox()
        # Centre with a stretch column, not an alignment flag: an alignment
        # argument lays the widget out at its sizeHint and drops the row's
        # vertical sizing on the floor (CLAUDE.md, Qt layout traps).
        tick_col = QVBoxLayout()
        tick_col.setContentsMargins(0, 0, 0, 0)
        tick_col.addStretch()
        tick_col.addWidget(self._tick)
        tick_col.addStretch()
        row.addLayout(tick_col)

        self._label = QLabel(item.text)
        self._label.setWordWrap(True)
        row.addWidget(self._label, stretch=1)

    def set_done(self, done: bool):
        self._done = done
        self._tick.set_done(done)
        f = self._label.font()
        f.setStrikeOut(done)
        self._label.setFont(f)
        self._restyle()

    def apply_scale(self, row_h: int, tick_px: int, text_px: int):
        # The row height is set, not left to the label's own hint: the rows have
        # to divide up the screen between them, otherwise six items huddle at
        # the top of a 55" panel with half of it empty.
        self.setFixedHeight(row_h)
        self._tick.set_side(tick_px)
        f = _font(brand.FONT_BODY, text_px, QFont.Weight.Medium)
        f.setStrikeOut(self._done)
        self._label.setFont(f)

    def apply_colors(self, ink: str, muted: str, line: str, fill: str):
        self._ink, self._muted = ink, muted
        self._tick.set_colors(line, fill)
        self._restyle()

    def _restyle(self):
        color = getattr(self, "_muted" if self._done else "_ink", brand.INK_DARK)
        self._label.setStyleSheet(f"color: {color}; background: transparent;")


# ── Overlay ───────────────────────────────────────────────────────────────────

class ChecklistOverlay(QWidget):

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(parent)
        self._screen_id = screen_id
        self._list_id: int | None = None
        self._rows: list[_ItemRow] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._build_ui()

        checklist.items_changed.connect(self._on_items_changed)
        checklist.item_toggled.connect(self._on_item_toggled)
        checklist.lists_changed.connect(self._on_lists_changed)
        config.team_changed.connect(lambda *_: self._refresh_style())
        config.screen_setting_changed.connect(self._on_setting_changed)

        self._refresh_style()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(48, 40, 48, 40)
        root.setSpacing(0)
        self._root = root

        self._eyebrow = QLabel("")
        root.addWidget(self._eyebrow)
        root.addSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(24)
        self._title = QLabel("")
        head.addWidget(self._title, stretch=1)
        self._progress_lbl = QLabel("")
        self._progress_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        head.addWidget(self._progress_lbl)
        root.addLayout(head)

        root.addSpacing(14)
        self._bar = _ProgressBar()
        root.addWidget(self._bar)
        root.addSpacing(26)

        self._items_box = QVBoxLayout()
        self._items_box.setContentsMargins(0, 0, 0, 0)
        self._items_box.setSpacing(10)
        root.addLayout(self._items_box)

        self._empty = QLabel("")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty, stretch=1)

        root.addStretch()

    # ── Which list ────────────────────────────────────────────────────────

    def set_list(self, list_id: int | None):
        if list_id == self._list_id:
            return
        self._list_id = list_id
        self._rebuild()

    def _rebuild(self):
        for row in self._rows:
            self._items_box.removeWidget(row)
            row.deleteLater()
        self._rows = []

        items = checklist.items(self._list_id) if self._list_id else []
        for item in items:
            row = _ItemRow(item)
            self._items_box.addWidget(row)
            self._rows.append(row)

        has_items = bool(items)
        self._empty.setVisible(not has_items)
        self._refresh_style()
        self._apply_scale()

    # ── Signals ───────────────────────────────────────────────────────────

    def _on_items_changed(self, list_id: int):
        if list_id == self._list_id:
            self._rebuild()

    def _on_item_toggled(self, list_id: int, item_id: int, done: bool):
        """One row, one repaint — this fires constantly while the crew works."""
        if list_id != self._list_id:
            return
        for row in self._rows:
            if row.item_id == item_id:
                row.set_done(done)
                break
        self._refresh_progress()

    def _on_lists_changed(self):
        # The list this screen points at may have just been deleted. Fall back
        # to whatever list still exists rather than going blank in front of a
        # pit full of visitors — the screen heals itself without anyone having
        # to walk back to the control panel and re-point it.
        if self._list_id is None or checklist.get_list(self._list_id) is None:
            self._list_id = checklist.default_list_id()
        self._rebuild()

    def _on_setting_changed(self, screen: str, key: str, _value):
        if screen == self._screen_id and key == "theme":
            self._refresh_style()

    # ── Paint / scale ─────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scale()

    def _apply_scale(self):
        h = self.height()
        if h <= 0:
            return
        pad_x = _clamp(h * 0.055, 20, 90)
        pad_y = _clamp(h * 0.045, 16, 72)
        self._root.setContentsMargins(pad_x, pad_y, pad_x, pad_y)

        eyebrow_px = _clamp(h * 0.024, 11, 34)
        title_px   = _clamp(h * 0.078, 20, 96)
        prog_px    = _clamp(h * 0.052, 15, 64)

        self._eyebrow.setFont(_font(brand.FONT_DISPLAY, eyebrow_px,
                                    QFont.Weight.DemiBold))
        self._title.setFont(_font(brand.FONT_DISPLAY, title_px, QFont.Weight.Bold))
        self._progress_lbl.setFont(_font(brand.FONT_MONO, prog_px,
                                         QFont.Weight.Bold))
        self._bar.setFixedHeight(_clamp(h * 0.012, 4, 16))
        self._empty.setFont(_font(brand.FONT_BODY, _clamp(h * 0.032, 13, 34),
                                  QFont.Weight.Normal))

        n = len(self._rows)
        if not n:
            return
        gap = _clamp(h * 0.010, 4, 20)
        self._items_box.setSpacing(gap)
        # Everything above the rows, plus the fixed gaps the layout adds.
        used = pad_y * 2 + eyebrow_px * 1.5 + title_px * 1.25 + self._bar.height() + 46
        avail = max(0, h - used - gap * (n - 1))
        # A long list stops growing at the floor and simply runs past the bottom
        # of the screen — a checklist nobody can read is worse than a short one,
        # so the fix there is fewer items, not smaller type.
        row_h = _clamp(avail / n, 26, h * 0.155)
        text_px = _clamp(row_h * 0.46, 12, 58)
        tick_px = _clamp(row_h * 0.60, 14, 64)
        for row in self._rows:
            row.apply_scale(row_h, tick_px, text_px)

    # ── Theming ───────────────────────────────────────────────────────────

    def _refresh_progress(self):
        done, total = checklist.progress(self._list_id) if self._list_id else (0, 0)
        self._progress_lbl.setText(f"{done}/{total}" if total else "—")
        self._bar.set_fraction(done / total if total else 0.0)

    def _refresh_style(self):
        theme = config.screen_theme(self._screen_id) if self._screen_id else "dark"
        pal = brand.palette(theme)
        accent = config.active_team.primary_color

        cl = checklist.get_list(self._list_id) if self._list_id else None
        screen_label = SCREEN_LABELS.get(self._screen_id, self._screen_id)
        team = config.active_team
        team_label = (f"{team.name} {team.number}" if team.name
                      else f"Team {team.number}").upper()

        self._eyebrow.setText(f"{team_label}  ·  CHECKLIST")
        self._title.setText(cl.name if cl else "No checklist selected")
        self._empty.setText(
            "Nothing on this list yet.\n\n"
            f"Add items on the control screen:\n{screen_label} → Checklist."
        )
        self._refresh_progress()

        self.setStyleSheet(f"background-color: {pal['bg']};")
        self._eyebrow.setStyleSheet(
            f"color: {accent}; background: transparent; letter-spacing: 3px;")
        self._title.setStyleSheet(f"color: {pal['title']}; background: transparent;")
        self._progress_lbl.setStyleSheet(
            f"color: {pal['muted']}; background: transparent;")
        self._empty.setStyleSheet(f"color: {pal['muted']}; background: transparent;")
        self._bar.set_colors(pal["line"], accent)

        for row in self._rows:
            row.apply_colors(pal["ink"], pal["faint"], pal["line"],
                             brand.STATUS_ONLINE)
            row.set_done(row._done)
