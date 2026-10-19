"""
Control → Pit Systems → Ask the pit. A question about the robot's logs, a
few sentences back from the local model (`app/ai/ask.py`).

* **Ask.** A line to type in (Enter asks) and the panel's one primary
  button, plus a few example questions to tap on a touch screen.
* **Is it working?** One line: ready, answering (and which tool it's
  reading), or what's missing; the model is set up on the Analysis panel.
* **Can I trust it?** Each answer names what it looked at. A figure the
  checks couldn't find in any tool result is listed under the answer with a
  pending dot, so nobody acts on a number the logs don't hold.

Answers stay for this launch (newest first); every one is also written to
`ask_log.jsonl` for judging the prompt later.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from app import attribution, brand
from app.ai.service import analysis
from app.attribution import link
from app.widgets.brand_widgets import RoundedButton, StatusDot, eyebrow
from app.widgets.helpers import LinkLabel, clear_layout, divider, label

_TOUCH = 46
EXAMPLES = (
    "Any faults in the newest log?",
    "Which motor ran hottest in the last match?",
    "Is anything wearing out?",
)
_DOT = {"answered": brand.STATUS_ONLINE, "unverified": brand.STATUS_PENDING,
        "failed": brand.STATUS_IDLE}


def _prose(text: str, obj: str = "stat_label") -> QWidget:
    lbl = label(text, obj)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


class _ExampleButton(RoundedButton):
    """Asks its own text: a bound method on the panel, never a lambda."""

    def __init__(self, text: str, panel: "AskPanel"):
        super().__init__(text, variant="secondary")
        self.setMinimumHeight(_TOUCH)
        self._panel = panel
        self.clicked.connect(self._ask)

    def _ask(self) -> None:
        self._panel.ask(self.text())


class AskPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(eyebrow("Ask about the robot's logs"))
        root.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("e.g. Did any motor fault in qm14?")
        self._input.setMinimumHeight(_TOUCH)
        self._input.setMaxLength(300)
        self._input.returnPressed.connect(self._ask_typed)
        row.addWidget(self._input, stretch=1)
        self._ask_btn = RoundedButton("Ask", variant="primary")
        self._ask_btn.setMinimumHeight(_TOUCH)
        self._ask_btn.clicked.connect(self._ask_typed)
        row.addWidget(self._ask_btn)
        root.addLayout(row)
        root.addSpacing(10)

        examples = QHBoxLayout()
        examples.setSpacing(8)
        for text in EXAMPLES:
            examples.addWidget(_ExampleButton(text, self))
        examples.addStretch()
        root.addLayout(examples)
        root.addSpacing(12)

        state = QHBoxLayout()
        state.setSpacing(10)
        self._dot = StatusDot()
        state.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._state = _prose("")
        state.addWidget(self._state, stretch=1)
        root.addLayout(state)
        root.addSpacing(8)
        root.addWidget(_prose(
            "It reads this machine's logs with the same read-only tools as the analysis, "
            "and every figure it gives is checked against what those tools returned. "
            "Nothing leaves this machine."))
        root.addSpacing(6)
        # An answer can name the match a log was and who played it (match_context).
        root.addWidget(LinkLabel(
            "Which match a log was, and who played in it: "
            f"{link(attribution.TBA_TEXT, attribution.TBA_URL)}."))
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        root.addWidget(eyebrow("Answers"))
        root.addSpacing(10)
        self._answers = QVBoxLayout()
        self._answers.setSpacing(0)
        root.addLayout(self._answers)

        # Bound methods, never lambdas: the service outlives this panel.
        analysis.state_changed.connect(self._refresh_state)
        analysis.answered.connect(self._on_answered)
        self._refresh_state()
        self._fill_answers()

    # ── asking ────────────────────────────────────────────────────────────

    def _ask_typed(self) -> None:
        self.ask(self._input.text())

    def ask(self, question: str) -> None:
        why = analysis.ask(question)
        if why:
            self._state.setText(why)
            return
        self._input.clear()
        self._refresh_state()

    # ── state ─────────────────────────────────────────────────────────────

    def _refresh_state(self) -> None:
        waiting = analysis.questions_waiting
        if analysis.asking:
            self._dot.set_color(brand.STATUS_PENDING)
            text = f"Answering “{analysis.asking}” · {analysis.step or 'starting…'}"
        elif analysis.busy:
            self._dot.set_color(brand.STATUS_PENDING)
            text = f"An analysis is running; your question is next. {analysis.describe()}"
        elif analysis.ready:
            self._dot.set_color(brand.STATUS_ONLINE)
            text = "Ready."
        else:
            self._dot.set_color(brand.STATUS_IDLE)
            text = (f"{analysis.describe()}. The model is set up on the Analysis panel.")
        if waiting and not (analysis.busy and not analysis.asking):
            text += f" {waiting} more waiting."
        self._state.setText(text)
        self._ask_btn.setEnabled(analysis.ready)
        # A disabled primary still paints red (CLAUDE.md, "Theming").
        self._ask_btn.set_variant("primary" if analysis.ready else "secondary")

    def _on_answered(self, _answer) -> None:
        self._fill_answers()

    def _fill_answers(self) -> None:
        clear_layout(self._answers)
        answers = list(reversed(analysis.answers))
        if not answers:
            self._answers.addWidget(_prose("Nothing asked yet this session."))
            return
        for i, a in enumerate(answers):
            if i:
                self._answers.addSpacing(14)
            self._answers.addWidget(self._answer_row(a))

    def _answer_row(self, a) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(StatusDot(_DOT.get(a.status, brand.STATUS_IDLE)),
                      alignment=Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        q = _prose(a.question, "stat_value")
        q.setStyleSheet("font-size: 15px;")
        col.addWidget(q)
        if a.status == "failed":
            col.addWidget(_prose(f"No answer: {a.error or 'the model gave none'}."))
        else:
            text = _prose(a.text, "")         # the app's body ink
            text.setStyleSheet("font-size: 15px;")
            col.addWidget(text)
        if a.unverified:
            col.addWidget(_prose(
                f"Not found in the logs: {', '.join(a.unverified)}. Check before trusting "
                "these figures."))
        looked = sorted({c["tool"] for c in a.calls})
        when = datetime.fromtimestamp(a.at).strftime("%H:%M")
        col.addWidget(_prose(
            f"{when} · {a.model} · {a.seconds:g} s"
            + (f" · looked at {', '.join(looked)}" if looked else " · from the logs list only")))
        lay.addLayout(col, stretch=1)
        return w
