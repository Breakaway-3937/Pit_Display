"""
Control → Pit Systems → Batteries. The charging cart, live, from each
battery's own BFG. **Mock-up** (see `app/batteries/`).

What the crew asks of a cart, in order: **which one do I grab**, **how long
until that one's full**, **is anything wrong**. So:

* **The next battery leads the list**, marked NEXT in white (the shared
  "chosen" mark), picked by PWF's own rule: most charge left, then most
  effective capacity.
* **Each row's bar is the BFG's own charge indicator** (manual, "Charge
  Indicator"): the solid part is charge remaining, the outline is what the
  battery could hold at today's rate, both against a new battery's design
  capacity. A tired battery has a short outline, full or not.
* **Rates are "in"**: a BFG reports discharge as positive, so a battery on the
  charger reads negative; nobody at a cart should have to flip a sign.
* **Status is a dot**, never coloured type: amber charging (waiting on
  something), teal charged, grey idle or offline. Red only for a device-ID
  clash, where two BFGs' numbers are being mixed together and every figure in
  that row is wrong.

Simulated data says so on every line it touches.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from app import brand
from app.batteries import batteries, bfg
from app.widgets.brand_widgets import RoundedButton, StatusDot, eyebrow, mono_font
from app.widgets.helpers import clear_layout, divider, label

_STATE_TEXT = {
    "charging_cc": "Charging · constant current",
    "charging_cv": "Charging · topping off",
    "charging_trickle": "Charging · trickle",
    "charged": "Charged · ready",
    "discharging": "Off the charger",
    "resting": "Resting (10 h+ untouched)",
    "unknown": "Starting up",
}
_ROW_H = 112


def _dot_color(b: bfg.Battery, online: bool) -> str:
    if b.clash:
        return brand.STATUS_FAULT
    if not online:
        return brand.STATUS_IDLE
    if b.charging:
        return brand.STATUS_PENDING
    if b.state == "charged":
        return brand.STATUS_ONLINE
    return brand.STATUS_IDLE


def _duration(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _age(seconds: int | None) -> str:
    if not seconds:
        return ""
    days = seconds / 86400
    return f"{days / 30.4:.0f} mo" if days >= 60 else f"{days:.0f} d"


class _BatteryRow(QWidget):
    """One battery, painted: identity left, the charge indicator and rates in
    the middle, ten minutes of charge current on the right."""

    def __init__(self, device_id: int, parent=None):
        super().__init__(parent)
        self.device_id = device_id
        self.battery: bfg.Battery | None = None
        self.online = False
        self.is_next = False
        self.simulated = False
        self.history: list[tuple[float, float, float]] = []
        self.setMinimumHeight(_ROW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(640, _ROW_H)

    def set_state(self, b: bfg.Battery, online: bool, is_next: bool,
                  simulated: bool, history) -> None:
        self.battery, self.online, self.is_next = b, online, is_next
        self.simulated, self.history = simulated, history
        self.update()

    @staticmethod
    def _font(family: str, px: int, weight=QFont.Weight.Normal) -> QFont:
        f = QFont(family)
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    def paintEvent(self, _event):
        b = self.battery
        if b is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        ink, muted, faint = QColor(brand.INK_DARK), QColor(brand.MUTED_DARK), QColor(brand.FAINT_DARK)
        wide = r.width() >= 720
        left_w = 224.0
        spark_w = 170.0 if wide else 0.0
        mid = QRectF(r.x() + left_w + 20, r.y() + 16,
                     r.width() - left_w - 20 - (spark_w + 24 if spark_w else 0), r.height() - 32)

        # ── identity ────────────────────────────────────────────────────
        p.setFont(self._font(brand.FONT_DISPLAY, 20, QFont.Weight.DemiBold))
        p.setPen(ink)
        name_y = r.y() + 16 + p.fontMetrics().ascent()
        p.drawText(QPointF(r.x(), name_y), b.name)
        if self.is_next:
            # The chosen mark: a white pill, carbon type.
            nx = r.x() + p.fontMetrics().horizontalAdvance(b.name) + 12
            p.setFont(self._font(brand.FONT_DISPLAY, 11, QFont.Weight.Bold))
            tw = p.fontMetrics().horizontalAdvance("NEXT")
            pill = QRectF(nx, name_y - 15, tw + 16, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(brand.WHITE))
            p.drawRoundedRect(pill, 10, 10)
            p.setPen(QColor(brand.CARBON))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, "NEXT")

        dot = QRectF(r.x(), name_y + 14, 10, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_dot_color(b, self.online)))
        p.drawEllipse(dot)
        p.setFont(self._font(brand.FONT_BODY, 13))
        p.setPen(muted)
        if b.clash:
            state = f"Two BFGs share ID {b.device_id}"
        elif not self.online:
            state = f"Not heard for {b.age_since(time.time()):.0f} s"
        else:
            state = _STATE_TEXT.get(b.state, b.state)
        p.drawText(QPointF(dot.right() + 8, dot.bottom()), state)

        facts = []
        if b.health is not None:
            facts.append(f"Health {b.health:.0%}")
        if b.cycles is not None:
            facts.append(f"{b.cycles} cycles")
        if b.age_s:
            facts.append(_age(b.age_s))
        p.setFont(self._font(brand.FONT_BODY, 12))
        p.setPen(faint)
        p.drawText(QPointF(r.x(), dot.bottom() + 24), " · ".join(facts))

        # ── the charge indicator ────────────────────────────────────────
        design = b.design_mah or 18_000
        bar = QRectF(mid.x(), mid.y() + 4, mid.width(), 16)
        track = QPainterPath()
        track.addRoundedRect(bar, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(brand.CARBON_SURF2))
        p.drawPath(track)
        if b.effective_mah:
            cap_w = bar.width() * min(1.0, b.effective_mah / design)
            p.setPen(QPen(QColor(brand.N400), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), cap_w, bar.height()), 8, 8)
        if b.soc_mah is not None:
            fill_w = bar.width() * min(1.0, b.soc_mah / design)
            if fill_w > 1:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(brand.WHITE) if self.online else QColor(brand.N500))
                p.drawRoundedRect(QRectF(bar.x(), bar.y(), max(fill_w, 16), bar.height()), 8, 8)

        # rates, under the bar
        y1 = bar.bottom() + 26
        p.setFont(mono_font(16))
        p.setPen(ink)
        fm = p.fontMetrics()
        rate_end = mid.x()
        if b.voltage_v is not None and b.current_a is not None:
            if b.charging or b.charge_rate_a > 0.05:
                rate = f"{b.charge_rate_a:.1f} A in"
            elif b.current_a > 0.05:
                rate = f"{b.current_a:.1f} A out"
            else:
                rate = "0.0 A"
            power = b.power_w or 0.0
            rates = f"{rate}  ·  {power:.0f} W  ·  {b.voltage_v:.2f} V"
            p.drawText(QPointF(mid.x(), y1), rates)
            rate_end = mid.x() + fm.horizontalAdvance(rates)
        if b.soc_mah is not None and b.fraction is not None:
            # The full figure where it fits, the percentage where it doesn't.
            for right in (f"{b.fraction:.0%}  ·  {b.soc_mah / 1000:.1f} / "
                          f"{(b.effective_mah or 0) / 1000:.1f} Ah", f"{b.fraction:.0%}"):
                if mid.right() - fm.horizontalAdvance(right) > rate_end + 24:
                    p.drawText(QPointF(mid.right() - fm.horizontalAdvance(right), y1), right)
                    break

        p.setFont(self._font(brand.FONT_BODY, 13))
        p.setPen(muted)
        eta = b.minutes_to_full()
        if not self.online:
            note = "Offline: unplugged from the cart's CAN, or its battery is flat"
        elif eta is not None:
            # Constant current: rate x missing charge is honest. Past it the
            # current keeps falling, so a time from today's rate would only be
            # a lower bound; say how much is left instead.
            note = (f"About {_duration(eta)} to full" if b.state == "charging_cc"
                    else f"Topping off · {max(0, 100 - round((b.fraction or 0) * 100))}% to go")
        elif b.state == "charged":
            note = "Full. Can come off the charger"
        elif b.state == "discharging":
            note = "Needs the charger"
        else:
            note = ""
        if self.simulated and note:
            note += "  (simulated)"
        p.drawText(QPointF(mid.x(), y1 + 22), note)

        # ── ten minutes of charge current ───────────────────────────────
        if spark_w:
            sr = QRectF(r.right() - spark_w, r.y() + 20, spark_w, r.height() - 52)
            self._spark(p, sr)
            p.setFont(mono_font(11))
            p.setPen(faint)
            pts = self.history
            span = (pts[-1][0] - pts[0][0]) if len(pts) > 1 else 0
            p.drawText(QPointF(sr.x(), sr.bottom() + 18),
                       f"amps in · {max(1, min(10, round(span / 60)))} min")

        p.end()

    def _spark(self, p: QPainter, sr: QRectF) -> None:
        pts = self.history
        p.setPen(QPen(QColor(brand.CARBON_LINE), 1))
        p.drawLine(QPointF(sr.x(), sr.bottom()), QPointF(sr.right(), sr.bottom()))
        if len(pts) < 2:
            return
        top = max(6.0, max(a for _t, a, _v in pts) * 1.15)
        # The span it has, up to ten minutes: two minutes of history drawn on
        # a ten-minute axis is a smear against the right edge.
        span = max(60.0, min(600.0, pts[-1][0] - pts[0][0]))
        t0 = pts[-1][0] - span
        path = QPainterPath()
        for i, (t, amps, _v) in enumerate(pts):
            x = sr.x() + sr.width() * max(0.0, (t - t0) / span)
            y = sr.bottom() - sr.height() * max(0.0, amps) / top
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(QColor(brand.N400), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        last = path.currentPosition()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(brand.WHITE))
        p.drawEllipse(last, 3, 3)


class BatteryPanel(QWidget):
    """The cart: a source line, a summary, then one row per battery."""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(eyebrow("Charging cart · battery fuel gauges"))
        root.addSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        self._dot = StatusDot()
        head.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._head = label("", "stat_value")
        self._head.setWordWrap(True)
        self._head.setStyleSheet("font-size: 15px;")
        head.addWidget(self._head, stretch=1)
        root.addLayout(head)
        root.addSpacing(6)
        self._sub = label("", "stat_label")
        self._sub.setWordWrap(True)
        root.addWidget(self._sub)
        root.addSpacing(10)
        self._sim_btn = RoundedButton("", variant="secondary")
        self._sim_btn.setMinimumHeight(46)
        self._sim_btn.clicked.connect(self._toggle_sim)
        root.addWidget(self._sim_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(8)

        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(0)
        root.addLayout(self._rows_box)
        self._rows: dict[int, tuple[QWidget, _BatteryRow, RoundedButton]] = {}

        batteries.changed.connect(self._refresh)
        batteries.source_changed.connect(self._rebuild)
        self._rebuild()

    # Bound methods, never lambdas: the service outlives this panel.

    def _toggle_sim(self) -> None:
        batteries.use_simulation(not batteries.simulated)

    def _identify(self) -> None:
        btn = self.sender()
        if btn is not None:
            batteries.identify(int(btn.property("device_id")))

    def _rebuild(self) -> None:
        clear_layout(self._rows_box)
        self._rows.clear()
        self._refresh()

    def _row_for(self, dev: int) -> tuple[QWidget, _BatteryRow, RoundedButton]:
        if dev not in self._rows:
            holder = QWidget()
            # The row's closing hairline spans the button too. A plain QWidget
            # ignores a stylesheet border without WA_StyledBackground.
            holder.setObjectName("battery_row")
            holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            holder.setStyleSheet(f"QWidget#battery_row {{ border-bottom: 1px solid "
                                 f"{brand.CARBON_LINE}; }}")
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(16)
            row = _BatteryRow(dev)
            lay.addWidget(row, stretch=1)
            btn = RoundedButton("Identify", variant="secondary")
            btn.setMinimumHeight(46)
            btn.setProperty("device_id", dev)
            btn.setToolTip("Shows this BFG's identification page on its own screen")
            btn.clicked.connect(self._identify)
            lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._rows[dev] = (holder, row, btn)
        return self._rows[dev]

    def _refresh(self) -> None:
        active, sim = batteries.active, batteries.simulated
        self._sim_btn.setVisible(not active or sim)
        self._sim_btn.setText("Stop the simulation" if sim else "Show the simulated cart")

        bats = batteries.batteries()
        if not active:
            self._dot.set_color(brand.STATUS_IDLE)
            self._head.setText("No battery bus on this machine")
            self._sub.setText(
                "Mock-up. Each battery's BFG talks CAN; chain the cart's CAN "
                "pigtails to a USB-CAN adapter on this machine and set "
                "PIT_BATTERY_CAN (e.g. socketcan:can0 for a CANivore on Linux, "
                "gs_usb:0 for a USB-CAN adapter on Windows). Nothing here "
                "touches the robot.")
        elif batteries.error:
            self._dot.set_color(brand.STATUS_FAULT)
            self._head.setText("The battery bus isn't readable")
            self._sub.setText(batteries.describe())
        else:
            online = [b for b in bats if batteries.online(b)]
            charging = [b for b in online if b.charging]
            amps = sum(b.charge_rate_a for b in charging)
            watts = sum(b.power_w or 0 for b in charging)
            nxt = batteries.next_battery()
            self._dot.set_color(brand.STATUS_ONLINE if online else brand.STATUS_PENDING)
            if not bats:
                self._head.setText("Listening… no BFG heard yet")
            else:
                self._head.setText(
                    (f"Next battery: {nxt.name}" if nxt else "No battery ready")
                    + f"  ·  {len(charging)} charging, {amps:.1f} A in, {watts:.0f} W")
            self._sub.setText(batteries.describe()
                              + ("  ·  SIMULATED DATA, not a real cart" if sim else ""))

        seen = {b.device_id for b in bats}
        for dev in list(self._rows):
            if dev not in seen:
                holder = self._rows.pop(dev)[0]
                holder.setParent(None)
                holder.deleteLater()
        nxt = batteries.next_battery()
        now = time.time()
        for i, b in enumerate(bats):
            holder, row, btn = self._row_for(b.device_id)
            if self._rows_box.indexOf(holder) != i:
                self._rows_box.removeWidget(holder)
                self._rows_box.insertWidget(i, holder)
            row.set_state(b, batteries.online(b), b is nxt, sim, batteries.history(b.device_id))
            sent = batteries.identify_sent.get(b.device_id, 0)
            btn.setText("Sent" if now - sent < 3 else "Identify")
            btn.setEnabled(b.serial is not None and batteries.online(b))
