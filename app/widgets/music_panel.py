"""
Music control panel for the control screen.

Four blocks: engine status, transport + volume, library/queue, and the
ten-band equaliser. Like the LED panel, it mirrors the service rather than
holding state, so a mode change that ducks the audio or swaps the EQ shows up
here without the operator touching anything.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from app import brand
from app.admin import admin
from app.config import config
from app.music import music
from app.music import eq as eq_module
from app.widgets.eq_field import EQField
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, SelectableChip, eyebrow, mono_font,
)
from app.widgets.helpers import clear_layout, divider, label
from app.widgets.toggle_switch import ToggleSwitch


def _fmt_time(seconds: float) -> str:
    if seconds <= 0:
        return "0:00"
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


class MusicPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._syncing = False
        self._build()

        music.now_playing_changed.connect(self._on_now_playing)
        music.state_changed.connect(self._sync_transport)
        music.queue_changed.connect(self._rebuild_queue)
        music.library_changed.connect(self._rebuild_library)
        music.eq_changed.connect(self._sync_eq)
        music.error.connect(self._on_error)
        config.team_changed.connect(self._on_team_changed)
        admin.lock_state_changed.connect(self._apply_lock)
        self._apply_lock(admin.unlocked)

        self._rebuild_library()
        self._rebuild_queue()
        self._sync_eq()
        self._sync_transport()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Engine status — only shown when there is something to say ---------
        self._engine_card = RoundedFrame(fill=brand.CARBON_SURF2,
                                         border=brand.CARBON_LINE,
                                         radius=brand.R_BTN)
        ec = QVBoxLayout(self._engine_card)
        ec.setContentsMargins(14, 11, 14, 11)
        self._engine_lbl = label("", "stat_label")
        self._engine_lbl.setWordWrap(True)
        ec.addWidget(self._engine_lbl)
        root.addWidget(self._engine_card)
        if music.engine_available:
            self._engine_card.setVisible(False)
        else:
            self._engine_lbl.setText(music.engine_note)
        root.addSpacing(14)

        # Now playing --------------------------------------------------------
        root.addWidget(eyebrow("Now playing"))
        root.addSpacing(8)
        self._title_lbl = label("Nothing playing", "screen_title")
        self._title_lbl.setWordWrap(True)
        root.addWidget(self._title_lbl)
        self._artist_lbl = label("", "stat_label")
        root.addWidget(self._artist_lbl)
        root.addSpacing(10)

        pos_row = QHBoxLayout()
        pos_row.setContentsMargins(0, 0, 0, 0)
        self._pos_lbl = label("0:00", "stat_label")
        self._pos_lbl.setFont(mono_font())
        pos_row.addWidget(self._pos_lbl)
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 1000)
        self._seek.sliderReleased.connect(self._on_seek_released)
        pos_row.addWidget(self._seek, stretch=1)
        self._dur_lbl = label("0:00", "stat_label")
        self._dur_lbl.setFont(mono_font())
        pos_row.addWidget(self._dur_lbl)
        root.addLayout(pos_row)
        root.addSpacing(10)

        transport = QHBoxLayout()
        transport.setSpacing(8)
        self._prev_btn = RoundedButton("◀◀", variant="secondary")
        self._play_btn = RoundedButton("▶ Play", variant="primary")
        self._next_btn = RoundedButton("▶▶", variant="secondary")
        self._stop_btn = RoundedButton("■", variant="ghost")
        self._prev_btn.clicked.connect(music.previous_track)
        self._play_btn.clicked.connect(music.toggle_play)
        self._next_btn.clicked.connect(music.next_track)
        self._stop_btn.clicked.connect(music.stop)
        for b in (self._prev_btn, self._play_btn, self._next_btn, self._stop_btn):
            transport.addWidget(b)
        root.addLayout(transport)
        root.addSpacing(14)

        # Volume + duck ------------------------------------------------------
        self._vol_slider, vol_box, self._vol_value = self._slider_row(
            "Volume", 0, 100, music.volume, self._on_volume)
        root.addWidget(vol_box)
        self._cap_slider, cap_box, self._cap_value = self._slider_row(
            "Volume cap", 0, 100, music.volume_cap, self._on_cap)
        root.addWidget(cap_box)
        root.addWidget(label(
            "The cap is the real ceiling — the volume slider cannot exceed it.",
            "stat_label"))
        root.addSpacing(10)

        self._duck_btn = RoundedButton("Duck for a visitor", variant="secondary",
                                       accent=config.active_team.primary_color)
        self._duck_btn.setToolTip(
            "Drop to 20% instantly. Judges mode does this on its own."
        )
        self._duck_btn.clicked.connect(music.toggle_duck)
        root.addWidget(self._duck_btn)
        root.addSpacing(10)

        opts = QHBoxLayout()
        opts.setSpacing(8)
        self._repeat_btn = RoundedButton("Repeat", variant="ghost")
        self._repeat_btn.clicked.connect(lambda: music.set_repeat(not music.repeat))
        self._shuffle_btn = RoundedButton("Shuffle", variant="ghost")
        self._shuffle_btn.clicked.connect(lambda: music.set_shuffle(not music.shuffle))
        opts.addWidget(self._repeat_btn)
        opts.addWidget(self._shuffle_btn)
        root.addLayout(opts)
        root.addSpacing(12)
        root.addWidget(divider())
        root.addSpacing(14)

        # Library -------------------------------------------------------------
        root.addWidget(eyebrow("Library"))
        root.addSpacing(8)

        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        scan_btn = RoundedButton("Add folder…", variant="secondary",
                                 accent=config.active_team.primary_color)
        scan_btn.clicked.connect(self._on_scan)
        scan_row.addWidget(scan_btn)
        self._count_lbl = label("", "stat_label")
        scan_row.addWidget(self._count_lbl)
        scan_row.addStretch()
        root.addLayout(scan_row)
        root.addSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search title, artist or album…")
        self._search.textChanged.connect(lambda _t: self._rebuild_library())
        root.addWidget(self._search)
        root.addSpacing(8)

        self._library_list = QListWidget()
        self._library_list.setMinimumHeight(150)
        self._library_list.itemDoubleClicked.connect(self._on_library_activated)
        root.addWidget(self._library_list)
        root.addSpacing(8)

        lib_actions = QHBoxLayout()
        lib_actions.setSpacing(8)
        play_all = RoundedButton("Play all", variant="secondary",
                                 accent=config.active_team.primary_color)
        play_all.clicked.connect(self._on_play_all)
        enqueue = RoundedButton("Add to queue", variant="ghost")
        enqueue.clicked.connect(self._on_enqueue)
        lib_actions.addWidget(play_all)
        lib_actions.addWidget(enqueue)
        root.addLayout(lib_actions)
        root.addSpacing(10)

        root.addSpacing(14)
        root.addWidget(divider())
        root.addSpacing(14)

        # Queue ----------------------------------------------------------------
        queue_head = QHBoxLayout()
        queue_head.setContentsMargins(0, 0, 0, 0)
        queue_head.addWidget(eyebrow("Queue"))
        queue_head.addStretch()
        clear_btn = RoundedButton("Clear", variant="ghost")
        clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(music.clear_queue)
        queue_head.addWidget(clear_btn)
        root.addLayout(queue_head)
        root.addSpacing(8)
        self._queue_list = QListWidget()
        self._queue_list.setMinimumHeight(110)
        self._queue_list.itemDoubleClicked.connect(self._on_queue_activated)
        root.addWidget(self._queue_list)
        root.addSpacing(14)
        root.addWidget(divider())
        root.addSpacing(14)

        # ── Equaliser (admin-only) ───────────────────────────────────────
        # Hidden rather than disabled: a pit that has been tuned once should
        # not invite re-tuning by whoever wanders past the control screen.
        self._eq_locked_notice = label(
            "The equaliser is admin-only. Open the admin panel from the "
            "Breakaway mark in the top-left to unlock.",
            "stat_label")
        self._eq_locked_notice.setWordWrap(True)
        root.addWidget(self._eq_locked_notice)

        self._eq_section = QWidget()
        root.addWidget(self._eq_section)
        root = QVBoxLayout(self._eq_section)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(16)
        head.addWidget(eyebrow("Equaliser · 10 band"))
        head.addStretch(1)
        self._eq_desc = label("", "stat_label")
        self._eq_desc.setWordWrap(True)
        self._eq_desc.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Preferred)
        self._eq_desc.setMinimumWidth(0)
        head.addWidget(self._eq_desc, stretch=2)
        root.addLayout(head)
        root.addSpacing(12)

        # Presets as chips, not a combo: there are five, they are the thing an
        # operator actually reaches for, and a dropdown hides four of them
        # behind a click during a match cycle.
        self._preset_row = QHBoxLayout()
        self._preset_row.setSpacing(10)
        self._preset_row.setContentsMargins(0, 0, 0, 0)
        self._preset_buttons: dict[str, SelectableChip] = {}
        root.addLayout(self._preset_row)
        root.addSpacing(12)

        # ── The instrument ───────────────────────────────────────────────
        eq_card = RoundedFrame(fill="#1A171A", border=brand.CARBON_LINE,
                               radius=brand.R_CARD)
        card = QVBoxLayout(eq_card)
        card.setContentsMargins(20, 18, 20, 18)
        card.setSpacing(10)

        card_head = QHBoxLayout()
        card_head.setSpacing(16)
        gain_lbl = label("GAIN dB")
        gain_lbl.setFont(mono_font(11))
        gain_lbl.setStyleSheet(f"color: {brand.GRAPHITE}; background: transparent;")
        card_head.addWidget(gain_lbl)
        hair = QFrame()
        hair.setFixedHeight(1)
        hair.setStyleSheet(f"background: {brand.RAISED_DARK}; border: none;")
        hair.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card_head.addWidget(hair, stretch=1)
        preamp_lbl = label("PREAMP")
        preamp_lbl.setFont(mono_font(11))
        preamp_lbl.setStyleSheet(f"color: {brand.GRAPHITE}; background: transparent;")
        card_head.addWidget(preamp_lbl)

        # The control sits with its readout rather than in a row of its own:
        # two "Preamp −2" labels 250px apart is the same value said twice.
        self._preamp_slider = QSlider(Qt.Orientation.Horizontal)
        self._preamp_slider.setRange(int(eq_module.GAIN_MIN),
                                     int(eq_module.GAIN_MAX))
        self._preamp_slider.setFixedWidth(160)
        self._preamp_slider.valueChanged.connect(self._on_preamp)
        card_head.addWidget(self._preamp_slider)

        self._preamp_value = label("0.0")
        self._preamp_value.setStyleSheet(
            f'color: {brand.WHITE}; background: transparent;'
            f' font-family: "{brand.FONT_DISPLAY}"; font-size: 15px;'
            f' font-weight: 600;')
        card_head.addWidget(self._preamp_value)

        # The live band display, in the card header beside the preamp — it is
        # a property of this instrument, not a setting somewhere else. Off by
        # default: it costs a second decode of the playing file, and most of
        # the time an operator is setting a curve rather than watching one.
        self._analyser_chip = SelectableChip("ANALYSER")
        self._analyser_chip.setCheckable(True)
        self._analyser_chip.setChecked(False)
        self._analyser_chip.clicked.connect(self._on_analyser_toggled)
        card_head.addSpacing(10)
        card_head.addWidget(self._analyser_chip)
        card.addLayout(card_head)

        self._eq_field = EQField()
        self._eq_field.band_changed.connect(self._on_band)
        card.addWidget(self._eq_field, stretch=1)
        root.addWidget(eq_card)
        root.addSpacing(12)

        eq_actions = QHBoxLayout()
        eq_actions.setSpacing(8)
        save_eq = RoundedButton("Save preset…", variant="secondary",
                                accent=config.active_team.primary_color)
        save_eq.clicked.connect(self._on_save_eq)
        eq_actions.addWidget(save_eq)
        # Only while a preset the team saved is selected; built-ins can't go.
        self._delete_eq = RoundedButton("Delete preset", variant="secondary",
                                        accent=config.active_team.primary_color)
        self._delete_eq.clicked.connect(self._on_delete_eq)
        self._delete_eq.setVisible(False)
        eq_actions.addWidget(self._delete_eq)
        eq_actions.addStretch(1)
        root.addLayout(eq_actions)
        root.addSpacing(10)

        self._follow_toggle, follow_row = self._toggle_row(
            "Follow display mode", music.follow_mode, self._on_follow_mode)
        root.addWidget(follow_row)
        root.addWidget(label(
            "Judges mode ducks the audio and switches to the speech-clear curve.",
            "stat_label"))
        root.addSpacing(14)

        tip = RoundedFrame(fill=brand.CARBON_SURF2, border=brand.CARBON_LINE,
                           radius=brand.R_BTN)
        tl = QVBoxLayout(tip)
        tl.setContentsMargins(14, 12, 14, 12)
        tl.setSpacing(4)
        tl.addWidget(eyebrow("Tuning the pit"))
        note = label(
            "Aim the speakers down into the pit before touching a slider — it "
            "beats any curve here. Then high-pass 100–120 Hz to kill the boom, "
            "cut 200–400 Hz for mud, and lift 2–4 kHz so people can still talk "
            "over it.",
            "stat_label")
        note.setWordWrap(True)
        tl.addWidget(note)
        root.addWidget(tip)

    # ── Small builders ────────────────────────────────────────────────────

    def _slider_row(self, title, lo, hi, value, handler):
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
        slider.valueChanged.connect(handler)
        v.addWidget(slider)
        return slider, box, value_lbl

    def _toggle_row(self, text, checked, handler):
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

    # ── Library / queue ───────────────────────────────────────────────────

    def _rebuild_library(self):
        self._library_list.clear()
        tracks = music.tracks(self._search.text())
        for track in tracks:
            item = QListWidgetItem(f"{track.label()}   {track.duration_text()}")
            item.setData(Qt.ItemDataRole.UserRole, track)
            self._library_list.addItem(item)
        total = len(tracks)
        self._count_lbl.setText(
            "No tracks yet — add a folder" if total == 0
            else f"{total} track{'s' if total != 1 else ''}"
        )

    def _rebuild_queue(self):
        self._queue_list.clear()
        for i, track in enumerate(music.queue):
            marker = "▶ " if i == music.queue_index else ""
            item = QListWidgetItem(f"{marker}{track.label()}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._queue_list.addItem(item)

    def _selected_tracks(self):
        return [i.data(Qt.ItemDataRole.UserRole)
                for i in self._library_list.selectedItems()]

    def _on_library_activated(self, item):
        # Double-clicking a row queues the whole visible list and starts there,
        # so the rest of the library keeps playing after this track ends.
        music.set_queue(music.tracks(self._search.text()),
                        start=self._library_list.row(item))

    def _on_queue_activated(self, item):
        music.play_queue_at(item.data(Qt.ItemDataRole.UserRole))

    def _on_play_all(self):
        tracks = music.tracks(self._search.text())
        if tracks:
            music.set_queue(tracks, start=0)

    def _on_enqueue(self):
        for track in self._selected_tracks():
            music.enqueue(track)

    def _on_scan(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a music folder")
        if not folder:
            return
        found, added = music.scan_folder(Path(folder))
        self._count_lbl.setText(f"Scanned {found} file(s), {added} new")

    # ── Transport ─────────────────────────────────────────────────────────

    def _on_volume(self, value: int):
        self._vol_value.setText(str(value))
        if not self._syncing:
            music.set_volume(value)

    def _on_cap(self, value: int):
        self._cap_value.setText(str(value))
        if not self._syncing:
            music.set_volume_cap(value)

    def _on_seek_released(self):
        duration = music.duration()
        if duration > 0:
            music.seek(duration * self._seek.value() / 1000.0)

    def _on_now_playing(self, track):
        if track is None:
            self._title_lbl.setText("Nothing playing")
            self._artist_lbl.setText("")
        else:
            self._title_lbl.setText(track.title)
            self._artist_lbl.setText(track.artist or track.album or "")
        self._rebuild_queue()

    def _sync_transport(self):
        self._syncing = True
        try:
            playing = music.playing
            self._play_btn.setText("❚❚ Pause" if playing else "▶ Play")
            self._duck_btn.setText(
                "Ducked — restore" if music.ducked else "Duck for a visitor")
            self._duck_btn.set_variant("primary" if music.ducked else "secondary")
            self._repeat_btn.set_active(music.repeat)
            self._shuffle_btn.set_active(music.shuffle)

            if self._vol_slider.value() != music.volume:
                self._vol_slider.setValue(music.volume)
            if self._cap_slider.value() != music.volume_cap:
                self._cap_slider.setValue(music.volume_cap)

            pos, dur = music.position(), music.duration()
            self._pos_lbl.setText(_fmt_time(pos))
            self._dur_lbl.setText(_fmt_time(dur))
            if dur > 0 and not self._seek.isSliderDown():
                self._seek.setValue(int(1000 * pos / dur))
        finally:
            self._syncing = False

    # ── EQ ────────────────────────────────────────────────────────────────

    def _rebuild_presets(self, names: list[str]):
        """The preset chips. Active is white — red is reserved on this panel."""
        clear_layout(self._preset_row)
        self._preset_buttons.clear()
        for name in names:
            btn = SelectableChip(name.upper())
            btn.setMinimumHeight(44)
            f = btn.font()
            f.setPixelSize(13)
            btn.setFont(f)
            btn.clicked.connect(lambda _c=False, n=name: self._on_eq_preset(n))
            self._preset_row.addWidget(btn)
            self._preset_buttons[name] = btn
        self._preset_row.addStretch(1)

    def _on_eq_preset(self, name: str):
        if not self._syncing:
            music.apply_eq_preset(name)

    def _on_band(self, index: int, value: float):
        if not self._syncing:
            music.set_band(index, float(value))

    def _on_preamp(self, value: int):
        self._preamp_value.setText(f"{value:+.1f}" if value else "0.0")
        if not self._syncing:
            music.set_preamp(float(value))

    def _on_save_eq(self):
        # Start from the selected preset's name, so saving again overwrites it.
        current = music.eq_preset
        start = "" if current in eq_module.BUILT_IN_NAMES else current
        name, ok = QInputDialog.getText(self, "Save EQ preset", "Preset name:",
                                        QLineEdit.EchoMode.Normal, start)
        name = name.strip()
        if not ok or not name:
            return
        existing = eq_module.find_user_preset(name)
        if existing is not None:
            answer = QMessageBox.question(
                self, "Overwrite preset?",
                f"“{existing}” already exists. Replace it with the current curve?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        music.save_eq_as(name)

    def _on_delete_eq(self):
        name = music.eq_preset
        if not name or name in eq_module.BUILT_IN_NAMES:
            return
        answer = QMessageBox.question(
            self, "Delete preset?",
            f"Delete the EQ preset “{name}”? The music keeps its current sound.")
        if answer == QMessageBox.StandardButton.Yes:
            music.delete_eq_preset(name)

    def _on_follow_mode(self, on: bool):
        if not self._syncing:
            music.set_follow_mode(on)

    def _sync_eq(self):
        self._syncing = True
        try:
            presets = eq_module.all_presets()
            names = [p.name for p in presets]
            if list(self._preset_buttons) != names:
                self._rebuild_presets(names)
            for name, btn in self._preset_buttons.items():
                btn.set_active(name == music.eq_preset)
            match = next((p for p in presets if p.name == music.eq_preset), None)
            self._eq_desc.setText(match.description if match else "")
            self._delete_eq.setVisible(match is not None and not match.built_in)

            self._eq_field.set_gains(music.eq_gains)
            preamp = int(round(music.eq_preamp))
            self._preamp_slider.setValue(preamp)
            self._preamp_value.setText(f"{preamp:+.1f}" if preamp else "0.0")
            self._follow_toggle.setChecked(music.follow_mode)
        finally:
            self._syncing = False

    def _on_analyser_toggled(self):
        """
        Show or hide the live band levels behind the curve.

        Nothing here can stop playback: the service starts a separate,
        output-less decoder and hands this widget the analyser it feeds. If
        that decoder will not start, `analyser()` stays None and the field
        draws exactly what it always drew.
        """
        on = self._analyser_chip.isChecked()
        analyser = music.set_analyser_enabled(on)
        self._eq_field.set_analyser(analyser if on else None)

    def _apply_lock(self, unlocked: bool):
        self._eq_section.setVisible(unlocked)
        self._eq_locked_notice.setVisible(not unlocked)

    # ── Misc ──────────────────────────────────────────────────────────────

    def _on_error(self, message: str):
        self._engine_card.setVisible(True)
        self._engine_lbl.setText(message)

    def _on_team_changed(self, team):
        color = team.primary_color
        # The follow toggle stays green — see ToggleSwitch.
        self._duck_btn.set_accent(color)
