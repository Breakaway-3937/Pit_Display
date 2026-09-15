"""
LED service singleton — the app's single point of contact with the strips.

`leds` is a lazy proxy: safe to import at module level anywhere. Call
`init_leds()` once in main() after `init_config()`.

The service owns the *intent* (mode, colour, brightness, on/off) and mirrors it
to the controller whenever a link is up. State lives here rather than on the
link so a reconnect can replay it — plug the board back in mid-event and the
strips come back exactly as they were, with no operator action.

**An alert is an overlay, not a change of intent.** `start_alert()` puts the
strips into a per-segment look — the sides in one colour, the centre in
another or on the white die — flashes it for a few seconds and then either
holds it or lets go; `clear_alert()` puts the resting look back exactly as it
was. The resting intent is never touched, so a queue alert that lands in the
middle of judges mode ends with judges mode, and a reconnect during an alert
replays the alert rather than the look underneath it.

**The flash is host-driven.** The firmware's ALERT mode flashes every segment,
and the queue alert wants the centre run steady while the sides flash — so
the app holds the controller in SOLID and toggles the sides' colour itself,
three times a second. A SET_COLOR is four bytes and a redraw is one show;
the serial trouble this subsystem has had was frames at 60 Hz, not at 3.

Usage anywhere:
    from app.leds import leds
    leds.connection_changed.connect(my_slot)
    leds.apply_preset("judges")
"""

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.cad_assets import cad_assets
from app.config import config
from app.lazy_proxy import LazyProxy
from app.leds import effects, palette
from app.leds.link import make_link
from app.leds.protocol import (
    ALL_SEGMENTS, SEG_CENTER, SEG_SIDES, DeviceInfo, Mode, Op,
    hex_to_rgb, payload_brightness, payload_color, payload_mode, rgb_to_hex,
)

# The alert flash: 1.5 Hz, so a toggle every 333 ms. Gentle on the serial
# link — anything much faster and the firmware's show() blackouts start
# eating the very frames that drive it.
ALERT_FLASH_MS = 333
ALERT_BRIGHTNESS = 200        # the firmware's MAX_BRIGHTNESS; an alert is the
                              # one moment full output is the point

# Firmware version that understands SET_COLOR's fifth byte, the white die.
_FW_WHITE = (2, 2)


@dataclass(frozen=True)
class Alert:
    """
    A per-segment look the strips hold instead of the resting intent.

    `sides` / `centre` are brand hexes or None (leave that segment dark);
    `centre_white` puts the centre run on the white die instead — the only
    real white these strips have. `flash_s` is how long the sides flash, then
    the look sits lit for `steady_s`; after that `hold` keeps it up until
    `clear_alert()`, and False lets the resting look back by itself.
    """
    sides: str | None
    centre: str | None = None
    centre_white: bool = False
    flash_s: float = 2.0
    steady_s: float = 1.0
    hold: bool = False
    flash_centre: bool = False
    label: str = ""


class _LEDService(QObject):

    # (connected, human status text)
    connection_changed = pyqtSignal(bool, str)

    # Any of mode / colour / brightness / enabled changed — panels re-read
    state_changed = pyqtSignal()

    # Something the operator should see
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._enabled = True
        self._preset_key = "team_solid"
        self._mode = Mode.SOLID
        self._speed = 128
        self._brightness = 180
        self._color = config.active_team.primary_color
        self._follow_team = True
        self._follow_mode = True
        self._device: DeviceInfo | None = None
        self._status = "Starting…"

        self._alert: Alert | None = None
        self._alert_on = False           # which half of the flash we are in
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(ALERT_FLASH_MS)
        self._flash_timer.timeout.connect(self._flash_tick)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._settle)
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(self.clear_alert)

        self._link = make_link(self)
        self._link.connected.connect(self._on_connected)
        self._link.disconnected.connect(self._on_disconnected)
        self._link.status.connect(self._on_status)
        self._link.link_error.connect(self.error)

        config.team_changed.connect(self._on_team_changed)
        config.mode_changed.connect(self._on_mode_changed)
        cad_assets.subsystem_focused.connect(self._on_subsystem_focused)

    @staticmethod
    def _wire_rgb(hex_color: str) -> tuple[int, int, int]:
        """
        Brand hex → the bytes actually sent to the strips.

        Snapped to a saturated primary: RGBW pixels render a mixed brand hex
        washed out (the team red came out pink), and a pit strip's job is to
        read as the team colour across a venue, not to match a swatch. See
        app/leds/palette.py for the measurements behind that.
        """
        return palette.snap(hex_to_rgb(hex_color))

    def start(self) -> None:
        self._link.start()

    def shutdown(self) -> None:
        # Leave the strips in a known state rather than frozen on whatever was
        # last showing, then let the firmware watchdog take it from there.
        if self._device is not None:
            self._link.send(Op.OFF)
        self._link.stop()

    # ── Read-only state ───────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._device is not None

    @property
    def device(self) -> DeviceInfo | None:
        return self._device

    @property
    def status(self) -> str:
        return self._status

    @property
    def port_name(self) -> str:
        return self._link.port_name

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def preset_key(self) -> str:
        return self._preset_key

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def speed(self) -> int:
        return self._speed

    @property
    def brightness(self) -> int:
        return self._brightness

    @property
    def color(self) -> str:
        return self._color

    @property
    def follow_team(self) -> bool:
        return self._follow_team

    @property
    def follow_mode(self) -> bool:
        return self._follow_mode

    @property
    def alert(self) -> Alert | None:
        return self._alert

    @property
    def supports_white(self) -> bool:
        """Does the connected firmware drive the white die per segment?"""
        d = self._device
        return d is not None and (d.fw_major, d.fw_minor) >= _FW_WHITE

    # ── Commands ──────────────────────────────────────────────────────────

    def set_enabled(self, on: bool) -> None:
        """The kill switch. Venue staff will eventually ask for this."""
        if on == self._enabled:
            return
        self._enabled = on
        if on:
            self._push_all()
        else:
            self._send(Op.OFF)
        self.state_changed.emit()

    def set_brightness(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        if value == self._brightness:
            return
        self._brightness = value
        if self._alert is None:
            self._send(Op.SET_BRIGHT, payload_brightness(value))
        self.state_changed.emit()

    def set_speed(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        if value == self._speed:
            return
        self._speed = value
        if self._alert is None:
            self._send(Op.SET_MODE, payload_mode(self._mode, value))
        self.state_changed.emit()

    def set_color(self, hex_color: str, *, manual: bool = True) -> None:
        try:
            rgb = hex_to_rgb(hex_color)
        except ValueError:
            self.error.emit(f"Not a colour: {hex_color}")
            return
        normalized = rgb_to_hex(rgb)
        if manual:
            # An explicit colour pick means the operator has taken over from
            # the team selector; stop overwriting their choice.
            self._follow_team = False
        if normalized == self._color:
            return
        # self._color keeps the true brand hex — the control panel and every
        # screen still show the real colour. Only the wire gets the snap.
        self._color = normalized
        if self._alert is None:
            self._send(Op.SET_COLOR, payload_color(palette.snap(rgb), ALL_SEGMENTS, 0))
        self.state_changed.emit()

    def set_mode(self, mode: Mode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        if self._alert is None:
            self._send(Op.SET_MODE, payload_mode(mode, self._speed))
        self.state_changed.emit()

    def apply_preset(self, key: str, *, manual: bool = True) -> None:
        preset = effects.get(key)
        if preset is None:
            self.error.emit(f"Unknown preset: {key}")
            return
        if manual:
            self._follow_mode = False
        self._preset_key = key
        self._mode = preset.mode
        self._speed = preset.speed
        self._brightness = preset.brightness
        self._color = preset.color or (
            config.active_team.primary_color if self._follow_team else self._color
        )
        self._push_all()
        self.state_changed.emit()

    def set_follow_team(self, on: bool) -> None:
        self._follow_team = on
        if on:
            self.set_color(config.active_team.primary_color, manual=False)
        self.state_changed.emit()

    def set_follow_mode(self, on: bool) -> None:
        self._follow_mode = on
        if on:
            self.apply_preset(effects.MODE_PRESETS.get(config.mode, "team_solid"),
                              manual=False)
        self.state_changed.emit()

    def save_as_default(self) -> None:
        """Persist the current look as the strip's boot / watchdog fallback."""
        self._send(Op.SAVE)

    def identify(self) -> None:
        """Flash the strip so the operator can confirm which board answered."""
        self._send(Op.SET_MODE, payload_mode(Mode.ALERT, 220))
        self._send(Op.SET_MODE, payload_mode(self._mode, self._speed))

    # ── Alerts ────────────────────────────────────────────────────────────

    def start_alert(self, alert: Alert) -> None:
        """Put the strips into `alert`: flash, then hold or let go."""
        self._release_timer.stop()
        self._alert = alert
        self._alert_on = True
        self._push_alert(flash_phase=True)
        self._flash_timer.start()
        self._settle_timer.start(int(max(0.0, alert.flash_s) * 1000))
        self.state_changed.emit()

    def clear_alert(self) -> None:
        """Back to the resting look, exactly as it was."""
        if self._alert is None:
            return
        self._flash_timer.stop()
        self._settle_timer.stop()
        self._release_timer.stop()
        self._alert = None
        self._push_all()
        self.state_changed.emit()

    def _segment_payloads(self, alert: Alert, sides_lit: bool,
                          centre_lit: bool) -> list[bytes]:
        """The two SET_COLOR payloads for one frame of the alert."""
        black = (0, 0, 0)
        sides = (self._wire_rgb(alert.sides) if alert.sides and sides_lit
                 else black)
        if alert.centre_white:
            centre_rgb, white = black, (255 if centre_lit else 0)
        else:
            centre_rgb = (self._wire_rgb(alert.centre)
                          if alert.centre and centre_lit else black)
            white = 0
        return [payload_color(sides, SEG_SIDES, 0),
                payload_color(centre_rgb, SEG_CENTER, white)]

    def _push_alert(self, flash_phase: bool) -> None:
        """Replay the alert in full: brightness, both colours, SOLID."""
        if self._device is None or not self._enabled or self._alert is None:
            return
        a = self._alert
        lit = self._alert_on or not flash_phase
        centre_lit = lit if a.flash_centre else True
        self._link.send(Op.SET_BRIGHT, payload_brightness(ALERT_BRIGHTNESS))
        for payload in self._segment_payloads(a, lit, centre_lit):
            self._link.send(Op.SET_COLOR, payload)
        self._link.send(Op.SET_MODE, payload_mode(Mode.SOLID, self._speed))

    def _flash_tick(self) -> None:
        if self._alert is None or self._device is None or not self._enabled:
            return
        self._alert_on = not self._alert_on
        a = self._alert
        centre_lit = self._alert_on if a.flash_centre else True
        payloads = self._segment_payloads(a, self._alert_on, centre_lit)
        # A steady centre was set when the alert started; only resend what moves.
        for payload in (payloads if a.flash_centre else payloads[:1]):
            self._link.send(Op.SET_COLOR, payload)

    def _settle(self) -> None:
        """The flash is over: sit lit for `steady_s`, then hold or let go."""
        self._flash_timer.stop()
        a = self._alert
        if a is None:
            return
        if not a.hold and a.steady_s <= 0:
            self.clear_alert()
            return
        self._alert_on = True
        self._push_alert(flash_phase=False)
        if not a.hold:
            self._release_timer.start(int(a.steady_s * 1000))

    # ── Link plumbing ─────────────────────────────────────────────────────

    def _send(self, op: int, payload: bytes = b"") -> None:
        if self._device is None:
            return                      # queued state is replayed on reconnect
        if not self._enabled and op != Op.OFF:
            return
        self._link.send(op, payload)

    def _push_all(self) -> None:
        """Replay the full intent. Used on connect and whenever a preset lands."""
        if self._device is None or not self._enabled:
            return
        if self._alert is not None:
            # An alert outranks the resting look for as long as it is up; a
            # reconnect mid-alert comes back into the alert, not under it.
            self._push_alert(flash_phase=self._flash_timer.isActive())
            return
        self._link.send(Op.SET_BRIGHT, payload_brightness(self._brightness))
        # W explicitly 0: a resting colour never wants the white die, and a
        # previous alert may have left it lit on the centre run.
        self._link.send(Op.SET_COLOR, payload_color(self._wire_rgb(self._color),
                                                    ALL_SEGMENTS, 0))
        self._link.send(Op.SET_MODE, payload_mode(self._mode, self._speed))

    def _on_connected(self, info: DeviceInfo, port: str) -> None:
        self._device = info
        self._status = f"Connected on {port} — {info.describe()}"
        self._push_all()
        self.connection_changed.emit(True, self._status)
        self.state_changed.emit()

    def _on_disconnected(self, reason: str) -> None:
        self._device = None
        self._status = f"Disconnected — {reason}"
        self.connection_changed.emit(False, self._status)
        self.state_changed.emit()

    def _on_status(self, text: str) -> None:
        if self._device is None:
            self._status = text
            self.connection_changed.emit(False, text)

    # ── App integrations ──────────────────────────────────────────────────

    def _on_team_changed(self, team) -> None:
        if self._follow_team:
            self.set_color(team.primary_color, manual=False)

    def _on_mode_changed(self, mode: str) -> None:
        if self._follow_mode:
            self.apply_preset(effects.MODE_PRESETS.get(mode, "team_solid"),
                              manual=False)

    def _on_subsystem_focused(self, sub_id: str) -> None:
        """
        The CAD viewer flew to a subsystem — echo its accent on the strips.
        Only while the team colour is being followed automatically; if someone
        has hand-picked a colour, leave it alone.
        """
        if not sub_id or not self._follow_team:
            return
        for sub in cad_assets.load_config().get("subsystems", []):
            if sub.get("id") != sub_id:
                continue
            accent = sub.get("accent_color")
            if accent:
                try:
                    rgb = hex_to_rgb(accent)
                except ValueError:
                    return          # a malformed colour in the config is not
                                    # worth interrupting a judges demo over
                if self._alert is None:
                    self._send(Op.SET_COLOR,
                               payload_color(palette.snap(rgb), ALL_SEGMENTS, 0))
            return


leds: _LEDService = LazyProxy("leds", "init_leds")  # type: ignore[assignment]


def init_leds() -> _LEDService:
    """Call once in main(), after init_config() and init_cad_assets()."""
    real = _LEDService()
    leds._install(real)
    real.start()
    return real
