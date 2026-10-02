"""
Control → Pit Systems → Analysis. The local model's runs, and the crew's
verdict on them.

The control screen is the one surface with reliable input, so this is where
runs get rated. What an operator needs, in order:

* **Is it working?** The engine line: ready, analysing (and which step), or
  what's missing (Ollama not running, the model not downloaded).
* **Run it.** "Analyse the newest log", and the switch that does it on every
  import. The button is secondary: the panel's one red is a fault's dot.
* **Which model is earning its keep?** The scoreboard: per model and prompt
  version, the share of rated findings the crew called useful or wrong, how
  many they acted on, the mean rank, and how often a run passed the checks.
* **Was it any good?** Recent runs; the chosen run's findings, each rated
  **Useful / Not useful / Wrong** plus **Acted on**, and a **1–5 rank** for
  the run. Ratings are the reward signal (`app/ai/feedback.py`): a clean bill
  of health the crew trusted is as useful as a fault, so nothing here counts
  problems found.

Status colours are dots, never type (CLAUDE.md, "Theming"): a finding's dot
is its severity, so red appears only for a latched fault.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from app import attribution, brand
from app.attribution import link
from app.ai import feedback, runtime
from app.ai.service import analysis
from app.widgets.brand_widgets import RoundedButton, SelectableChip, StatusDot, eyebrow, mono_font
from app.widgets.helpers import LinkLabel, clear_layout, divider, label
from app.widgets.toggle_switch import ToggleSwitch

_SEVERITY_DOT = {"fault": brand.STATUS_FAULT, "warn": brand.STATUS_PENDING,
                 "ok": brand.STATUS_ONLINE, "idle": brand.STATUS_IDLE}
_RUN_DOT = {"published": brand.STATUS_ONLINE, "rejected": brand.STATUS_PENDING,
            "running": brand.STATUS_PENDING, "failed": brand.STATUS_IDLE}
_RATING_TEXT = (("useful", "Useful"), ("not_useful", "Not useful"), ("wrong", "Wrong"))
_TOUCH = 46


def _chip(text: str) -> SelectableChip:
    c = SelectableChip(text)
    c.setMinimumHeight(_TOUCH)
    return c


def _prose(text: str, obj: str = "stat_label") -> QWidget:
    lbl = label(text, obj)
    lbl.setWordWrap(True)
    return lbl


class AnalysisPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected: int | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── engine ────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Local model · robot-log analysis"))
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
        self._sub = _prose("")
        root.addWidget(self._sub)
        root.addSpacing(12)

        # Secondary: on this panel red is a latched fault's dot, and the
        # button is the exception (runs start on import by themselves).
        self._run_btn = RoundedButton("Analyse the newest log", variant="secondary")
        self._run_btn.setMinimumHeight(_TOUCH)
        self._run_btn.clicked.connect(self._run_now)
        self._dl_btn = RoundedButton("", variant="secondary")
        self._dl_btn.setMinimumHeight(_TOUCH)
        self._dl_btn.clicked.connect(self._download)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._run_btn)
        buttons.addWidget(self._dl_btn)
        buttons.addStretch()
        root.addLayout(buttons)
        root.addSpacing(12)

        auto = QHBoxLayout()
        auto.setSpacing(12)
        auto_text = label("Analyse each new log as it's imported", "stat_value")
        auto_text.setWordWrap(True)
        auto_text.setStyleSheet("font-size: 15px;")
        auto.addWidget(auto_text, stretch=1)
        self._auto = ToggleSwitch()
        self._auto.setChecked(analysis.prefs["auto_run"])
        self._auto.toggled.connect(self._set_auto)
        auto.addWidget(self._auto, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(auto)
        root.addSpacing(8)
        # A run can name the match a log was and its partners (`match_context`,
        # from home's TBA tables): the credit is a condition of that data's use.
        root.addWidget(LinkLabel(
            "Which match a log was, and who played in it: "
            f"{link(attribution.TBA_TEXT, attribution.TBA_URL)}."))
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── scoreboard ────────────────────────────────────────────────────
        root.addWidget(eyebrow("Scoreboard · what the crew found useful"))
        root.addSpacing(10)
        self._board_grid = QGridLayout()
        self._board_grid.setHorizontalSpacing(18)
        self._board_grid.setVerticalSpacing(8)
        root.addLayout(self._board_grid)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── runs ──────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Recent runs"))
        root.addSpacing(10)
        self._runs_box = QVBoxLayout()
        self._runs_box.setSpacing(6)
        root.addLayout(self._runs_box)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── the chosen run ────────────────────────────────────────────────
        self._detail = QVBoxLayout()
        self._detail.setSpacing(0)
        root.addLayout(self._detail)

        # Bound methods, never lambdas: the service outlives this panel.
        analysis.state_changed.connect(self._refresh_head)
        analysis.run_finished.connect(self._on_run_finished)
        analysis.runs_changed.connect(self._reload)
        self._refresh_head()
        self._reload()

    # ── engine line ───────────────────────────────────────────────────────

    def _refresh_head(self) -> None:
        busy, ready = analysis.busy, analysis.ready
        self._dot.set_color(brand.STATUS_PENDING if busy or analysis.downloading
                            else brand.STATUS_ONLINE if ready else brand.STATUS_IDLE)
        self._head.setText(("Analysing · " if busy else "") + analysis.describe())
        if busy:
            sub = "The board appears below when it's done; the strips flash purple."
        elif analysis.downloading:
            sub = ("Once only: the model stays on this machine through every update. "
                   "It resumes if interrupted.")
        elif analysis.download_error:
            sub = f"The download stopped: {analysis.download_error}"
        elif analysis.needs_download:
            sub = ("The engine is built in; it needs its model once, from Hugging Face, "
                   "checked against a pinned fingerprint. Nothing else leaves the pit.")
        elif not ready:
            sub = ("This build has no built-in engine. Ollama works too: install it from "
                   f"ollama.com and run: ollama pull {analysis.prefs['model']}")
        else:
            sub = ("Reads the newest robot log through nine read-only tools; every "
                   "figure on a board is checked against what the tools returned.")
        self._sub.setText(sub)
        self._run_btn.setEnabled(ready and not busy)
        self._dl_btn.setVisible(analysis.needs_download or analysis.downloading)
        self._dl_btn.setText("Cancel the download" if analysis.downloading
                             else f"Download the model ({runtime.MODEL['bytes'] / 1e9:.1f} GB)")

    def _run_now(self) -> None:
        if not analysis.analyse():
            self._sub.setText("No robot log with data on this machine yet: import one first.")

    def _download(self) -> None:
        if analysis.downloading:
            analysis.cancel_download()
        else:
            analysis.start_download()

    def _set_auto(self, on: bool) -> None:
        analysis.set_pref(auto_run=bool(on))

    def _on_run_finished(self, run_id: int, _status: str) -> None:
        if run_id > 0:
            self._selected = run_id
        self._reload()

    # ── runs list ─────────────────────────────────────────────────────────

    def _fill_scoreboard(self) -> None:
        """Model × prompt version, best first. Shares are of the findings rated."""
        clear_layout(self._board_grid)
        rows = feedback.scoreboard()
        if not rows:
            self._board_grid.addWidget(_prose("Nothing to score yet."), 0, 0)
            return
        heads = ("Model · prompt", "Runs", "Passed checks", "Rated", "Useful", "Wrong",
                 "Acted on", "Rank")
        for c, h in enumerate(heads):
            self._board_grid.addWidget(eyebrow(h), 0, c)

        def pct(x):
            return "—" if x is None else f"{x * 100:.0f}%"

        for r_i, r in enumerate(rows[:6], start=1):
            cells = (f"{r['model']} · v{r['prompt']}", str(r["runs"]), pct(r["published"]),
                     str(r["rated"]), pct(r["useful"]), pct(r["wrong"]), str(r["acted"]),
                     "—" if r["rank"] is None else f"{r['rank']:.1f}")
            for c, text in enumerate(cells):
                cell = label(text, "stat_value" if c == 0 else "stat_label")
                if c:
                    cell.setFont(mono_font(13))
                self._board_grid.addWidget(cell, r_i, c)
        self._board_grid.setColumnStretch(0, 1)

    def _reload(self) -> None:
        self._fill_scoreboard()
        runs = feedback.recent_runs()
        clear_layout(self._runs_box)
        if not runs:
            self._runs_box.addWidget(_prose("No runs yet."))
        ids = [r["id"] for r in runs]
        if self._selected not in ids:
            self._selected = ids[0] if ids else None
        for r in runs:
            when = feedback.local_time(r["started_at"])
            what = (r["match_key"] or r["title"]
                    or (f"log {r['log_started'][5:16]}" if r["log_started"] else "")
                    or r["session_name"][:28])
            chip = _chip(f"{when}  ·  {what}  ·  {r['status'].capitalize()}")
            chip.setProperty("run_id", r["id"])
            chip.set_active(r["id"] == self._selected)
            chip.clicked.connect(self._pick_run)
            self._runs_box.addWidget(chip)
        self._show(next((r for r in runs if r["id"] == self._selected), None))

    def _pick_run(self) -> None:
        btn = self.sender()
        if btn is not None:
            self._selected = int(btn.property("run_id"))
            self._reload()

    # ── one run ───────────────────────────────────────────────────────────

    def _show(self, run: dict | None) -> None:
        clear_layout(self._detail)
        if run is None:
            return
        verdict = feedback.verdicts(run["id"])
        st = run["status"]

        line = QHBoxLayout()
        line.setSpacing(10)
        line.addWidget(StatusDot(_RUN_DOT.get(st, brand.STATUS_IDLE)),
                       alignment=Qt.AlignmentFlag.AlignTop)
        if st == "published" and run["headline"]:
            text = f"Published · {run['headline'].get('title', '')}"
        elif st == "running":
            text = "Running…"
        else:
            text = f"{st.capitalize()} · {run['reason'][:300]}"
        head = label(text, "stat_value")
        head.setWordWrap(True)
        head.setStyleSheet("font-size: 15px;")
        line.addWidget(head, stretch=1)
        self._detail.addLayout(line)
        self._detail.addSpacing(6)
        s = run["stats"] or {}
        secs = (s.get("analyst_s") or 0) + (s.get("designer_s") or 0)
        meta = label(f"{run['model']}  ·  {secs / 60:.1f} min  ·  "
                     f"{s.get('tool_calls', 0)} tool calls  ·  {run['session_name'][:48]}",
                     "stat_label")
        meta.setFont(mono_font(12))
        meta.setWordWrap(True)
        self._detail.addWidget(meta)
        self._detail.addSpacing(16)

        if run["status"] in ("published", "rejected") and run["findings"]:
            self._detail.addWidget(eyebrow("Rank this run"))
            self._detail.addSpacing(8)
            ranks = QHBoxLayout()
            ranks.setSpacing(6)
            score = (verdict.get(feedback.RUN) or {}).get("score")
            for n in range(1, 6):
                c = _chip(str(n))
                c.setFixedWidth(_TOUCH + 10)
                c.setProperty("score", n)
                c.set_active(score == n)
                c.clicked.connect(self._rank)
                ranks.addWidget(c)
            ranks.addStretch()
            self._detail.addLayout(ranks)
            self._detail.addSpacing(16)
            self._detail.addWidget(eyebrow("Findings · was each one worth the crew's time?"))
            self._detail.addSpacing(8)
            for f in run["findings"]:
                self._detail.addWidget(self._finding(f, verdict.get(f.get("id", "")) or {}))

    def _finding(self, f: dict, verdict: dict) -> QWidget:
        box = QWidget()
        box.setObjectName("finding_row")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(f"QWidget#finding_row {{ border-bottom: 1px solid "
                          f"{brand.CARBON_LINE}; }}")
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 12, 0, 12)
        col.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(StatusDot(_SEVERITY_DOT.get(f.get("severity"), brand.STATUS_IDLE)),
                      alignment=Qt.AlignmentFlag.AlignTop)
        claim = label(f.get("claim", ""), "stat_value")
        claim.setWordWrap(True)
        claim.setStyleSheet("font-size: 15px;")
        top.addWidget(claim, stretch=1)
        col.addLayout(top)
        m = f.get("metric") or {}
        fig = label(f"{m.get('name', '')}: {m.get('value', '')} {m.get('unit') or ''}".strip(),
                    "stat_label")
        fig.setFont(mono_font(12))
        col.addWidget(fig)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        fid = f.get("id", "")
        for key, text in _RATING_TEXT:
            c = _chip(text)
            c.setProperty("finding", fid)
            c.setProperty("rating", key)
            c.set_active(verdict.get("rating") == key)
            c.clicked.connect(self._rate)
            chips.addWidget(c)
        acted = _chip("Acted on")
        acted.setProperty("finding", fid)
        acted.set_active(bool(verdict.get("acted")))
        acted.clicked.connect(self._toggle_acted)
        chips.addWidget(acted)
        chips.addStretch()
        col.addLayout(chips)
        return box

    # ── verdicts (a second tap clears) ────────────────────────────────────

    def _rank(self) -> None:
        btn = self.sender()
        if btn is None or self._selected is None:
            return
        n = int(btn.property("score"))
        current = (feedback.verdicts(self._selected).get(feedback.RUN) or {}).get("score")
        feedback.set_score(self._selected, None if current == n else n)
        self._reload()

    def _rate(self) -> None:
        btn = self.sender()
        if btn is None or self._selected is None:
            return
        fid, key = str(btn.property("finding")), str(btn.property("rating"))
        current = (feedback.verdicts(self._selected).get(fid) or {}).get("rating")
        feedback.set_rating(self._selected, fid, None if current == key else key)
        self._reload()

    def _toggle_acted(self) -> None:
        btn = self.sender()
        if btn is None or self._selected is None:
            return
        fid = str(btn.property("finding"))
        current = (feedback.verdicts(self._selected).get(fid) or {}).get("acted")
        feedback.set_acted(self._selected, fid, not current)
        self._reload()
