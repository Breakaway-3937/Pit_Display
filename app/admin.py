"""
Admin lock — gates the advanced LED and equaliser controls.

`admin` is a lazy proxy: safe to import at module level anywhere. Call
`init_admin()` once in main() after `init_db()` (it seeds the default
credential on first run).

## What this is, and what it is not

This is a **UI lock**, not a security boundary. It stops a visitor, a student,
or a well-meaning helper from re-tuning the pit EQ or rewriting the LED presets
while nobody is watching. The credential lives in the SQLite file on the pit
machine's disk; anyone with that file can replace the row. **Never reuse a
password that protects anything else.**

The hash is still done properly — PBKDF2-HMAC-SHA256, per-credential random
salt, 200k iterations, constant-time comparison — because storing a plain
password would be worse for no benefit, and because a shared team password
tends to get reused no matter how loudly you tell people not to.

## Lock lifecycle

The lock is **session-only and deliberately impatient**: it lives in memory,
never persists, and closing the admin panel re-locks immediately. Re-opening
the panel always demands the password again. There is no "stay unlocked"
option, on purpose — the pit display runs unattended for hours.
"""

import hashlib
import hmac
import os

from PyQt6.QtCore import QObject, pyqtSignal

from app.db import db
from app.lazy_proxy import LazyProxy

ALGO = "pbkdf2_sha256"
ITERATIONS = 200_000
SALT_BYTES = 16

# Shipped credential. The panel nags until it is changed.
DEFAULT_PASSWORD = "Password"

MIN_PASSWORD_LENGTH = 4


def _hash(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    ).hex()


class _AdminService(QObject):

    # Emitted whenever the lock opens or closes. Panels show/hide on this.
    lock_state_changed = pyqtSignal(bool)

    # Emitted after a successful password change
    password_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._unlocked = False
        self._ensure_seeded()

    # ── Seeding ───────────────────────────────────────────────────────────

    def _ensure_seeded(self) -> None:
        """Write the default credential on first run. Never overwrites."""
        row = db.fetchone("SELECT id FROM admin_credential WHERE id = 1")
        if row is not None:
            return
        salt = os.urandom(SALT_BYTES)
        with db.transaction():
            db.execute(
                """INSERT INTO admin_credential
                       (id, algo, iterations, salt, hash, is_default)
                   VALUES (1, ?, ?, ?, ?, 1)""",
                (ALGO, ITERATIONS, salt.hex(), _hash(DEFAULT_PASSWORD, salt)),
            )

    # ── State ─────────────────────────────────────────────────────────────

    @property
    def unlocked(self) -> bool:
        return self._unlocked

    @property
    def is_default_password(self) -> bool:
        """True while the shipped password is still in place — worth nagging about."""
        row = db.fetchone("SELECT is_default FROM admin_credential WHERE id = 1")
        return bool(row["is_default"]) if row else True

    # ── Verify / unlock ───────────────────────────────────────────────────

    def verify(self, password: str) -> bool:
        row = db.fetchone(
            "SELECT algo, iterations, salt, hash FROM admin_credential WHERE id = 1"
        )
        if row is None or row["algo"] != ALGO:
            return False
        candidate = _hash(password, bytes.fromhex(row["salt"]), row["iterations"])
        # compare_digest, not ==, so a wrong password does not leak how much of
        # it was right through timing.
        return hmac.compare_digest(candidate, row["hash"])

    def unlock(self, password: str) -> bool:
        if not self.verify(password):
            return False
        if not self._unlocked:
            self._unlocked = True
            self.lock_state_changed.emit(True)
        return True

    def lock(self) -> None:
        if self._unlocked:
            self._unlocked = False
            self.lock_state_changed.emit(False)

    # ── Change password ───────────────────────────────────────────────────

    def change_password(self, current: str, new: str) -> tuple[bool, str]:
        """
        Returns (ok, message). The message is shown to the operator verbatim,
        so it says what went wrong and how to fix it.
        """
        if not self._unlocked:
            return False, "Unlock the admin panel first."
        if not self.verify(current):
            return False, "Current password is not correct."
        new = new.strip()
        if len(new) < MIN_PASSWORD_LENGTH:
            return False, f"New password must be at least {MIN_PASSWORD_LENGTH} characters."
        if new == current:
            return False, "New password is the same as the current one."

        salt = os.urandom(SALT_BYTES)
        with db.transaction():
            db.execute(
                """UPDATE admin_credential
                      SET algo = ?, iterations = ?, salt = ?, hash = ?,
                          is_default = ?, updated_at = datetime('now')
                    WHERE id = 1""",
                (ALGO, ITERATIONS, salt.hex(), _hash(new, salt),
                 1 if new == DEFAULT_PASSWORD else 0),
            )
        self.password_changed.emit()
        return True, "Password changed."

    def reset_to_default(self) -> None:
        """Escape hatch for a forgotten password — see the note in ADMIN docs."""
        salt = os.urandom(SALT_BYTES)
        with db.transaction():
            db.execute(
                """UPDATE admin_credential
                      SET algo = ?, iterations = ?, salt = ?, hash = ?,
                          is_default = 1, updated_at = datetime('now')
                    WHERE id = 1""",
                (ALGO, ITERATIONS, salt.hex(), _hash(DEFAULT_PASSWORD, salt)),
            )
        self.password_changed.emit()


admin: _AdminService = LazyProxy("admin", "init_admin")  # type: ignore[assignment]


def init_admin() -> _AdminService:
    """Call once in main(), after init_db()."""
    real = _AdminService()
    admin._install(real)
    return real
