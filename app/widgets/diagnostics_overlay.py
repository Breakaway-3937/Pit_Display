"""
Robot Diagnostics — the pit's between-matches board, on an overhead screen.

Read from across the pit like the checklist, so everything is sized from the
live widget height rather than a fixed point size. Same reason, same method.

## What it is for

The slide rotation talks to visitors. **This talks to the crew.** Every tile is
something you would act on in the six minutes before the next match: how far the
battery sagged, what latched a fault, which mechanism is running hot. If a
number here does not change what anyone does, it does not belong on the board.

## Colour

`brand.STATUS_*` — green / amber / red / grey, the status-dot palette the brand
defines in §7. **Red on this board means a latched fault and nothing else.**
That is the one place the red budget is spent: when the robot is clean the board
has no red on it at all, so red appearing anywhere is the thing to look at. The
team accent carries the eyebrow, exactly as it does on every other overlay here.

## Where the numbers come from

`app.robot.diagnostics`, which reads only the rolled-up columns — never a raw
sample — and takes its OK/warn/fault from the robot's own latched faults rather
than thresholds invented in this app. The board is a view; the judgment lives
there, next to the data it is judging.

Nothing here computes anything. If a figure looks wrong, `diagnostics.py` is
where it is wrong.
"""

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from app import brand
from app.config import SCREEN_LABELS, config
from app.robot import diagnostics as dg
from app.widgets.brand_widgets import Trace

_STATUS_COLOR = {
    dg.OK:    brand.STATUS_ONLINE,
    dg.WARN:  brand.STATUS_PENDING,
    dg.FAULT: brand.STATUS_FAULT,
    dg.IDLE:  brand.STATUS_IDLE,
}

# A tile narrower than this cannot hold "12.39 V" plus its caption at a size
# anyone can read from the far side of a pit, so the grid drops a column
# instead of shrinking below it.
_MIN_TILE_W = 250

# Even on a 55" panel. Past four across, the board stops being a set of numbers
# you take in at a glance and becomes a spreadsheet you have to read.
_MAX_COLS = 4

# A subsystem column holds a name, an amp figure and a temperature. Narrower
# than this and the name starts eliding, which is the one part that matters.
_MIN_SUB_COL_W = 300


def clamp(v: float, lo: float, hi: float) -> int:
    return int(max(lo, min(hi, v)))


def scaled_font(family: str, px: int, weight: QFont.Weight,
                mono: bool = False) -> QFont:
    # The mono role goes through the whole family stack — naming a font Qt
    # cannot resolve costs ~40 ms on every QFont construction (see CLAUDE.md).
    f = QFont()
    f.setFamilies(brand.FONT_MONO_STACK if mono else [family])
    f.setPixelSize(max(8, px))
    f.setWeight(weight)
    if mono:
        f.setStyleHint(QFont.StyleHint.Monospace)
    return f


class StatusDot(QWidget):
    """
    A filled circle. Painted, not a styled QLabel, so it scales with the board.

    Carries a `sizeHint` — a custom painted widget without one is laid out at
    zero width and never appears (CLAUDE.md, Qt layout traps).
    """

    def __init__(self, status: str = dg.IDLE, parent=None):
        super().__init__(parent)
        self._status = status
        self._side = 14
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(self._side, self._side)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_side(self, px: int):
        self._side = max(6, px)
        self.updateGeometry()
        self.update()

    def set_status(self, status: str):
        self._status = status
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_STATUS_COLOR.get(self._status, brand.STATUS_IDLE)))
        p.drawEllipse(QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s))
        p.end()


class StatTile(QFrame):
    """One measurement: caption, number, unit, and one line of context."""

    def __init__(self, reading: dg.Reading, parent=None):
        super().__init__(parent)
        self.setObjectName("stat_tile")
        self._reading = reading

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 13, 16, 13)
        box.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._caption = QLabel(reading.label.upper())
        head.addWidget(self._caption, stretch=1)
        self._dot = StatusDot(reading.status)
        head.addWidget(self._dot)
        box.addLayout(head)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._value = QLabel(reading.value)
        row.addWidget(self._value)
        self._unit = QLabel(reading.unit)
        # Bottom-aligned so a unit sits on the number's baseline rather than
        # floating beside its cap height.
        self._unit.setAlignment(Qt.AlignmentFlag.AlignLeft
                                | Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self._unit)
        row.addStretch()
        box.addLayout(row)

        self._detail = QLabel(reading.detail)
        self._detail.setWordWrap(True)
        box.addWidget(self._detail)
        # Spare height in a tile with no caption goes to the bottom. Without
        # this the value row absorbs it and the bottom-aligned unit floats away
        # from the number it belongs to.
        box.addStretch()

    def apply_scale(self, cap_px: int, val_px: int, det_px: int, pad: int):
        self.layout().setContentsMargins(pad, int(pad * 0.8), pad, int(pad * 0.8))
        self._caption.setFont(scaled_font(brand.FONT_DISPLAY, cap_px,
                                          QFont.Weight.DemiBold))
        self._value.setFont(scaled_font("", val_px, QFont.Weight.Bold, mono=True))
        self._unit.setFont(scaled_font(brand.FONT_DISPLAY, int(val_px * 0.42),
                                       QFont.Weight.DemiBold))
        self._detail.setFont(scaled_font(brand.FONT_BODY, det_px,
                                         QFont.Weight.Normal))
        self._detail.setVisible(bool(self._reading.detail) and det_px >= 10)

    def apply_colors(self, pal: dict):
        colour = _STATUS_COLOR.get(self._reading.status, brand.STATUS_IDLE)
        # The number itself only takes a status colour when something is wrong.
        # A board of green numbers is as hard to read as a board of red ones.
        ink = colour if self._reading.status in (dg.WARN, dg.FAULT) else pal["title"]
        self.setStyleSheet(
            f"#stat_tile {{ background: {pal['surface']};"
            f" border: 1px solid {pal['line']};"
            f" border-radius: {brand.R_CARD}px; }}")
        self._caption.setStyleSheet(
            f"color: {pal['muted']}; background: transparent; letter-spacing: 2px;")
        self._value.setStyleSheet(f"color: {ink}; background: transparent;")
        self._unit.setStyleSheet(f"color: {pal['muted']}; background: transparent;")
        self._detail.setStyleSheet(f"color: {pal['faint']}; background: transparent;")
        self._dot.set_status(self._reading.status)


class _SubsystemRow(QFrame):
    """One mechanism: name, peak current, temperature, status."""

    def __init__(self, sub: dg.Subsystem, parent=None):
        super().__init__(parent)
        self.setObjectName("sub_row")
        self._sub = sub

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(12)

        self._dot = StatusDot(sub.status)
        row.addWidget(self._dot)
        self._name = QLabel(sub.name)
        row.addWidget(self._name, stretch=1)

        # Stator first, then supply, then the PDH channel: not every mechanism
        # logs all three, and a row reading "— —" tells the crew nothing about a
        # subsystem the robot is plainly measuring.
        self._stator = QLabel(_amps(_first(sub.stator_a, sub.supply_a, sub.pdh_a)))
        self._stator.setAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._stator)
        self._temp = QLabel(_degrees(sub.temp_f))
        self._temp.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._temp)

    def apply_scale(self, h: int, text_px: int, dot_px: int):
        self.setFixedHeight(h)
        self._dot.set_side(dot_px)
        self._name.setFont(scaled_font(brand.FONT_BODY, text_px,
                                       QFont.Weight.Medium))
        num = scaled_font("", int(text_px * 0.92), QFont.Weight.DemiBold, mono=True)
        for w in (self._stator, self._temp):
            w.setFont(num)
            w.setFixedWidth(int(text_px * 5.2))

    def apply_colors(self, pal: dict):
        colour = _STATUS_COLOR.get(self._sub.status, brand.STATUS_IDLE)
        self.setStyleSheet(
            f"#sub_row {{ background: {pal['surface']};"
            f" border-radius: {brand.R_BTN}px; }}")
        self._name.setStyleSheet(f"color: {pal['ink']}; background: transparent;")
        self._stator.setStyleSheet(
            f"color: {colour if self._sub.status == dg.WARN else pal['muted']};"
            f" background: transparent;")
        self._stator.setToolTip("Peak current: stator, else supply, else PDH channel")
        self._temp.setStyleSheet(f"color: {pal['muted']}; background: transparent;")
        self._dot.set_status(self._sub.status)


def _first(*values: float | None) -> float | None:
    for v in values:
        if v is not None:
            return v
    return None


def _amps(v: float | None) -> str:
    return "—" if v is None else f"{abs(v):,.0f} A"


def _degrees(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f}°F"


class DiagnosticsOverlay(QWidget):
    """Screen A's board. Rebuilt on demand, never on a timer."""

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(parent)
        self._screen_id = screen_id
        self._tiles: list[StatTile] = []
        self._rows: list[_SubsystemRow] = []
        self._cols = 0
        self._data = dg.Dashboard()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._build_ui()
        config.team_changed.connect(lambda *_: self._refresh_style())
        config.screen_setting_changed.connect(self._on_setting_changed)
        # Self-subscribed, like every other overlay here: the board re-reads the
        # database whenever the imported-log set changes, whoever owns it. It
        # must not depend on a parent remembering to call `reload()`.
        config.logs_changed.connect(self.reload)
        self.reload()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(44, 36, 44, 36)
        root.setSpacing(0)
        self._root = root

        self._eyebrow = QLabel("")
        root.addWidget(self._eyebrow)
        root.addSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(20)
        self._title = QLabel("")
        head.addWidget(self._title, stretch=1)
        self._stamp = QLabel("")
        self._stamp.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignBottom)
        head.addWidget(self._stamp)
        root.addLayout(head)

        # One accent device on the surface, per the brand — the Trace leads the
        # eye from the headline into the numbers. Held to a fraction of the
        # width in `_apply_scale`: stretched across the whole board it stops
        # reading as a leading line and becomes a horizontal rule.
        trace_row = QHBoxLayout()
        trace_row.setContentsMargins(0, 0, 0, 0)
        self._trace = Trace()
        trace_row.addWidget(self._trace)
        trace_row.addStretch()
        root.addLayout(trace_row)
        root.addSpacing(18)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)
        root.addWidget(self._grid_host)

        root.addSpacing(16)
        self._sub_caption = QLabel("")
        root.addWidget(self._sub_caption)
        root.addSpacing(8)

        self._sub_host = QWidget()
        self._sub_box = QGridLayout(self._sub_host)
        self._sub_box.setContentsMargins(0, 0, 0, 0)
        self._sub_box.setSpacing(6)
        root.addWidget(self._sub_host)

        self._empty = QLabel("")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty, stretch=1)

        root.addStretch()

    # ── Data ──────────────────────────────────────────────────────────────

    def reload(self):
        """Re-read the database. Called on import, not on a timer."""
        try:
            self._data = dg.dashboard()
        except Exception:
            # A half-imported or unusual log must never take an audience screen
            # down. An empty board says "nothing imported" and stays up.
            self._data = dg.Dashboard()
        self._rebuild()

    def _rebuild(self):
        for t in self._tiles:
            self._grid.removeWidget(t)
            t.deleteLater()
        self._tiles = []
        for r in self._rows:
            self._sub_box.removeWidget(r)
            r.deleteLater()
        self._rows = []

        for reading in self._data.vitals:
            self._tiles.append(StatTile(reading))
        for sub in self._data.subsystems:
            self._rows.append(_SubsystemRow(sub))

        self._cols = 0            # force a re-flow: the tile set just changed
        self._sub_cols = 0
        has = bool(self._tiles or self._rows)
        self._empty.setVisible(not has)
        self._grid_host.setVisible(bool(self._tiles))
        self._sub_host.setVisible(bool(self._rows))
        self._sub_caption.setVisible(bool(self._rows))
        self._trace.setVisible(has)
        self._relayout_grid()
        self._relayout_subs()
        self._refresh_style()
        self._apply_scale()

    def _relayout_subs(self):
        """
        Flow the subsystem rows into columns.

        A robot with eleven mechanisms in one column runs off the bottom of a
        1080p panel; the rows are mostly whitespace, so the width is there to
        use. Columns are capped by width as well as by count — three across a
        55" panel reads fine, three across a 720p cart monitor does not.
        """
        if not self._rows:
            return
        n = len(self._rows)
        # Four rows deep at most, so the block stays under the tile grid on a
        # 720p panel without any measuring.
        want = 3 if n > 8 else 2 if n > 4 else 1
        cols = max(1, min(want, max(1, self.width() // _MIN_SUB_COL_W)))
        if cols == self._sub_cols:
            return
        self._sub_cols = cols
        per_col = -(-len(self._rows) // cols)      # ceil
        for i, row in enumerate(self._rows):
            self._sub_box.addWidget(row, i % per_col, i // per_col)
        for c in range(cols):
            self._sub_box.setColumnStretch(c, 1)

    def _relayout_grid(self):
        """Re-flow the tiles for the current width."""
        if not self._tiles:
            return
        # Measured from the overlay, not from `_grid_host`: the host's width is
        # still last layout's value when this runs from `resizeEvent`, so the
        # grid would settle one resize behind.
        left, _, right, _ = self._root.getContentsMargins()
        usable = max(1, self.width() - left - right)
        cols = max(1, min(len(self._tiles), _MAX_COLS, usable // _MIN_TILE_W))
        if cols == self._cols:
            return
        self._cols = cols
        for i, tile in enumerate(self._tiles):
            self._grid.addWidget(tile, i // cols, i % cols)
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)

    # ── Scale ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_grid()
        self._relayout_subs()
        self._apply_scale()

    def _apply_scale(self):
        h = self.height()
        if h <= 0:
            return
        pad_x = clamp(h * 0.048, 18, 80)
        pad_y = clamp(h * 0.038, 14, 64)
        self._root.setContentsMargins(pad_x, pad_y, pad_x, pad_y)

        eyebrow_px = clamp(h * 0.022, 10, 30)
        title_px = clamp(h * 0.068, 18, 82)
        stamp_px = clamp(h * 0.026, 11, 34)
        self._eyebrow.setFont(scaled_font(brand.FONT_DISPLAY, eyebrow_px,
                                          QFont.Weight.DemiBold))
        self._title.setFont(scaled_font(brand.FONT_DISPLAY, title_px,
                                        QFont.Weight.Bold))
        self._stamp.setFont(scaled_font("", stamp_px, QFont.Weight.Medium,
                                        mono=True))
        self._sub_caption.setFont(scaled_font(brand.FONT_DISPLAY, eyebrow_px,
                                              QFont.Weight.DemiBold))
        self._empty.setFont(scaled_font(brand.FONT_BODY,
                                        clamp(h * 0.030, 12, 32),
                                        QFont.Weight.Normal))

        gap = clamp(h * 0.014, 6, 20)
        self._grid.setSpacing(gap)
        self._sub_box.setSpacing(max(3, gap // 2))

        cap_px = clamp(h * 0.020, 9, 26)
        val_px = clamp(h * 0.052, 16, 62)
        det_px = clamp(h * 0.018, 9, 22)
        pad = clamp(h * 0.018, 8, 26)
        for tile in self._tiles:
            tile.apply_scale(cap_px, val_px, det_px, pad)

        # Brand §5.3: the flat run is ~1.5× the element it leads to, and the pad
        # sits just past the headline — not at the far edge of the screen.
        title_w = self._title.fontMetrics().horizontalAdvance(self._title.text())
        self._trace.setFixedWidth(clamp(title_w * 1.25, 160, self.width() * 0.62))

        if self._rows:
            row_h = clamp(h * 0.042, 20, 62)
            for row in self._rows:
                row.apply_scale(row_h, clamp(row_h * 0.42, 10, 28),
                                clamp(row_h * 0.26, 6, 18))


    # ── Style ─────────────────────────────────────────────────────────────

    def _on_setting_changed(self, screen: str, key: str, _value):
        if screen == self._screen_id and key == "theme":
            self._refresh_style()

    def _refresh_style(self):
        theme = config.screen_theme(self._screen_id) if self._screen_id else "dark"
        pal = brand.palette(theme)
        accent = config.active_team.primary_color
        team = config.active_team
        team_label = (f"{team.name} {team.number}" if team.name
                      else f"Team {team.number}").upper()

        d = self._data
        self._eyebrow.setText(f"{team_label}  ·  DIAGNOSTICS")
        self._title.setText(self._headline())
        self._stamp.setText(self._stamp_text())
        self._sub_caption.setText("SUBSYSTEMS")
        self._empty.setText(
            "No robot log imported yet.\n\n"
            "Import one on the control screen:\nPit Systems → Robot Logs."
        )

        self.setStyleSheet(f"background-color: {pal['bg']};")
        self._eyebrow.setStyleSheet(
            f"color: {accent}; background: transparent; letter-spacing: 3px;")
        self._sub_caption.setStyleSheet(
            f"color: {pal['muted']}; background: transparent; letter-spacing: 3px;")
        # The headline takes the worst status on the board. When nothing is
        # wrong it is ordinary title ink, so colour here always means something.
        worst = d.worst
        head_ink = (_STATUS_COLOR[worst] if worst in (dg.WARN, dg.FAULT)
                    else pal["title"])
        self._title.setStyleSheet(f"color: {head_ink}; background: transparent;")
        self._stamp.setStyleSheet(f"color: {pal['faint']}; background: transparent;")
        self._empty.setStyleSheet(f"color: {pal['muted']}; background: transparent;")
        self._trace.set_color(accent)

        for tile in self._tiles:
            tile.apply_colors(pal)
        for row in self._rows:
            row.apply_colors(pal)

    def _headline(self) -> str:
        d = self._data
        if d.empty:
            return "No log imported"
        if not d.faults:
            return "All clear"
        # The worst fault and *its* device count. Summing every fault's count
        # would read as one enormous number that belongs to nothing.
        worst = d.faults[0]
        return f"{worst.label} · {worst.value} {worst.unit}".strip()

    def _stamp_text(self) -> str:
        d = self._data
        if d.empty:
            return ""
        bits = []
        if d.match_key:
            bits.append(d.match_key.upper())
        if d.started_at:
            bits.append(d.started_at)
        bits.append(f"{d.duration_s / 60:.1f} min")
        return "  ·  ".join(bits)
