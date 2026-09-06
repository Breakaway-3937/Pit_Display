"""
LED service singleton — the app's single point of contact with the strips.

`leds` is a lazy proxy: safe to import at module level anywhere. Call
`init_leds()` once in main() after `init_config()`.

The service owns the *intent* (mode, colour, brightness, on/off) and mirrors it
to the controller whenever a link is up. State lives here rather than on the
link so a reconnect can replay it — plug the board back in mid-event and the
strips come back exactly as they were, with no operator action.

Usage anywhere:
    from app.leds import leds
    leds.connection_changed.connect(my_slot)
    leds.apply_preset("judges")
"""

from PyQt6.QtCore import QObject, pyqtSignal

from app.cad_assets import cad_assets
from app.config import config
from app.lazy_proxy import LazyProxy
from app.leds import effects, palette
from app.leds.link import make_link
from app.leds.protocol import (
    ALL_SEGMENTS, DeviceInfo, Mode, Op,
    hex_to_rgb, payload_brightness, payload_color, payload_mode, rgb_to_hex,
)


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
        self._send(Op.SET_BRIGHT, payload_brightness(value))
        self.state_changed.emit()

    def set_speed(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        if value == self._speed:
            return
        self._speed = value
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
        self._send(Op.SET_COLOR, payload_color(palette.snap(rgb), ALL_SEGMENTS))
        self.state_changed.emit()

    def set_mode(self, mode: Mode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
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
        self._link.send(Op.SET_BRIGHT, payload_brightness(self._brightness))
        self._link.send(Op.SET_COLOR, payload_color(self._wire_rgb(self._color),
                                                    ALL_SEGMENTS))
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
                self._send(Op.SET_COLOR,
                           payload_color(palette.snap(rgb), ALL_SEGMENTS))
            return


leds: _LEDService = LazyProxy("leds", "init_leds")  # type: ignore[assignment]


def init_leds() -> _LEDService:
    """Call once in main(), after init_config() and init_cad_assets()."""
    real = _LEDService()
    leds._install(real)
    real.start()
    return real
