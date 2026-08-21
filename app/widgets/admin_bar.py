"""
Admin bar — the slim inline strip under the control screen's top bar.

Deliberately not a separate screen or a modal: the operator needs to watch the
LED and equaliser controls appear as they unlock, and a dialog would cover the
very thing that is changing.

It is also deliberately *small*. This is a transient strip that appears over the
operator's actual work, so it stays one row tall (~48px) and only grows when the
change-password form is open. Everything here fights for that: fixed vertical
size policies so it hugs its content, compact controls, and the status message
sharing the row rather than claiming a line of its own.

Two states in one widget:
  locked   — password field + Unlock
  unlocked — status, change-password, Lock & close

Closing always locks. The pit display runs unattended, so there is no
stay-unlocked option.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLineEdit, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from app import brand
from app.admin import admin
from app.config import config
from app.widgets.brand_widgets import RoundedButton, eyebrow
from app.widgets.helpers import label

_LOCKED, _UNLOCKED = 0, 1

_ROW_H = 30          # compact control height — the top bar is only 76px itself
_FIELD_W = 170


def _compact(btn: RoundedButton, width: int | None = None) -> RoundedButton:
    btn.setMinimumHeight(_ROW_H)
    btn.setFixedHeight(_ROW_H)
    if width:
        btn.setFixedWidth(width)
    return btn


def _field(placeholder: str) -> QLineEdit:
    f = QLineEdit()
    f.setEchoMode(QLineEdit.EchoMode.Password)
    f.setPlaceholderText(placeholder)
    f.setFixedHeight(_ROW_H)
    f.setFixedWidth(_FIELD_W)
    return f


class AdminBar(QWidget):
    """Emits `closed` when dismissed (which also locks)."""

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        admin.lock_state_changed.connect(self._on_lock_changed)
        admin.password_changed.connect(self._refresh_nag)
        config.team_changed.connect(self._on_team_changed)
        self._refresh_nag()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        # Hug the content. Without this the bar expands into whatever vertical
        # space the parent layout has spare, which is most of the window.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        # Scope the fill to this widget by object name — a bare `QWidget` rule
        # would repaint every child, including the buttons that draw themselves.
        # The red left edge borrows the sidebar's active-row idiom: it reads as
        # "a mode is currently open" at a glance.
        self.setObjectName("admin_bar")
        self.setStyleSheet(
            f"QWidget#admin_bar {{"
            f"  background-color: {brand.CARBON_SURF2};"
            f"  border-bottom: 1px solid {brand.CARBON_LINE};"
            f"  border-left: 3px solid {brand.RED};"
            f"}}"
            f"QWidget#admin_bar QLineEdit {{ background-color: {brand.CARBON_BG}; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 8, 20, 8)
        outer.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Policy.Preferred,
                                  QSizePolicy.Policy.Fixed)
        self._stack.addWidget(self._locked_page())
        self._stack.addWidget(self._unlocked_page())
        # A QStackedWidget sizes to its tallest page; both are one row, so pin
        # it rather than letting the hidden page's hint drive the height.
        self._stack.setFixedHeight(_ROW_H)
        outer.addWidget(self._stack)

        # Change-password form — outside the stack so it can push the bar
        # taller only while it is open.
        self._change_form = self._change_page()
        self._change_form.setVisible(False)
        outer.addWidget(self._change_form)

    def _locked_page(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(eyebrow("Admin"))

        self._password = _field("Password")
        self._password.returnPressed.connect(self._try_unlock)
        row.addWidget(self._password)

        self._unlock_btn = _compact(
            RoundedButton("Unlock", variant="primary",
                          accent=config.active_team.primary_color), 84)
        self._unlock_btn.clicked.connect(self._try_unlock)
        row.addWidget(self._unlock_btn)

        self._locked_msg = label("Unlocks LED tuning and the equaliser.", "stat_label")
        row.addWidget(self._locked_msg)

        row.addStretch()
        close = _compact(RoundedButton("✕", variant="ghost"), 34)
        close.clicked.connect(self._close)
        row.addWidget(close)
        return page

    def _unlocked_page(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(eyebrow("Admin unlocked", brand.STATUS_ONLINE))

        self._unlocked_msg = label("LED tuning and the equaliser are editable.",
                                   "stat_label")
        row.addWidget(self._unlocked_msg)
        row.addStretch()

        self._change_toggle = _compact(
            RoundedButton("Change password", variant="ghost"), 148)
        self._change_toggle.clicked.connect(self._toggle_change_form)
        row.addWidget(self._change_toggle)

        lock_btn = _compact(
            RoundedButton("Lock & close", variant="secondary",
                          accent=config.active_team.primary_color), 124)
        lock_btn.clicked.connect(self._close)
        row.addWidget(lock_btn)
        return page

    def _change_page(self) -> QWidget:
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 9, 0, 0)
        row.setSpacing(8)

        self._current_pw = _field("Current")
        self._new_pw = _field("New")
        self._confirm_pw = _field("Confirm new")
        for f in (self._current_pw, self._new_pw, self._confirm_pw):
            f.returnPressed.connect(self._try_change)
            row.addWidget(f)

        self._save_pw_btn = _compact(
            RoundedButton("Save", variant="primary",
                          accent=config.active_team.primary_color), 78)
        self._save_pw_btn.clicked.connect(self._try_change)
        row.addWidget(self._save_pw_btn)
        row.addStretch()
        return page

    # ── Actions ───────────────────────────────────────────────────────────

    def _try_unlock(self):
        if admin.unlock(self._password.text()):
            self._password.clear()
            # Not _set_message("") — the unlock signal may have just raised the
            # default-password nag, and clearing here would wipe it.
            self._refresh_nag()
        else:
            self._password.selectAll()
            self._set_message("That password is not correct.", error=True)

    def _try_change(self):
        new, confirm = self._new_pw.text(), self._confirm_pw.text()
        if new != confirm:
            self._set_message("The two new passwords do not match.", error=True)
            return
        ok, msg = admin.change_password(self._current_pw.text(), new)
        self._set_message(msg, error=not ok)
        if ok:
            for f in (self._current_pw, self._new_pw, self._confirm_pw):
                f.clear()
            self._change_form.setVisible(False)

    def _toggle_change_form(self):
        showing = not self._change_form.isVisible()
        self._change_form.setVisible(showing)
        if showing:
            self._current_pw.setFocus()

    def _close(self):
        """Dismiss the bar. Always locks — re-opening demands the password."""
        admin.lock()
        self._reset_fields()
        self.closed.emit()

    def _reset_fields(self):
        for f in (self._password, self._current_pw, self._new_pw, self._confirm_pw):
            f.clear()
        self._change_form.setVisible(False)
        self._set_message("")

    def focus_password(self):
        """Called when the bar opens, so the operator can just start typing."""
        self._password.setFocus()
        self._password.selectAll()

    # ── State ─────────────────────────────────────────────────────────────

    def _on_lock_changed(self, unlocked: bool):
        self._stack.setCurrentIndex(_UNLOCKED if unlocked else _LOCKED)
        if not unlocked:
            self._change_form.setVisible(False)
        self._refresh_nag()

    def _refresh_nag(self):
        """Raise the default-password warning, or clear it once it no longer applies."""
        if admin.unlocked and admin.is_default_password:
            self._set_message("Default password still in use — change it.",
                              error=True)
        else:
            self._set_message("")

    def _set_message(self, text: str, error: bool = False):
        """
        The message shares the row with the status text rather than taking a
        line of its own — it replaces it, so the bar never grows a second row
        just to say something short.
        """
        color = brand.RED if error else brand.MUTED_DARK
        target = self._unlocked_msg if admin.unlocked else self._locked_msg
        default = ("LED tuning and the equaliser are editable." if admin.unlocked
                   else "Unlocks LED tuning and the equaliser.")
        target.setText(text or default)
        target.setStyleSheet(f"color: {color if text else brand.MUTED_DARK};"
                             " background: transparent;")

    def current_message(self) -> str:
        """The status text currently shown, whichever page is active."""
        target = self._unlocked_msg if admin.unlocked else self._locked_msg
        return target.text()

    def _on_team_changed(self, team):
        for btn in (self._unlock_btn, self._save_pw_btn):
            btn.set_accent(team.primary_color)
