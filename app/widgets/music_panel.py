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
    QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QSlider, QVBoxLayout, QWidget,
)

from app import brand
from app.admin import admin
from app.config import config
from app.music import music
from app.music import eq as eq_module
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, eyebrow, mono_font,
)
from app.widgets.helpers import divider, label
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
        self._band_sliders: list[QSlider] = []
        self._band_values: list[QLabel] = []
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

        root.addWidget(eyebrow("Equaliser"))
        root.addSpacing(8)

        self._eq_combo = QComboBox()
        self._eq_combo.currentIndexChanged.connect(self._on_eq_preset)
        root.addWidget(self._eq_combo)
        self._eq_desc = label("", "stat_label")
        self._eq_desc.setWordWrap(True)
        root.addSpacing(6)
        root.addWidget(self._eq_desc)
        root.addSpacing(12)

        bands = QGridLayout()
        bands.setSpacing(4)
        for i, band_label in enumerate(eq_module.BAND_LABELS):
            value_lbl = label("0", "stat_label", Qt.AlignmentFlag.AlignHCenter)
            value_lbl.setFont(mono_font())
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(int(eq_module.GAIN_MIN), int(eq_module.GAIN_MAX))
            slider.setValue(0)
            slider.setMinimumHeight(96)
            slider.valueChanged.connect(
                lambda v, idx=i: self._on_band(idx, v))
            name_lbl = label(band_label, "stat_label", Qt.AlignmentFlag.AlignHCenter)
            bands.addWidget(value_lbl, 0, i)
            bands.addWidget(slider, 1, i, Qt.AlignmentFlag.AlignHCenter)
            bands.addWidget(name_lbl, 2, i)
            self._band_sliders.append(slider)
            self._band_values.append(value_lbl)
        root.addLayout(bands)
        root.addSpacing(6)
        root.addWidget(label("Gain in dB per band · Hz", "stat_label",
                             Qt.AlignmentFlag.AlignHCenter))
        root.addSpacing(12)

        self._preamp_slider, preamp_box, self._preamp_value = self._slider_row(
            "Preamp", int(eq_module.GAIN_MIN), int(eq_module.GAIN_MAX),
            0, self._on_preamp)
        root.addWidget(preamp_box)
        root.addSpacing(10)

        eq_actions = QHBoxLayout()
        eq_actions.setSpacing(8)
        save_eq = RoundedButton("Save preset…", variant="secondary",
                                accent=config.active_team.primary_color)
        save_eq.clicked.connect(self._on_save_eq)
        reset_eq = RoundedButton("Flat", variant="ghost")
        reset_eq.clicked.connect(lambda: music.apply_eq_preset("Flat"))
        eq_actions.addWidget(save_eq)
        eq_actions.addWidget(reset_eq)
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
        toggle = ToggleSwitch(color_on=config.active_team.primary_color)
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

    def _on_eq_preset(self, index: int):
        if self._syncing or index < 0:
            return
        music.apply_eq_preset(self._eq_combo.itemText(index))

    def _on_band(self, index: int, value: int):
        self._band_values[index].setText(str(value))
        if not self._syncing:
            music.set_band(index, float(value))

    def _on_preamp(self, value: int):
        self._preamp_value.setText(str(value))
        if not self._syncing:
            music.set_preamp(float(value))

    def _on_save_eq(self):
        name, ok = QInputDialog.getText(self, "Save EQ preset", "Preset name:")
        if ok and name.strip():
            music.save_eq_as(name.strip())

    def _on_follow_mode(self, on: bool):
        if not self._syncing:
            music.set_follow_mode(on)

    def _sync_eq(self):
        self._syncing = True
        try:
            presets = eq_module.all_presets()
            names = [p.name for p in presets]
            if [self._eq_combo.itemText(i) for i in range(self._eq_combo.count())] != names:
                self._eq_combo.clear()
                self._eq_combo.addItems(names)
            if music.eq_preset in names:
                self._eq_combo.setCurrentIndex(names.index(music.eq_preset))
            match = next((p for p in presets if p.name == music.eq_preset), None)
            self._eq_desc.setText(match.description if match else "")

            for i, gain in enumerate(music.eq_gains):
                self._band_sliders[i].setValue(int(round(gain)))
                self._band_values[i].setText(str(int(round(gain))))
            self._preamp_slider.setValue(int(round(music.eq_preamp)))
            self._preamp_value.setText(str(int(round(music.eq_preamp))))
            self._follow_toggle.setChecked(music.follow_mode)
        finally:
            self._syncing = False

    def _apply_lock(self, unlocked: bool):
        self._eq_section.setVisible(unlocked)
        self._eq_locked_notice.setVisible(not unlocked)

    # ── Misc ──────────────────────────────────────────────────────────────

    def _on_error(self, message: str):
        self._engine_card.setVisible(True)
        self._engine_lbl.setText(message)

    def _on_team_changed(self, team):
        color = team.primary_color
        for toggle in (self._follow_toggle,):
            toggle.set_color_on(color)
        self._duck_btn.set_accent(color)
