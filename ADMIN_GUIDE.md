# Admin lock

Some controls are hidden behind a password so that a visitor, a student, or a
helpful parent cannot re-tune the pit while nobody is watching. Once the LEDs
and the EQ are set for an event, they should stay set.

## What it gates

| Locked (admin only) | Always available |
|---|---|
| LED brightness, speed, looks, colour, follow toggles, save-as-default | **LED on/off** — the kill switch, in the panel and the sidebar |
| The whole **equaliser** — bands, preamp, presets, follow-mode | All music transport: play, pause, skip, queue, search, scan |
| Changing the admin password | Volume, volume cap, duck |

The reasoning behind the split: anything an event volunteer might legitimately
ask you to do *right now* stays unlocked. "Turn the lights off" and "turn it
down" must never need a password. Re-tuning the room does.

Gated controls are **hidden, not greyed out**. A disabled control just invites
someone to ask who has the password; an absent one is not a conversation.

## Using it

**The Breakaway mark in the top-left of the control screen is the door.** Click
it.

1. The admin bar drops down under the top bar. It is inline, not a pop-up, so
   you can watch the gated controls appear as you unlock.
2. Type the password, press Enter or click **Unlock**.
3. The LED tuning block and the equaliser appear in their panels.
4. Click **Lock & close**, the **✕**, or the Breakaway mark again to finish.

### The lock is deliberately impatient

**Closing the bar re-locks immediately, every time.** There is no "stay
unlocked" option and the unlock is never remembered — not across a panel close,
not across a restart. Re-opening always asks again.

That is on purpose: this display runs unattended for hours in a room full of
strangers. An admin session that outlives the person who opened it is worse than
no lock at all.

## Changing the password

Unlock first, then **Change password**. Enter the current password, the new one
twice, and **Save**.

Minimum four characters. The app refuses a new password identical to the current
one, and tells you exactly what went wrong when it refuses.

## The shipped password

The default is `Password`.

**Change it before your first event.** It is written here, in a file that ships
with the project — anybody who has seen this repository knows it. While the
default is still in place, the admin bar nags you every time you unlock.

## Forgotten the password?

There is no recovery flow in the UI, on purpose — a "reset" button in the
interface would defeat the lock. Reset it from a terminal on the pit machine:

```bash
uv run python -c "
import app.db.migrations
from app.db import init_db
from app.admin import init_admin
from PyQt6.QtWidgets import QApplication
QApplication([])
init_db()
init_admin().reset_to_default()
print('Admin password reset to: Password')
"
```

Then change it to something new straight away.

## What this is not

**This is a UI lock, not a security boundary.** The credential lives in
`data/pit_display.db` on the pit machine's disk, and anyone with that file can
replace the row — exactly as the command above does.

The hash itself is done properly: PBKDF2-HMAC-SHA256, a random per-credential
salt, 200,000 iterations, and a constant-time comparison. That is not because it
makes the lock strong, but because a shared team password gets reused elsewhere
no matter how firmly you ask people not to, and storing it in plain text would
turn a pit-display annoyance into somebody's real account.

**Do not use a password that protects anything else.**

## For developers

- `app/admin.py` — the `admin` singleton; `init_admin()` after `init_db()`
- `app/widgets/admin_bar.py` — the inline bar
- Migration `_v6_admin` — the `admin_credential` table (one row, `id = 1`)
- Gate a new control by subscribing to `admin.lock_state_changed(bool)` and
  hiding the widget, following `LEDPanel._apply_lock` / `MusicPanel._apply_lock`
