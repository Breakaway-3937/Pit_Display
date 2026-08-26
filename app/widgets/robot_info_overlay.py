"""
Robot Info — screen B's companion to the diagnostics board on screen A.

## Why two screens and not one big one

A pit has two overhead panels and a crew that reads them from different places.
Splitting the same data across both is how you end up with two half-boards
nobody can use, so the split is by **question**, not by column count:

| | asks | answers with |
|---|---|---|
| **A — Diagnostics** | *is anything wrong right now?* | vitals, subsystem status |
| **B — Robot Info** | *what is wrong, on which motor, and what was this log?* | the fault list by name, the per-motor table, provenance |

A is glanceable from ten feet: a headline and a grid of numbers. B is the thing
you walk up to when A has gone amber, and it is dense on purpose — the fault
names and CAN ids are what you take to the robot.

Both read `app.robot.diagnostics`; neither computes anything. Both scale from
the live widget height, like every other overlay here.

**The motor table shows the operator's names, not CAN ids** — `DeviceRow.display`
falls back to `TalonFX 11` only where nobody has typed one. Naming motors in
Control → Pit Systems → Robot Logs is what turns this screen from a list of
addresses into a list of mechanisms.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from app import brand
from app.config import config
from app.robot import diagnostics as dg
from app.widgets.brand_widgets import Trace
from app.widgets.diagnostics_overlay import (
    _STATUS_COLOR, StatusDot, clamp, scaled_font,
)

# Hard caps, not a measured fit.
#
# An earlier version measured where the table had landed and hid whatever fell
# past the fold. It read geometry that had not settled yet, so it hid rows there
# was room for — and the failure was silent, which is the worst way for a
# diagnostics screen to be wrong. Column flow made caps viable instead: six
# faults and eight motors in two columns always fit a 720p panel, so the
# arithmetic is done here once rather than guessed at every resize.
#
# Both lists are sorted worst-first upstream, so what a cap drops is always the
# healthy end.
_MAX_FAULT_ROWS = 6
_MAX_MOTOR_ROWS = 8


def _budget(h: int) -> tuple[int, int, bool]:
    """
    (faults, motors, show the CAN-id line) for a panel this tall.

    Banded rather than continuous, so the board does not reshuffle while a
    window is being dragged, and computed from the height alone rather than
    from measured geometry — the numbers below are what actually fits, checked
    against a render at each size.
    """
    if h >= 900:                       # 1080p and up: everything
        return _MAX_FAULT_ROWS, _MAX_MOTOR_ROWS, True
    if h >= 760:
        return 5, 6, True
    if h >= 620:                       # 720p: the id list is unreadable anyway
        return 5, 6, False
    return 4, 4, False                 # a cart monitor

# A motor column holds a name and three numbers; narrower and the name elides.
_MIN_MOTOR_COL_W = 420


class _FaultRow(QFrame):
    """One latched fault: what it was, how many motors, and which."""

    def __init__(self, reading: dg.Reading, parent=None):
        super().__init__(parent)
        self.setObjectName("fault_row")
        self._reading = reading

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(12)

        self._dot = StatusDot(reading.status)
        row.addWidget(self._dot)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self._name = QLabel(reading.label)
        text.addWidget(self._name)
        self._who = QLabel(reading.detail)
        self._who.setWordWrap(True)
        text.addWidget(self._who)
        row.addLayout(text, stretch=1)

        self._count = QLabel(f"{reading.value} {reading.unit}".strip())
        self._count.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._count)

    def apply_scale(self, name_px: int, who_px: int, dot_px: int, pad: int,
                    show_who: bool = True):
        self.layout().setContentsMargins(pad, int(pad * 0.55), pad,
                                         int(pad * 0.55))
        self._dot.set_side(dot_px)
        self._name.setFont(scaled_font(brand.FONT_DISPLAY, name_px,
                                       QFont.Weight.DemiBold))
        self._who.setFont(scaled_font(brand.FONT_BODY, who_px,
                                      QFont.Weight.Normal))
        self._count.setFont(scaled_font("", name_px, QFont.Weight.Bold,
                                        mono=True))
        # The CAN-id list is the first thing to go on a short screen: at that
        # size nobody can read it, and the fault name is what matters.
        self._who.setVisible(show_who and bool(self._reading.detail)
                             and who_px >= 10)

    def apply_colors(self, pal: dict):
        colour = _STATUS_COLOR.get(self._reading.status, brand.STATUS_IDLE)
        self.setStyleSheet(
            f"#fault_row {{ background: {pal['surface']};"
            f" border-radius: {brand.R_BTN}px; }}")
        self._name.setStyleSheet(f"color: {colour}; background: transparent;")
        self._who.setStyleSheet(f"color: {pal['faint']}; background: transparent;")
        self._count.setStyleSheet(f"color: {pal['muted']}; background: transparent;")
        self._dot.set_status(self._reading.status)


class _MotorRow(QFrame):
    """One CAN device across the table: name, temp, current, volts."""

    _COLS = ("temp", "amps", "volts")

    def __init__(self, motor: dg.MotorRow | None, header: bool = False,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("motor_row")
        self._motor = motor
        self._header = header

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 4, 14, 4)
        row.setSpacing(10)

        self._dot = StatusDot(motor.status if motor else dg.IDLE)
        self._dot.setVisible(not header)
        row.addWidget(self._dot)

        self._name = QLabel("MOTOR" if header else motor.label)
        row.addWidget(self._name, stretch=1)

        if header:
            values = ("TEMP", "PEAK A", "MIN V")
        else:
            values = (
                "—" if motor.temp_c is None else f"{motor.temp_c:,.0f}°C",
                "—" if motor.stator_a is None else f"{abs(motor.stator_a):,.0f}",
                "—" if motor.supply_v is None else f"{motor.supply_v:,.2f}",
            )
        self._cells = []
        for v in values:
            lbl = QLabel(v)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lbl)
            self._cells.append(lbl)

    def apply_scale(self, h: int, text_px: int, dot_px: int):
        self.setFixedHeight(h)
        self._dot.set_side(dot_px)
        self._name.setFont(scaled_font(
            brand.FONT_DISPLAY if self._header else brand.FONT_BODY,
            int(text_px * (0.82 if self._header else 1.0)),
            QFont.Weight.DemiBold if self._header else QFont.Weight.Medium))
        num = scaled_font(brand.FONT_DISPLAY if self._header else "",
                          int(text_px * (0.74 if self._header else 0.94)),
                          QFont.Weight.DemiBold, mono=not self._header)
        for cell in self._cells:
            cell.setFont(num)
            cell.setFixedWidth(int(text_px * 4.4))

    def apply_colors(self, pal: dict):
        if self._header:
            self.setStyleSheet("#motor_row { background: transparent; }")
            for w in (self._name, *self._cells):
                w.setStyleSheet(
                    f"color: {pal['faint']}; background: transparent;"
                    " letter-spacing: 2px;")
            return
        colour = _STATUS_COLOR.get(self._motor.status, brand.STATUS_IDLE)
        self.setStyleSheet(
            f"#motor_row {{ background: {pal['surface']};"
            f" border-radius: {brand.R_BTN}px; }}")
        self._name.setStyleSheet(f"color: {pal['ink']}; background: transparent;")
        self._cells[0].setStyleSheet(
            f"color: {colour if self._motor.status == dg.FAULT else pal['muted']};"
            f" background: transparent;")
        for cell in self._cells[1:]:
            cell.setStyleSheet(f"color: {pal['muted']}; background: transparent;")
        self._dot.set_status(self._motor.status)


class RobotInfoOverlay(QWidget):
    """Screen B's board."""

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(parent)
        self._screen_id = screen_id
        self._fault_rows: list[_FaultRow] = []
        self._motor_rows: list[_MotorRow] = []
        self._data = dg.Dashboard()
        self._motor_cols = 0
        self._shown_motors = _MAX_MOTOR_ROWS
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._build_ui()
        config.team_changed.connect(lambda *_: self._refresh_style())
        config.screen_setting_changed.connect(self._on_setting_changed)
        # Self-subscribed, like every other overlay here: the board re-reads the
        # database whenever the imported-log set changes, whoever owns it. It
        # must not depend on a parent remembering to call `reload()`.
        config.logs_changed.connect(self.reload)
        self.reload()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(44, 36, 44, 36)
        root.setSpacing(0)
        self._root = root

        self._eyebrow = QLabel("")
        root.addWidget(self._eyebrow)
        root.addSpacing(4)
        self._title = QLabel("")
        root.addWidget(self._title)

        # Held to a fraction of the width in `_apply_scale` — a Trace stretched
        # edge to edge reads as a horizontal rule, not a leading line.
        trace_row = QHBoxLayout()
        trace_row.setContentsMargins(0, 0, 0, 0)
        self._trace = Trace()
        trace_row.addWidget(self._trace)
        trace_row.addStretch()
        root.addLayout(trace_row)
        root.addSpacing(16)

        self._fault_caption = QLabel("")
        root.addWidget(self._fault_caption)
        root.addSpacing(6)
        self._fault_host = QWidget()
        self._fault_box = QVBoxLayout(self._fault_host)
        self._fault_box.setContentsMargins(0, 0, 0, 0)
        self._fault_box.setSpacing(6)
        root.addWidget(self._fault_host)

        root.addSpacing(18)
        self._motor_caption = QLabel("")
        root.addWidget(self._motor_caption)
        root.addSpacing(4)
        self._motor_host = QWidget()
        self._motor_box = QGridLayout(self._motor_host)
        self._motor_box.setContentsMargins(0, 0, 0, 0)
        self._motor_box.setSpacing(4)
        root.addWidget(self._motor_host)

        self._empty = QLabel("")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty, stretch=1)

        root.addStretch()

        self._footer = QLabel("")
        self._footer.setWordWrap(True)
        # Fixed, so the provenance line keeps its own space instead of being
        # painted over by a motor table that ran long.
        self._footer.setSizePolicy(QSizePolicy.Policy.Preferred,
                                   QSizePolicy.Policy.Fixed)
        root.addWidget(self._footer)

    # ── Data ──────────────────────────────────────────────────────────────

    def reload(self):
        try:
            self._data = dg.dashboard()
        except Exception:
            self._data = dg.Dashboard()
        self._rebuild()

    def _rebuild(self):
        for r in self._fault_rows:
            self._fault_box.removeWidget(r)
            r.deleteLater()
        self._fault_rows = []
        for r in self._motor_rows:
            self._motor_box.removeWidget(r)
            r.deleteLater()
        self._motor_rows = []

        for reading in self._data.faults[:_MAX_FAULT_ROWS]:
            row = _FaultRow(reading)
            self._fault_box.addWidget(row)
            self._fault_rows.append(row)

        for m in self._data.motors[:_MAX_MOTOR_ROWS]:
            self._motor_rows.append(_MotorRow(m))
        self._motor_cols = 0

        has = bool(self._fault_rows or self._motor_rows)
        self._empty.setVisible(not has)
        self._trace.setVisible(has)
        self._fault_host.setVisible(bool(self._fault_rows))
        self._fault_caption.setVisible(bool(self._fault_rows))
        self._motor_host.setVisible(bool(self._motor_rows))
        self._motor_caption.setVisible(bool(self._motor_rows))
        self._relayout_motors()
        self._refresh_style()
        self._apply_scale()

    def _relayout_motors(self):
        """
        Flow the motor rows into columns.

        Eleven motors plus a header is more rows than a 1080p panel has room
        for under the fault list, and each row is a short name and three
        numbers — the width is there.
        """
        if not self._motor_rows:
            return
        n = min(len(self._motor_rows), self._shown_motors)
        want = 2 if n > 4 else 1
        cols = max(1, min(want, max(1, self.width() // _MIN_MOTOR_COL_W)))
        if cols == self._motor_cols:
            return
        self._motor_cols = cols
        per_col = -(-n // cols)
        for i, row in enumerate(self._motor_rows):
            row.setVisible(i < n)
            if i < n:
                self._motor_box.addWidget(row, i % per_col, i // per_col)
        for c in range(cols):
            self._motor_box.setColumnStretch(c, 1)

    # ── Scale ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_motors()
        self._apply_scale()

    def _apply_scale(self):
        h = self.height()
        if h <= 0:
            return
        pad_x = clamp(h * 0.048, 18, 80)
        pad_y = clamp(h * 0.038, 14, 64)
        self._root.setContentsMargins(pad_x, pad_y, pad_x, pad_y)

        eyebrow_px = clamp(h * 0.022, 10, 30)
        title_px = clamp(h * 0.062, 17, 74)
        self._eyebrow.setFont(scaled_font(brand.FONT_DISPLAY, eyebrow_px,
                                          QFont.Weight.DemiBold))
        self._title.setFont(scaled_font(brand.FONT_DISPLAY, title_px,
                                        QFont.Weight.Bold))
        for cap in (self._fault_caption, self._motor_caption):
            cap.setFont(scaled_font(brand.FONT_DISPLAY, eyebrow_px,
                                    QFont.Weight.DemiBold))
        self._footer.setFont(scaled_font("", clamp(h * 0.018, 9, 22),
                                         QFont.Weight.Normal, mono=True))
        self._empty.setFont(scaled_font(brand.FONT_BODY, clamp(h * 0.030, 12, 32),
                                        QFont.Weight.Normal))

        gap = clamp(h * 0.010, 4, 14)
        self._fault_box.setSpacing(gap)
        self._motor_box.setSpacing(max(2, gap // 2))

        max_faults, max_motors, show_who = _budget(h)
        fault_px = clamp(h * 0.030, 12, 36)
        for i, row in enumerate(self._fault_rows):
            row.setVisible(i < max_faults)
            row.apply_scale(fault_px, clamp(fault_px * 0.60, 9, 22),
                            clamp(fault_px * 0.52, 7, 20),
                            clamp(h * 0.016, 8, 24), show_who)

        title_w = self._title.fontMetrics().horizontalAdvance(self._title.text())
        self._trace.setFixedWidth(clamp(title_w * 1.4, 160, self.width() * 0.62))

        if self._motor_rows:
            if max_motors != self._shown_motors:
                self._shown_motors = max_motors
                self._motor_cols = 0            # the flow depends on the count
                self._relayout_motors()
            row_h = clamp(h * 0.036, 18, 54)
            for row in self._motor_rows:
                row.apply_scale(row_h, clamp(row_h * 0.46, 10, 28),
                                clamp(row_h * 0.28, 6, 18))


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

        self._eyebrow.setText(f"{team_label}  ·  ROBOT INFO")
        self._title.setText("Nothing latched" if not d.faults else "What tripped")
        self._fault_caption.setText("LATCHED FAULTS")
        self._motor_caption.setText("MOTORS  ·  PEAK TEMP / CURRENT / MIN VOLTS")
        self._empty.setText(
            "No robot log imported yet.\n\n"
            "Import one on the control screen:\nPit Systems → Robot Logs."
        )
        self._footer.setText(self._provenance())

        self.setStyleSheet(f"background-color: {pal['bg']};")
        self._eyebrow.setStyleSheet(
            f"color: {accent}; background: transparent; letter-spacing: 3px;")
        self._title.setStyleSheet(f"color: {pal['title']}; background: transparent;")
        for cap in (self._fault_caption, self._motor_caption):
            cap.setStyleSheet(
                f"color: {pal['muted']}; background: transparent;"
                " letter-spacing: 3px;")
        self._empty.setStyleSheet(f"color: {pal['muted']}; background: transparent;")
        self._footer.setStyleSheet(f"color: {pal['faint']}; background: transparent;")
        self._trace.set_color(accent)

        for row in self._fault_rows:
            row.apply_colors(pal)
        for row in self._motor_rows:
            row.apply_colors(pal)

    def _provenance(self) -> str:
        """Which log this came from — the screen has to be able to say."""
        d = self._data
        if d.empty:
            return ""
        bits = list(d.sources) or [d.source_name]
        stamp = []
        if d.match_key:
            stamp.append(d.match_key.upper())
        if d.started_at:
            stamp.append(d.started_at)
        return "  ·  ".join(stamp + bits)
