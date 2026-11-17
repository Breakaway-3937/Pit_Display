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
* **What has it learned?** The playbook (`app/ai/playbook.py`): the
  lessons the analyst has drawn from these ratings, each with its record
  (did findings on it get rated better once it was given?) and whether
  code enforces it. An admin can enforce, retire or restore a lesson and
  start a learning pass.
* **Was it any good?** Recent runs; the chosen run's findings, each rated
  **Useful / Not useful / Wrong** plus **Acted on** (and, for the last two,
  one tap of *why*: the reason is what a lesson is learned from), and a
  **1–5 rank** for the run. Ratings are the reward signal (`app/ai/feedback.py`): a clean bill
  of health the crew trusted is as useful as a fault, so nothing here counts
  problems found.

Status colours are dots, never type (CLAUDE.md, "Theming"): a finding's dot
is its severity, so red appears only for a latched fault.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

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


class _BriefEditor(QWidget):
    """
    The crew's robot brief for the analyst (`app/ai/brief.py`): the season,
    the mechanisms, what normal looks like. Admin-only to edit (it steers
    every run on every pit); everyone sees how much is written.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        from app.admin import admin
        from app.ai import brief
        self._brief = brief
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(eyebrow("Robot brief · what the analyst should know"))
        intro = _prose("A short note the analyst reads before every log: this season's "
                       "game, each mechanism and the motors that drive it, what normal "
                       "looks like, and what the crew wants flagged. Short and specific "
                       "beats long: the model can't read a game manual or the robot code.")
        lay.addWidget(intro)
        self._state = label("", "stat_label")
        self._state.setWordWrap(True)
        lay.addWidget(self._state)
        self._body = QWidget()
        b = QVBoxLayout(self._body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(8)
        self._edit = QPlainTextEdit()
        self._edit.setFixedHeight(260)
        self._edit.textChanged.connect(self._count)
        b.addWidget(self._edit)
        row = QHBoxLayout()
        self._counter = label("", "stat_label")
        row.addWidget(self._counter, stretch=1)
        tpl = RoundedButton("Start from the template", variant="secondary")
        tpl.setMinimumHeight(_TOUCH)
        tpl.clicked.connect(self._template)
        row.addWidget(tpl)
        save = RoundedButton("Save the brief", variant="secondary")
        save.setMinimumHeight(_TOUCH)
        save.clicked.connect(self._save)
        row.addWidget(save)
        b.addLayout(row)
        lay.addWidget(self._body)
        self._edit.setPlainText(brief.load()["text"])
        try:
            admin.lock_state_changed.connect(self._apply_lock)
            unlocked = admin.unlocked
        except RuntimeError:                  # no lock service (a check): stay locked
            unlocked = False
        self._apply_lock(unlocked)

    def _apply_lock(self, unlocked: bool) -> None:
        self._body.setVisible(unlocked)
        written = self._brief.for_model()
        self._state.setText(
            (f"{len(written)} characters written; the analyst reads them before every log."
             if written else "Nothing written yet, so the analyst works without it.")
            + ("" if unlocked else " Unlock with the Breakaway mark to edit."))

    def _count(self) -> None:
        n = len(self._edit.toPlainText())
        over = n - self._brief.MAX_CHARS
        self._counter.setText(f"{n} / {self._brief.MAX_CHARS} characters"
                              + (f" · {over} over: shorten it" if over > 0 else ""))

    def _template(self) -> None:
        if not self._edit.toPlainText().strip():
            self._edit.setPlainText(self._brief.TEMPLATE)
        else:
            self._edit.setPlainText(self._edit.toPlainText().rstrip() + "\n\n" + self._brief.TEMPLATE)

    def _save(self) -> None:
        if len(self._edit.toPlainText()) > self._brief.MAX_CHARS:
            self._counter.setText(self._counter.text() + " · not saved")
            return
        self._brief.save(text=self._edit.toPlainText())
        self._apply_lock(True)


class _PlaybookView(QWidget):
    """
    The playbook (`app/ai/playbook.py`): every lesson in force, what it's
    learned from, its record, and whether code enforces it. Switching a
    lesson steers every run on every pit, so the switches are admin-only
    (hidden, not disabled); reading the book isn't.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        from app.admin import admin
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(eyebrow("Playbook · what the analyst has learned from your ratings"))
        lay.addWidget(_prose(
            "Your ratings, and the reason you give for a Not useful or Wrong, become short "
            "lessons the analyst reads before each log it's relevant to. Each one is scored "
            "by your later ratings. When the analyst keeps ignoring an Avoid lesson, code "
            "enforces it and leaves those findings out (never a fault)."))
        learn = QHBoxLayout()
        learn.setSpacing(12)
        txt = label("Learn from the crew's ratings when the engine is free", "stat_value")
        txt.setWordWrap(True)
        txt.setStyleSheet("font-size: 15px;")
        learn.addWidget(txt, stretch=1)
        self._learn = ToggleSwitch()
        self._learn.setChecked(bool(analysis.prefs.get("learn", True)))
        self._learn.toggled.connect(self._set_learn)
        learn.addWidget(self._learn, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(learn)
        self._state = _prose("")
        lay.addWidget(self._state)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(0)
        lay.addLayout(self._rows)
        self._admin = QWidget()
        a = QHBoxLayout(self._admin)
        a.setContentsMargins(0, 8, 0, 0)
        self._learn_btn = RoundedButton("Learn from ratings now", variant="secondary")
        self._learn_btn.setMinimumHeight(_TOUCH)
        self._learn_btn.clicked.connect(self._learn_now)
        a.addWidget(self._learn_btn)
        a.addStretch()
        lay.addWidget(self._admin)
        self._unlocked = False
        self._waiting = 0
        try:
            admin.lock_state_changed.connect(self._apply_lock)
            self._unlocked = admin.unlocked
        except RuntimeError:                  # no lock service (a check): stay locked
            pass
        analysis.playbook_changed.connect(self.refresh)
        analysis.state_changed.connect(self._refresh_state)
        self.refresh()

    def _apply_lock(self, unlocked: bool) -> None:
        self._unlocked = bool(unlocked)
        self.refresh()

    def _set_learn(self, on: bool) -> None:
        analysis.set_pref(learn=bool(on))

    def _learn_now(self) -> None:
        why = analysis.learn_now()
        if why:
            self._state.setText(why)

    def _refresh_state(self) -> None:
        from app.ai import curate, playbook
        n = len(playbook.active())
        last = analysis.last_curation
        if analysis.curating:
            text = "Learning from the crew's ratings now…"
        elif last is not None and last.status == "failed":
            text = f"The last learning pass didn't finish: {last.error[:160]}"
        else:
            waiting = self._waiting          # counted in refresh(): this runs on every step
            text = (f"{n} lesson(s) in force · {waiting} rating(s) not learned from yet"
                    + (f" (it learns after {curate.DUE_COUNT})" if waiting < curate.DUE_COUNT
                       else ""))
            hist = playbook.load()["history"]
            if hist:
                text += f" · last change {hist[-1]['at'][:16]} UTC: {hist[-1]['what'][:140]}"
        self._state.setText(text)

    def refresh(self) -> None:
        from app.ai import curate, playbook
        try:
            self._waiting = len(curate.pending(limit=10_000))
        except Exception:
            self._waiting = 0
        clear_layout(self._rows)
        doc = playbook.load()
        lessons = playbook.active(doc)
        try:
            records = playbook.evaluate(lessons=lessons)
        except Exception:
            records = {}
        retired = ([s for s in playbook.SEEDS if s["id"] in doc["retired_seeds"]]
                   + [x for x in doc["lessons"] if x["retired"] and x.get("by") != "seed"])
        for x in lessons:
            self._rows.addWidget(self._row(x, records.get(x["id"]), retired=False))
        if self._unlocked:
            for x in retired:
                self._rows.addWidget(self._row(x, None, retired=True))
        self._admin.setVisible(self._unlocked)
        self._refresh_state()

    def _row(self, x: dict, record, *, retired: bool) -> QWidget:
        box = QWidget()
        box.setObjectName("lesson_row")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(f"QWidget#lesson_row {{ border-bottom: 1px solid "
                          f"{brand.CARBON_LINE}; }}")
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 10, 0, 10)
        col.setSpacing(4)
        kind = "Avoid" if x["kind"] == "avoid" else "Do"
        rule = label(f"{kind}: {x['rule']}", "stat_value")
        rule.setWordWrap(True)
        rule.setStyleSheet("font-size: 15px;")
        col.addWidget(rule)
        if x.get("seed"):
            origin = "built in · " + x.get("why", "")
        else:
            n = len(x.get("learned_from") or [])
            origin = f"learned {x.get('added', '')} by {x.get('by') or 'the model'} from {n} rating(s)"
        bits = [origin]
        if record is not None:
            bits.append(record.summary(x["kind"]))
        if x.get("enforce") and x["kind"] == "avoid":
            bits.append("enforced by code")
        if retired:
            bits.append("retired")
        meta = label("  ·  ".join(b for b in bits if b), "stat_label")
        meta.setFont(mono_font(12))
        meta.setWordWrap(True)
        col.addWidget(meta)
        if self._unlocked:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            if x["kind"] == "avoid" and not retired:
                c = _chip("Enforced by code")
                c.set_active(bool(x.get("enforce")))
                c.setProperty("lesson", x["id"])
                c.clicked.connect(self._toggle_enforce)
                chips.addWidget(c)
            c = _chip("Restore" if retired else "Retire")
            c.setProperty("lesson", x["id"])
            c.setProperty("retired", retired)
            c.clicked.connect(self._toggle_retired)
            chips.addWidget(c)
            chips.addStretch()
            col.addLayout(chips)
        return box

    def _toggle_enforce(self) -> None:
        from app.ai import playbook
        btn = self.sender()
        if btn is None:
            return
        lid = str(btn.property("lesson"))
        now = next((x for x in playbook.active() if x["id"] == lid), {}).get("enforce")
        playbook.set_flag(lid, enforce=not now)
        self.refresh()

    def _toggle_retired(self) -> None:
        from app.ai import playbook
        btn = self.sender()
        if btn is None:
            return
        playbook.set_flag(str(btn.property("lesson")),
                          retired=not bool(btn.property("retired")))
        self.refresh()


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
        self._traces_open: set[int] = set()      # runs whose "what it looked at" is unfolded
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
        self._all_btn = RoundedButton("Analyse every log not yet analysed", variant="secondary")
        self._all_btn.setMinimumHeight(_TOUCH)
        self._all_btn.clicked.connect(self._run_all)
        self._dl_btn = RoundedButton("", variant="secondary")
        self._dl_btn.setMinimumHeight(_TOUCH)
        self._dl_btn.clicked.connect(self._download)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._run_btn)
        buttons.addWidget(self._all_btn)
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
        root.addWidget(_BriefEditor())
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)
        self._playbook = _PlaybookView()
        root.addWidget(self._playbook)
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
            sub = ("Reads every newly imported robot log, one after another, through "
                   "read-only tools; every figure on a board is checked against what the "
                   "tools returned. Open a run below to see exactly what it looked at.")
        self._sub.setText(sub)
        self._run_btn.setEnabled(ready and not busy)
        self._all_btn.setEnabled(ready)
        self._dl_btn.setVisible(analysis.needs_download or analysis.downloading)
        self._dl_btn.setText("Cancel the download" if analysis.downloading
                             else f"Download the model ({runtime.MODEL['bytes'] / 1e9:.1f} GB)")

    def _run_now(self) -> None:
        if not analysis.analyse():
            self._sub.setText("No robot log with data on this machine yet: import one first.")

    def _run_all(self) -> None:
        n = analysis.analyse_all()
        self._sub.setText(f"Queued {n} log(s) that no run has looked at yet." if n
                          else "Every log on this machine has been analysed.")

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
            runs = str(r["runs"]) + (f" ({r['failed']} failed)" if r.get("failed") else "")
            cells = (f"{r['model']} · v{r['prompt']}", runs, pct(r["published"]),
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
        self._playbook.refresh()
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
        # What code left out, so nothing is dropped where nobody can see it.
        left = []
        if s.get("filler"):
            left.append(f"{len(s['filler'])} all-clear finding(s) beside a real one")
        if s.get("enforced"):
            left.append(", ".join(f"{d['finding']} (playbook: {d['lesson']})"
                                  for d in s["enforced"]))
        if s.get("playbook"):
            left_text = f"Given {len(s['playbook'])} playbook lesson(s)"
        else:
            left_text = ""
        if left:
            left_text += ("  ·  " if left_text else "") + "Left out by code: " + "; ".join(left)
        if left_text:
            self._detail.addSpacing(4)
            self._detail.addWidget(_prose(left_text))
        self._detail.addSpacing(10)
        self._add_trace(run)
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
            csa = {d.get("finding"): d for d in (run["stats"] or {}).get("incidents") or []}
            for f in run["findings"]:
                self._detail.addWidget(self._finding(f, verdict.get(f.get("id", "")) or {},
                                                     csa.get(f.get("id"))))

    # ── what it looked at ─────────────────────────────────────────────────

    def _add_trace(self, run: dict) -> None:
        """Every log, tool call and result the run saw (`app/ai/trace.py`),
        folded away behind one chip until asked for."""
        opened = run["id"] in self._traces_open
        steps, names = feedback.run_trace(run["id"]) if opened else ([], {})
        n_tools = (run["stats"] or {}).get("tool_calls", 0)
        chip = _chip(("Hide" if opened else "Show") + f" what it looked at · {n_tools} tool calls")
        chip.set_active(opened)
        chip.setProperty("run_id", run["id"])
        chip.clicked.connect(self._toggle_trace)
        self._detail.addWidget(chip)
        if not opened:
            return
        self._detail.addSpacing(8)
        logs = ", ".join(sorted(names.values())) or "—"
        head = label(f"Logs: {logs}", "stat_label")
        head.setWordWrap(True)
        self._detail.addWidget(head)
        if not steps:
            self._detail.addWidget(_prose("No transcript was kept for this run."))
            return
        for i, st in enumerate(steps, 1):
            args = ", ".join(f"{k}={names.get(str(v), v) if 'session' in k else v}"
                             for k, v in st.args.items())
            text = f"{i:>2}. {st.title}" + (f"({args})" if args else "")
            line = label(text, "stat_label")
            f = mono_font(12)
            f.setBold(True)
            line.setFont(f)
            line.setWordWrap(True)
            self._detail.addWidget(line)
            if st.result:
                res = label(f"     → {st.result}", "stat_label")
                res.setFont(mono_font(12))
                res.setWordWrap(True)
                self._detail.addWidget(res)

    def _toggle_trace(self) -> None:
        btn = self.sender()
        if btn is None:
            return
        rid = int(btn.property("run_id"))
        self._traces_open ^= {rid}
        self._reload()

    def _finding(self, f: dict, verdict: dict, diagnosis: dict | None = None) -> QWidget:
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
        if diagnosis:
            self._add_diagnosis(col, fid, diagnosis, verdict)
        # Why, one tap (feedback.REASONS): what a playbook lesson is learned from.
        reasons = feedback.REASONS.get(verdict.get("rating") or "")
        if reasons:
            why = QHBoxLayout()
            why.setSpacing(6)
            why.addWidget(label("Why?", "stat_label"))
            for text in reasons:
                c = _chip(text)
                c.setProperty("finding", fid)
                c.setProperty("reason", text)
                c.set_active(verdict.get("note") == text)
                c.clicked.connect(self._set_reason)
                why.addWidget(c)
            why.addStretch()
            col.addLayout(why)
        return box

    def _add_diagnosis(self, col, fid: str, d: dict, verdict: dict) -> None:
        """The CSA's reasoning (app/ai/diagnose.py), and "what was it really?":
        one tap on the cause the crew found, recorded as a confirmation the
        next diagnoses see (feedback.confirmations)."""
        who = "the model" if d.get("by") == "model" else "code (the model's answer didn't pass)"
        ruled = [c["cause"] for c in d.get("candidates") or [] if c["id"] in (d.get("ruled_out") or [])]
        # Three kinds of statement, kept apart (Brayden, 2026-11-05: "we need
        # diagnosable tools and noted extrapolation"): what the logs show,
        # what the model infers from it, and where it's reaching.
        if d.get("measured"):
            col.addWidget(_prose("Measured: " + "; ".join(d["measured"]) + "."))
        text = (f"Inferred ({d.get('confidence', 'low')} confidence, by {who}): "
                f"{d.get('why', '')}"
                + (f" Ruled out: {'; '.join(ruled)}." if ruled else ""))
        why = _prose(text)
        col.addWidget(why)
        if d.get("hunch"):
            col.addWidget(_prose("A reach (not checked against the knowledge base): "
                                 + d["hunch"]))
        cands = [c for c in d.get("candidates") or []][:4]
        if not cands:
            return
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(label("What was it really?", "stat_label"))
        for c in cands:
            chip = _chip(c["cause"][:40] + ("…" if len(c["cause"]) > 40 else ""))
            chip.setProperty("finding", fid)
            chip.setProperty("reason", feedback.CONFIRMED + c["id"])
            chip.set_active(verdict.get("note") == feedback.CONFIRMED + c["id"])
            chip.clicked.connect(self._set_reason)
            row.addWidget(chip)
        row.addStretch()
        col.addLayout(row)

    # ── verdicts (a second tap clears) ────────────────────────────────────

    def _rank(self) -> None:
        btn = self.sender()
        if btn is None or self._selected is None:
            return
        n = int(btn.property("score"))
        current = (feedback.verdicts(self._selected).get(feedback.RUN) or {}).get("score")
        feedback.set_score(self._selected, None if current == n else n)
        analysis.verdict_changed()
        self._reload()

    def _rate(self) -> None:
        btn = self.sender()
        if btn is None or self._selected is None:
            return
        fid, key = str(btn.property("finding")), str(btn.property("rating"))
        before = feedback.verdicts(self._selected).get(fid) or {}
        feedback.set_rating(self._selected, fid, None if before.get("rating") == key else key)
        if before.get("note") and before.get("rating") != key:
            feedback.set_note(self._selected, fid, None)   # a reason belongs to its verdict
        analysis.verdict_changed()
        self._reload()

    def _set_reason(self) -> None:
        btn = self.sender()
        if btn is None or self._selected is None:
            return
        fid, text = str(btn.property("finding")), str(btn.property("reason"))
        before = feedback.verdicts(self._selected).get(fid) or {}
        current = before.get("note")
        feedback.set_note(self._selected, fid, None if current == text else text)
        if text.startswith(feedback.CONFIRMED) and current != text and not before.get("rating"):
            feedback.set_rating(self._selected, fid, "useful")   # it was right enough to name
        analysis.verdict_changed()
        self._reload()

    def _toggle_acted(self) -> None:
        btn = self.sender()
        if btn is None or self._selected is None:
            return
        fid = str(btn.property("finding"))
        current = (feedback.verdicts(self._selected).get(fid) or {}).get("acted")
        feedback.set_acted(self._selected, fid, not current)
        analysis.verdict_changed()
        self._reload()
