"""
LED control panel for the control screen.

Mirrors the LED service's state rather than holding its own: every control
writes to `leds` and the whole panel re-reads on `state_changed`. That way the
panel stays correct when something else moves the strips — a team switch, a
mode change, or the CAD viewer focusing a subsystem.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog, QGridLayout, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget,
)

from app import brand
from app.admin import admin
from app.config import config
from app.leds import leds
from app.leds.effects import PRESETS
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, eyebrow, mono_font,
)
from app.widgets.helpers import divider, label
from app.widgets.toggle_switch import ToggleSwitch

# Quick picks. Team colour comes first; the rest are the brand palette plus the
# handful of colours that actually read well on a strip across a pit.
SWATCHES = [
    ("Team", None),
    ("Red", brand.RED),
    ("Amber", "#E08A1E"),
    ("Teal", "#2E8B7F"),
    ("Blue", "#2B3A67"),
    ("Violet", "#6B4E71"),
    ("White", "#FFFFFF"),
]


class _StatusDot(QWidget):
    """A small filled circle — brand status colours (§7)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor(brand.STATUS_IDLE)

    def set_state(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(0, 0, 10, 10)
        p.end()


class LEDPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset_buttons: dict[str, RoundedButton] = {}
        self._swatch_buttons: list[tuple[RoundedButton, str | None]] = []
        self._syncing = False
        self._build()

        leds.connection_changed.connect(self._on_connection)
        leds.state_changed.connect(self._sync)
        leds.error.connect(self._on_error)
        config.team_changed.connect(self._on_team_changed)
        admin.lock_state_changed.connect(self._apply_lock)

        self._on_connection(leds.connected, leds.status)
        self._apply_lock(admin.unlocked)
        self._sync()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Status ----------------------------------------------------------
        status_card = RoundedFrame(fill=brand.CARBON_SURF2, border=brand.CARBON_LINE,
                                   radius=brand.R_BTN)
        sc = QHBoxLayout(status_card)
        sc.setContentsMargins(14, 11, 14, 11)
        sc.setSpacing(10)
        self._dot = _StatusDot()
        sc.addWidget(self._dot)
        self._status_lbl = label("Starting…", "stat_value")
        self._status_lbl.setWordWrap(True)
        sc.addWidget(self._status_lbl, stretch=1)
        self._identify_btn = RoundedButton("Identify", variant="ghost")
        self._identify_btn.setFixedWidth(88)
        self._identify_btn.clicked.connect(leds.identify)
        sc.addWidget(self._identify_btn)
        root.addWidget(status_card)
        root.addSpacing(16)

        # Master ----------------------------------------------------------
        master_row = QHBoxLayout()
        master_row.setContentsMargins(0, 0, 0, 0)
        self._enable_toggle = ToggleSwitch()
        self._enable_toggle.setChecked(True)
        self._enable_toggle.toggled.connect(self._on_enable_toggled)
        master_row.addWidget(self._enable_toggle)
        master_row.addSpacing(10)
        self._enable_lbl = label("Strips on", "stat_value")
        master_row.addWidget(self._enable_lbl)
        master_row.addStretch()
        root.addLayout(master_row)
        root.addSpacing(6)
        root.addWidget(label(
            "Master switch for the pit strips. Event staff sometimes ask.",
            "stat_label"))
        root.addSpacing(14)

        # ── Everything below is admin-only ────────────────────────────────
        # On/off stays available to any operator; tuning does not, so a visitor
        # cannot rewrite the pit's look while nobody is watching. The whole
        # block is hidden rather than disabled — greyed-out controls just
        # invite people to ask who has the password.
        self._locked_notice = label(
            "Brightness, looks and colour are admin-only. Open the admin panel "
            "from the Breakaway mark in the top-left to unlock.",
            "stat_label")
        self._locked_notice.setWordWrap(True)
        root.addWidget(self._locked_notice)

        self._advanced = QWidget()
        root.addWidget(self._advanced)
        adv = QVBoxLayout(self._advanced)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setSpacing(0)
        root = adv          # everything from here lands in the gated container

        # Brightness / speed ----------------------------------------------
        self._bright_slider, bright_box, self._bright_value = self._slider_row(
            "Brightness", 0, 255, leds.brightness, self._on_brightness)
        root.addWidget(bright_box)
        self._speed_slider, speed_box, self._speed_value = self._slider_row(
            "Speed", 0, 255, leds.speed, self._on_speed)
        root.addWidget(speed_box)
        root.addSpacing(10)
        root.addWidget(divider())
        root.addSpacing(14)

        # Presets ----------------------------------------------------------
        root.addWidget(eyebrow("Look"))
        root.addSpacing(10)
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, preset in enumerate(PRESETS):
            btn = RoundedButton(preset.label, variant="secondary",
                                accent=config.active_team.primary_color)
            btn.setToolTip(preset.description)
            btn.clicked.connect(lambda _c, k=preset.key: leds.apply_preset(k))
            self._preset_buttons[preset.key] = btn
            grid.addWidget(btn, i // 2, i % 2)
        root.addLayout(grid)
        root.addSpacing(16)

        # Colour -----------------------------------------------------------
        root.addWidget(eyebrow("Colour"))
        root.addSpacing(10)
        sw = QGridLayout()
        sw.setSpacing(8)
        for i, (name, hex_color) in enumerate(SWATCHES):
            btn = RoundedButton(name, variant="secondary",
                                accent=hex_color or config.active_team.primary_color)
            btn.clicked.connect(lambda _c, h=hex_color: self._pick_swatch(h))
            self._swatch_buttons.append((btn, hex_color))
            sw.addWidget(btn, i // 3, i % 3)
        custom = RoundedButton("Custom…", variant="ghost")
        custom.clicked.connect(self._pick_custom)
        sw.addWidget(custom, (len(SWATCHES)) // 3, (len(SWATCHES)) % 3)
        root.addLayout(sw)
        root.addSpacing(14)

        # Follow toggles -----------------------------------------------------
        self._follow_team_toggle, follow_team_row = self._toggle_row(
            "Follow team colour", leds.follow_team, self._on_follow_team)
        root.addWidget(follow_team_row)
        self._follow_mode_toggle, follow_mode_row = self._toggle_row(
            "Follow display mode", leds.follow_mode, self._on_follow_mode)
        root.addWidget(follow_mode_row)
        root.addSpacing(6)
        root.addWidget(label(
            "Judges goes calm, Lunch goes warm amber, Standard runs the team wash.",
            "stat_label"))
        root.addSpacing(16)

        save = RoundedButton("Save as boot default", variant="primary",
                             accent=config.active_team.primary_color)
        save.setToolTip(
            "Persist this look to the controller's EEPROM. It becomes what the "
            "strips show on power-up and what the watchdog falls back to."
        )
        save.clicked.connect(leds.save_as_default)
        self._save_btn = save
        root.addWidget(save)

    def _slider_row(self, title: str, lo: int, hi: int, value: int, handler):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(label(title, "stat_label"))
        head.addStretch()
        value_lbl = label(str(value), "stat_value")
        value_lbl.setFont(mono_font())
        head.addWidget(value_lbl)
        v.addLayout(head)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(value)
        slider.valueChanged.connect(lambda x: (value_lbl.setText(str(x)), handler(x)))
        v.addWidget(slider)
        return slider, box, value_lbl

    def _toggle_row(self, text: str, checked: bool, handler):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        toggle = ToggleSwitch()
        toggle.setChecked(checked)
        toggle.toggled.connect(handler)
        h.addWidget(toggle)
        h.addSpacing(10)
        h.addWidget(label(text, "stat_value"))
        h.addStretch()
        return toggle, row

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_enable_toggled(self, on: bool):
        if self._syncing:
            return
        self._enable_lbl.setText("Strips on" if on else "Strips off")
        leds.set_enabled(on)

    def _on_brightness(self, value: int):
        if not self._syncing:
            leds.set_brightness(value)

    def _on_speed(self, value: int):
        if not self._syncing:
            leds.set_speed(value)

    def _on_follow_team(self, on: bool):
        if not self._syncing:
            leds.set_follow_team(on)

    def _on_follow_mode(self, on: bool):
        if not self._syncing:
            leds.set_follow_mode(on)

    def _pick_swatch(self, hex_color: str | None):
        if hex_color is None:
            leds.set_follow_team(True)
        else:
            leds.set_color(hex_color)

    def _pick_custom(self):
        current = QColor(leds.color)
        chosen = QColorDialog.getColor(current, self, "Strip colour")
        if chosen.isValid():
            leds.set_color(chosen.name().upper())

    def _on_connection(self, connected: bool, message: str):
        self._dot.set_state(brand.STATUS_ONLINE if connected else brand.STATUS_PENDING)
        self._status_lbl.setText(message)
        for widget in (self._identify_btn, self._save_btn):
            widget.setEnabled(connected)

    def _on_error(self, message: str):
        self._dot.set_state(brand.STATUS_FAULT)
        self._status_lbl.setText(message)

    def _on_team_changed(self, team):
        color = team.primary_color
        # The toggles stay green — see ToggleSwitch. Only the controls whose
        # colour *is* the meaning follow the team.
        for btn in self._preset_buttons.values():
            btn.set_accent(color)
        for btn, hex_color in self._swatch_buttons:
            if hex_color is None:
                btn.set_accent(color)
        self._save_btn.set_accent(color)

    def _apply_lock(self, unlocked: bool):
        """Admin-only controls are hidden outright, not merely disabled."""
        self._advanced.setVisible(unlocked)
        self._locked_notice.setVisible(not unlocked)

    # ── Mirror service state ──────────────────────────────────────────────

    def _sync(self):
        """Re-read everything from the service. Guarded so it never loops."""
        self._syncing = True
        try:
            self._enable_toggle.setChecked(leds.enabled)
            self._enable_lbl.setText("Strips on" if leds.enabled else "Strips off")
            for slider, value_lbl, value in (
                (self._bright_slider, self._bright_value, leds.brightness),
                (self._speed_slider, self._speed_value, leds.speed),
            ):
                if slider.value() != value:
                    slider.setValue(value)
                value_lbl.setText(str(value))
            self._follow_team_toggle.setChecked(leds.follow_team)
            self._follow_mode_toggle.setChecked(leds.follow_mode)
            for key, btn in self._preset_buttons.items():
                btn.set_active(key == leds.preset_key)
        finally:
            self._syncing = False
