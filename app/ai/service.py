"""
`_AnalysisService`: runs the pipeline inside the app.

* **When:** every newly imported log (`config.logs_changed`: each log from
  the last 24 h with no run yet, newest first, when `auto_run` is on), or the
  panel's buttons. Brayden, 2026-10-03: a batch import must have **every**
  log analysed, not only the newest, so requests queue (FIFO) instead of
  "newest wins", and the queue is **held while a batch imports** (`hold()`):
  the model's 6.5 GB and the import don't compete for a 16 GB machine.
* **Where:** a worker thread with its own database connection
  (`local.ThreadDB`), one run at a time; a request while busy is queued (the
  newest wins). The GUI never waits on a model.
* **The engine** (`engine` preference, default `auto`): the built-in
  llama-server (`runtime.py`) once its model is downloaded, else Ollama if
  it's running with the model. The panel's "Download the model" fetches the
  5 GB file once, with progress, resumable, checked against its pinned hash.
* **Memory:** the built-in server is stopped `keep_alive` after the last run
  and when the app quits (Ollama unloads on its own `keep_alive`), so a 16 GB
  pit machine holds the model's ~6.7 GB only while analysing.
* **A published board flashes the strips purple** (sides violet, centre on
  its white die, 2 s + 1 s), the same shape as the queue alerts. Judges and
  lunch mode suppress it, as they do every alert. Rejected and failed runs
  don't flash: they're not news for the crew; the panel shows them.

* **Learning** (`learn` preference, default on; `curate.py`): when the
  engine has nothing queued and enough new verdicts have come in, the model
  turns them into playbook lessons, lowest priority of all (a log or a
  question waiting goes first). At most one attempt every
  `CURATE_RETRY_S`; a rating restarts a short settle timer so a crew rating
  ten findings gets one pass, not ten.
* **A run left "running"** by a quit or a crash mid-run is marked failed at
  startup (one launch runs one run at a time, so nothing can still be
  running); it read as running forever (run 4, 2026-10-03, until 2026-11-03).

`PIT_AI_QUIET=1` (set by `--self-check`): no engine probe, no auto-run.
"""

from __future__ import annotations

import os
import re
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.ai import feedback, runtime, settings
from app.ai.llama import LlamaServer
from app.ai.ollama import Ollama
from app.config import config
from app.lazy_proxy import LazyProxy
from app.leds import leds
from app.leds.service import Alert

STRIP_PURPLE = "#8000FF"        # palette.snap → "violet", the strips' purple
PROBE_EVERY_MS = 30_000
CURATE_SETTLE_MS = 90_000        # after the last rating
CURATE_RETRY_S = 20 * 60         # between attempts that found the engine unwilling
STALE_RUN_H = 3                  # a run older than this still "running" never finished
BUILT_IN, OLLAMA = "llama", "ollama"


def _seconds(keep_alive: str) -> int:
    """Ollama's '1m' / '30s' / '2h' style, as seconds (default 60)."""
    m = re.fullmatch(r"\s*(\d+)\s*([smh]?)\s*", str(keep_alive))
    if not m:
        return 60
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


class _AnalysisService(QObject):

    # busy / step / engine / download state moved: panels re-read
    state_changed = pyqtSignal()
    # run id, status (published | rejected | failed)
    run_finished = pyqtSignal(int, str)
    # runs or verdicts arrived from another machine (sync)
    runs_changed = pyqtSignal()
    # the playbook changed (a curation pass, an admin's switch, a sync)
    playbook_changed = pyqtSignal()

    # Cross-thread hand-offs (emitted from worker threads, queued to this one)
    _worker_step = pyqtSignal(str)
    _worker_done = pyqtSignal(int, str, str)
    _worker_board = pyqtSignal(str)       # a board went up mid-run (the CSA's first look)
    answered = pyqtSignal(object)        # ask.Answer: Ask the pit has a reply
    _ask_done = pyqtSignal(object)
    _probe_done = pyqtSignal(object, object)
    _dl_progress = pyqtSignal(object, object)
    _dl_done = pyqtSignal(str)
    _curate_done = pyqtSignal(object)

    def __init__(self, llm_factory=None):
        super().__init__()
        self._quiet = os.environ.get("PIT_AI_QUIET") == "1"
        self._llm_factory = llm_factory
        self.prefs = settings.load()
        self.runtime = runtime.Runtime(ctx=self.prefs["num_ctx"])
        self.busy = False
        self.step = ""
        self.engine_version: str | None = None       # Ollama's, when it's running
        self.models: list[str] = []
        self.last: tuple[int, str, str] | None = None     # run id, status, reason
        self.downloading = False
        self.download_done = 0
        self.download_error = ""
        self._cancel = threading.Event()
        self._queue: list[str] = []          # session uids waiting, in order
        self._held = False                   # a batch import is running
        self._running: str | None = None
        self._questions: list[str] = []      # Ask the pit, ahead of queued logs
        self.asking = ""                     # the question being answered
        self.answers: list = []              # this launch's, newest last
        self.curating = False
        self.last_curation = None            # curate.Result
        self._curate_tried = 0.0             # monotonic time of the last attempt

        self._worker_step.connect(self._on_step)
        self._worker_done.connect(self._on_done)
        self._worker_board.connect(self._on_board)
        self._flashed = False            # this run's board already flashed the strips
        self._ask_done.connect(self._on_ask_done)
        self._probe_done.connect(self._on_probe)
        self._dl_progress.connect(self._on_dl_progress)
        self._dl_done.connect(self._on_dl_done)
        self._curate_done.connect(self._on_curate_done)
        config.logs_changed.connect(self._on_logs_changed)

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._stop_if_idle)
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(PROBE_EVERY_MS)
        self._probe_timer.timeout.connect(self.probe)
        self._curate_timer = QTimer(self)
        self._curate_timer.setSingleShot(True)
        self._curate_timer.timeout.connect(self._maybe_curate)
        if not self._quiet:
            _close_stale_runs()
            self._probe_timer.start()
            self.probe()

    # ── engines ───────────────────────────────────────────────────────────

    def probe(self) -> None:
        """Ask Ollama what it has, off the GUI thread."""
        def work():
            llm = Ollama()
            version = llm.version()
            models = []
            if version:
                try:
                    models = llm.models()
                except Exception:
                    models = []
            self._probe_done.emit(version, models)
        threading.Thread(target=work, name="ai-probe", daemon=True).start()

    def _on_probe(self, version, models) -> None:
        changed = (version, models) != (self.engine_version, self.models)
        self.engine_version, self.models = version, list(models or [])
        if changed:
            self.state_changed.emit()
        self._next()                        # logs queued before the engine was ready

    @property
    def model_ready(self) -> bool:
        """Ollama has the configured model."""
        m = self.prefs["model"]
        return m in self.models or f"{m}:latest" in self.models

    @property
    def built_in(self) -> bool:
        """This machine has the bundled (or a developer's) llama-server."""
        return runtime.binary() is not None

    def engine(self) -> str | None:
        """Which engine a run would use now, or None."""
        pref = self.prefs["engine"]
        llama_ok = self.built_in and runtime.model_ready()
        ollama_ok = bool(self.engine_version) and self.model_ready
        if pref == BUILT_IN:
            return BUILT_IN if llama_ok else None
        if pref == OLLAMA:
            return OLLAMA if ollama_ok else None
        return BUILT_IN if llama_ok else OLLAMA if ollama_ok else None

    @property
    def ready(self) -> bool:
        return self._llm_factory is not None or self.engine() is not None

    @property
    def needs_download(self) -> bool:
        """The built-in engine is here but its model isn't (and Ollama can't stand in)."""
        return (self.built_in and not runtime.model_ready()
                and self.prefs["engine"] != OLLAMA)

    def model_name(self) -> str:
        return runtime.MODEL["name"] if self.engine() == BUILT_IN else self.prefs["model"]

    def describe(self) -> str:
        """One line for the panel: what's stopping a run, or what's running."""
        if self.curating:
            return "Learning from the crew's ratings"
        more = f" · {len(self._queue)} more queued" if self._queue else ""
        if self._questions:
            more += f" · {len(self._questions)} question(s) waiting"
        if self.busy:
            return (self.step or "Starting…") + more
        if self._held and self._queue:
            return f"{len(self._queue)} log(s) queued; analysis starts when the import finishes"
        if self.downloading:
            return (f"Downloading the model · {self.download_done / 1e9:.2f} of "
                    f"{runtime.MODEL['bytes'] / 1e9:.2f} GB")
        eng = self.engine()
        if eng == BUILT_IN:
            return f"Ready · {runtime.MODEL['name']} on the built-in engine"
        if eng == OLLAMA:
            return f"Ready · {self.prefs['model']} on Ollama {self.engine_version}"
        if self.needs_download:
            return (f"The model isn't downloaded yet "
                    f"({runtime.MODEL['bytes'] / 1e9:.1f} GB, once)")
        if self.engine_version and not self.model_ready:
            return f"{self.prefs['model']} isn't in Ollama (ollama pull {self.prefs['model']})"
        return "No analysis engine on this machine"

    def set_pref(self, **changes) -> None:
        self.prefs = settings.save(**changes)
        self.runtime.ctx = self.prefs["num_ctx"]
        self.state_changed.emit()

    def _make_llm(self):
        if self._llm_factory is not None:
            return self._llm_factory()
        if self.engine() == BUILT_IN:
            return LlamaServer(ensure=self.runtime.ensure, label=runtime.MODEL["name"])
        return Ollama(num_ctx=self.prefs["num_ctx"], keep_alive=self.prefs["keep_alive"])

    # ── the model download ───────────────────────────────────────────────

    def start_download(self) -> None:
        if self.downloading or runtime.model_ready():
            return
        self.downloading, self.download_error = True, ""
        self._cancel.clear()
        self.state_changed.emit()

        def work():
            try:
                runtime.download(self._dl_progress.emit, self._cancel)
                self._dl_done.emit("")
            except runtime.DownloadError as e:
                self._dl_done.emit(str(e))
            except Exception as e:
                self._dl_done.emit(f"{type(e).__name__}: {e}")
        threading.Thread(target=work, name="ai-download", daemon=True).start()

    def cancel_download(self) -> None:
        self._cancel.set()

    def _on_dl_progress(self, done, _total) -> None:
        self.download_done = int(done)
        self.state_changed.emit()

    def _on_dl_done(self, error: str) -> None:
        self.downloading, self.download_error = False, error
        self.state_changed.emit()
        self._next()

    # ── runs ──────────────────────────────────────────────────────────────

    def _on_logs_changed(self) -> None:
        if self._quiet or not self.prefs["auto_run"] or not self.ready:
            return
        for uid in feedback.unanalysed():
            self.enqueue(uid)

    def enqueue(self, uid: str) -> None:
        """Queue one log; it runs when everything before it has."""
        if uid in self._queue or uid == self._running:
            return
        self._queue.append(uid)
        self.state_changed.emit()
        self._next()

    def analyse_all(self) -> int:
        """Queue every log on this machine that no run has looked at."""
        before = len(self._queue)
        for uid in feedback.unanalysed(since_hours=None):
            if uid not in self._queue and uid != self._running:
                self._queue.append(uid)
        self.state_changed.emit()
        self._next()
        return len(self._queue) - before

    def hold(self, held: bool) -> None:
        """A batch import starts (True) or ends (False). Logs queue meanwhile."""
        self._held = held
        self.state_changed.emit()
        if not held:
            self._on_logs_changed()
            self._next()

    @property
    def queued(self) -> int:
        return len(self._queue)

    def _next(self) -> None:
        if self.busy or self._held or not self.ready:
            return
        if self._questions:
            self._start_ask(self._questions.pop(0))
        elif self._queue:
            self.analyse(self._queue.pop(0))
        else:
            self._curate_timer.start(5_000)     # nothing waiting: time to learn?

    # ── learning (app/ai/curate.py) ───────────────────────────────────────

    def verdict_changed(self) -> None:
        """The crew rated something (the panel calls this): learn once they pause."""
        if not self._quiet:
            self._curate_timer.start(CURATE_SETTLE_MS)

    def _maybe_curate(self, force: bool = False) -> bool:
        import time
        from app.ai import curate
        if (self._quiet or self.busy or self._held or self._queue or self._questions
                or not self.ready or not (self.prefs.get("learn") or force)):
            return False
        if not force and time.monotonic() - self._curate_tried < CURATE_RETRY_S \
                and self._curate_tried:
            return False
        try:
            if not (force or curate.due()):
                return False
        except Exception:
            return False
        self._curate_tried = time.monotonic()
        self._idle.stop()
        self.busy, self.curating, self.step = True, True, "Learning from the crew's ratings"
        self.state_changed.emit()
        model, llm = self.model_name(), self._make_llm()
        threading.Thread(target=self._curate_run, args=(model, llm), name="ai-curate",
                         daemon=True).start()
        return True

    def learn_now(self) -> str:
        """The panel's "Learn from ratings now" (admin): "" or why it can't."""
        from app.ai import curate
        if self.busy:
            return "The engine is busy; it learns when it's free."
        if not self.ready:
            return self.describe()
        try:
            if not curate.pending():
                return "No new ratings to learn from."
        except Exception as e:
            return f"{type(e).__name__}: {e}"
        return "" if self._maybe_curate(force=True) else "It can't learn right now."

    def _curate_run(self, model: str, llm) -> None:
        from app.ai import curate, tools
        from app.ai.local import ThreadDB
        handle = None
        try:
            if isinstance(llm, LlamaServer) and not self.runtime.running:
                self._worker_step.emit("starting the built-in engine")
            handle = ThreadDB()
            with tools.using(handle):
                res = curate.curate(llm, model, conn=handle)
        except Exception as e:
            res = curate.Result("failed", model=model, error=f"{type(e).__name__}: {e}")
        finally:
            if handle is not None:
                handle.close()
        _log_line("curate_log.jsonl", res.as_dict())
        self._curate_done.emit(res)

    def _on_curate_done(self, res) -> None:
        self.busy, self.curating, self.step = False, False, ""
        self.last_curation = res
        self.state_changed.emit()
        if res.status == "changed":
            self.playbook_changed.emit()
        if self._questions or (self._queue and not self._held):
            self._next()
        elif self.runtime.running:
            self._idle.start(_seconds(self.prefs["keep_alive"]) * 1000)

    # ── Ask the pit ───────────────────────────────────────────────────────

    def ask(self, question: str) -> str:
        """Answer `question` (app/ai/ask.py) when the engine is free: now, or
        after the run in progress and before queued logs. Returns why it
        can't be asked, or ""; the reply comes as `answered`."""
        question = question.strip()
        if not question:
            return "Type a question first."
        if not self.ready:
            return self.describe()
        if self.busy or self._held:
            self._questions.append(question)
            self.state_changed.emit()
            return ""
        self._start_ask(question)
        return ""

    @property
    def questions_waiting(self) -> int:
        return len(self._questions)

    def _start_ask(self, question: str) -> None:
        self._idle.stop()
        self.busy, self.asking, self.step = True, question, "Reading the question…"
        self.state_changed.emit()
        model = self.model_name()
        llm = self._make_llm()
        history = [(a.question, a.text) for a in self.answers[-3:] if a.status != "failed"]
        on = next((a.focus for a in reversed(self.answers) if a.focus), None)
        threading.Thread(target=self._ask_run, args=(question, model, llm, history, on),
                         name="ai-ask", daemon=True).start()

    def _ask_run(self, question: str, model: str, llm, history, on: str | None) -> None:
        from app.ai import ask, tools
        from app.ai.local import LocalToolbox, ThreadDB
        handle = None
        try:
            if isinstance(llm, LlamaServer) and not self.runtime.running:
                self._worker_step.emit("starting the built-in engine")
            handle = ThreadDB()
            with tools.using(handle):
                answer = ask.ask(LocalToolbox(), llm, model, question, history=history,
                                 on_step=self._worker_step.emit, previous_focus=on)
        except Exception as e:
            answer = ask.Answer(question=question, status="failed", model=model,
                                error=f"{type(e).__name__}: {e}")
        finally:
            if handle is not None:
                handle.close()
        _log_answer(answer)
        self._ask_done.emit(answer)

    def _on_ask_done(self, answer) -> None:
        self.busy, self.asking, self.step = False, "", ""
        self.answers = (self.answers + [answer])[-30:]
        self.state_changed.emit()
        self.answered.emit(answer)
        if self._questions or (self._queue and not self._held):
            self._next()
        elif self.runtime.running:
            self._idle.start(_seconds(self.prefs["keep_alive"]) * 1000)

    def analyse(self, session_uid: str | None = None) -> bool:
        """Start a run on `session_uid` (default: the newest log). False if none."""
        uid = session_uid or feedback.newest_session()
        if uid is None:
            return False
        if self.busy:
            if uid not in self._queue and uid != self._running:
                self._queue.append(uid)
            return True
        # The logs recorded with this one go in the same run (feedback.recorded_with).
        uids = feedback.recorded_with(uid)
        self._queue = [q for q in self._queue if q not in uids]
        self._running = uid
        self._idle.stop()
        self.busy, self.step = True, "Starting…"
        self.state_changed.emit()
        prefs = dict(self.prefs, model=self.model_name())
        llm = self._make_llm()
        lessons = feedback.lessons()        # the crew's verdicts, read on this thread
        from app.ai import brief, playbook
        threading.Thread(target=self._run, args=(uids, prefs, llm, lessons, brief.for_model(),
                                                 playbook.active(), feedback.confirmations()),
                         name="ai-run", daemon=True).start()
        return True

    def _run(self, uids: list[str], prefs: dict, llm, lessons: list[str],
             robot_brief: str = "", lessons_in_force: list[dict] | None = None,
             confirmed: dict[str, int] | None = None) -> None:
        # Imported here: the worker is the only thing that needs them.
        from app.ai import pipeline, tools
        from app.ai.local import LocalToolbox, SqliteSink, ThreadDB
        handle = None
        try:
            if isinstance(llm, LlamaServer) and not self.runtime.running:
                self._worker_step.emit("starting the built-in engine")
            handle = ThreadDB()
            with tools.using(handle):
                res = pipeline.analyse(
                    LocalToolbox(), SqliteSink(), llm, uids, analyst=prefs["model"],
                    designer=prefs["designer"] or None, on_step=self._worker_step.emit,
                    lessons=lessons, brief=robot_brief,
                    playbook_lessons=lessons_in_force, ctx_tokens=prefs["num_ctx"],
                    diagnose_issues=True, csa_confirmed=confirmed, publish_early=True,
                    on_board=self._worker_board.emit)
            self._worker_done.emit(res.run_id, res.status, res.reason)
        except Exception as e:      # before a run row existed (no database, etc.)
            self._worker_done.emit(-1, "failed", f"{type(e).__name__}: {e}")
        finally:
            if handle is not None:
                handle.close()

    def _on_step(self, text: str) -> None:
        self.step = text
        self.state_changed.emit()

    def _on_done(self, run_id: int, status: str, reason: str) -> None:
        self.busy, self.step = False, ""
        self._running = None
        self.last = (run_id, status, reason)
        if status == "published" and not self._flashed:
            self._flash()
        self._flashed = False
        self.state_changed.emit()
        self.run_finished.emit(run_id, status)
        if (self._questions or self._queue) and not self._held:
            self._next()
        else:
            if self.runtime.running:
                self._idle.start(_seconds(self.prefs["keep_alive"]) * 1000)
            self._curate_timer.start(5_000)     # learn while the model is still loaded

    def _flash(self) -> None:
        leds.start_alert(Alert(sides=STRIP_PURPLE, centre_white=True,
                               flash_s=2.0, steady_s=1.0, label="analysis"))

    def _on_board(self, _uid: str) -> None:
        """A board went up before the run ended (the CSA's diagnoses): the
        faces reload now, and the strips flash once for the run, here."""
        if not self._flashed:
            self._flashed = True
            self._flash()
        self.runs_changed.emit()

    def _stop_if_idle(self) -> None:
        if not self.busy:
            threading.Thread(target=self.runtime.stop, name="ai-stop", daemon=True).start()

    def shutdown(self) -> None:
        """Quitting: stop the engine (its memory goes back) and any download."""
        self._cancel.set()
        self.runtime.stop()


def _log_answer(answer) -> None:
    """Every answer, for judging the prompt later (`ask_log.jsonl` beside the
    database; never synced). Best effort."""
    _log_line("ask_log.jsonl", answer.as_dict())


def _log_line(name: str, record: dict) -> None:
    import json
    from app import paths
    try:
        with open(paths.data(name), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def _close_stale_runs() -> None:
    """Runs a quit or crash left "running" (see the module docstring)."""
    from app.db import db
    try:
        with db.transaction() as conn:
            conn.execute(
                f"""UPDATE analysis_run SET status = 'failed', finished_at = datetime('now'),
                      reject_reason = 'never finished: the app closed or stopped during the run'
                    WHERE status = 'running'
                      AND started_at < datetime('now', '-{int(STALE_RUN_H)} hours')""")
    except Exception:
        pass                    # a broken database is reported elsewhere


analysis: _AnalysisService = LazyProxy("analysis", "init_analysis")  # type: ignore[assignment]


def init_analysis(llm_factory=None) -> _AnalysisService:
    real = _AnalysisService(llm_factory)
    analysis._install(real)
    return real
