# Operator guide

Everything the pit crew does with the app, from first setup to the morning of an
event. Installing and updating the app itself: [`DEPLOYMENT.md`](DEPLOYMENT.md).

**Nothing is downloaded at an event.** Venue wifi can't be trusted. Every file
the display shows has to be on the machine before you leave. The one exception
is the live Nexus feed, which needs internet (the team carries a hotspot).

---

## The control screen

Only the control screen opens at launch. Every other screen has a **power
switch** in the sidebar: on builds it, off destroys it. Turning a screen off and
on again genuinely resets it.

| Screen | Where | What it shows |
|---|---|---|
| Presentation A / B | the two overhead panels | the slide rotation, or a pinned board (Next Match, checklist, diagnostics) |
| Project | the pit-front touch panel | the CAD model and the team's story |

**Three modes** in the top bar apply to the whole pit. **Standard** rotates slides.
**Judges** shows the judges deck, ducks the music and turns the strips white.
**Lunch** shows the holding card and turns the strips red. Judges and Lunch
light up red, because they're the exceptional states.

**Each screen's settings:** *Display* and *Fill the display* choose its monitor.
With one monitor, screens stay windowed, so a full-screen panel can never bury
the control screen. *Standard content* pins a board instead of the rotation.
*Where this screen appears* is **Monitor** or **Network** (see
[Screens over Ethernet](#screens-over-ethernet)).

---

## The admin lock

**Click the Breakaway mark (top left)** to open the admin bar, then type the
password. Gated controls appear; closing the bar re-locks immediately, every
time. The unlock is never remembered. That's deliberate for a display that runs
unattended in a room full of strangers.

| Admin only (hidden until unlocked) | Always available |
|---|---|
| LED brightness, speed, looks, colour, save-as-default | **LED on/off**: the kill switch |
| The whole equaliser | Music transport, volume, volume cap, duck |
| Nexus keys, setup-file import/export, update token | Event key, polling, refresh |
| Deleting a robot-log session or a checklist | Importing logs, naming CAN ids, ticking checklists |

**The shipped password is `Password`. Change it before the first event:**
unlock, then *Change password*. The bar nags until you do.

Forgotten it? There's no reset in the UI (that would defeat the lock). From a
terminal in a checkout:

```bash
uv run python -c "
import app.db.migrations
from app.db import init_db; from app.admin import init_admin
from PyQt6.QtWidgets import QApplication; QApplication([])
init_db(); init_admin().reset_to_default(); print('reset to: Password')"
```

This is a UI lock, not a security boundary: the credential lives in the local
database. Don't reuse a password that protects anything else.

---

## Music

**Control → Pit Systems → Music → Add folder…**, pointed at the team's audio
folder. The library indexes files where they are; nothing is copied. Re-scanning
is always safe: new files are added and deleted ones are hidden, not erased.

- **Curate the folder beforehand and play it through once.** There's no
  content filter. Whatever is in the folder can play in front of judges.
- Formats: mp3, m4a, aac, flac, ogg, opus, wav, wma. Titles come from tags, then
  the filename.
- **Volume cap** is the real ceiling. Set it once at the start of the event.
- **Duck for a visitor** drops to 20%. Judges mode ducks automatically.
- The VLC runtime is inside the app. You don't install VLC.

**The equaliser (admin).** Ten bands plus preamp, drawn as the response
curve. Press anywhere to move the nearest band. Presets: *Flat*, *Pit Default*,
*Crowded*, *Judges Visiting*, *Lunch*. Before touching it, **aim the speakers
down into the pit**. Then the two moves that matter are a high-pass around
100–120 Hz (pit boom) and a broad cut at 200–400 Hz (mud); every preset does
both. The optional live band display shows what the speakers are actually
getting.

---

## LED strips

**Control → Pit Systems → LED Strips.** The controller is found by USB
automatically. Replugging is fine.

- **The three-way switch on the box:** up = white work light, middle = the app
  is in charge, down = solid red. Up and down work with no computer at all.
- **Resting look** is the white work light. Queue calls flash the sides in our
  alliance colour; passing inspection flashes them green.
- **The violet sparkle** means the controller has power but no app talking to
  it. That's normal at boot, and five seconds after the app closes.
- **On/off** is the kill switch and needs no password.

Hardware, wiring and firmware details are in [`CLAUDE.md`](CLAUDE.md), "LED
strips".

---

## Judges slides

Numbered PNG/JPG files, sorted alphabetically, so use leading zeros
(`01_intro.png`, `02_robot.png`, … `10_outreach.png`). They go in the
`assets/judges_slides/` folder **inside the data directory**:

| | |
|---|---|
| Windows pit machine | `%LOCALAPPDATA%\Breakaway Pit Display\assets\judges_slides\` |
| A checkout (the Mac) | `assets/judges_slides/` in the repo |

Then **Reload** on the control screen's slide picker. Design them at 1920×1080.
In Judges mode they're shown full-bleed.

---

## The robot CAD

Once a season:

1. Onshape → top-level assembly → **Export** → **GLTF**, *single file (.glb)*,
   keeping the hierarchy. Choose **coarse/medium tessellation**; fine makes a
   file hundreds of MB that loads and orbits slowly.
2. **Control → Project → CAD Viewer Config → Upload Model (.glb)**. It's stored
   in the data directory, so updates keep it.
3. Robot lying on its side? Set **Model up axis** (usually *Z up*) and **Save
   Config**. It applies instantly.
4. **+ Add Subsystem** for each mechanism: display name, the **exact** Onshape
   sub-assembly name (case-sensitive), an accent colour, one fact per line, and
   optionally a camera preset (azimuth 0 = front / 90 = right, elevation,
   distance factor, 2.2 by default). **Save Config.**

**Visitors** drag to tumble (it pivots on the point they grab), pinch to zoom,
two-finger drag to pan, and tap a part to isolate its subsystem.

**Judges mode:** Control → Presentation A or B → **Judges CAD** → pick a
subsystem. Both overhead screens fly to it. *↩ Full View* returns to the whole
robot, *◀ Back to Slides* to the deck.

Troubleshooting: a subsystem that won't isolate has a node name that doesn't
match Onshape exactly.

---

## Robot logs

**Control → Pit Systems → Telemetry → Robot telemetry → Choose log file…**

| File | Where it comes from |
|---|---|
| `.hoot` | straight off the CTRE controller: **the one to use** (extracted with owlet first) |
| `.wpilog` | the roboRIO's own log: the team's application signals, PDH currents |
| `.txt` | an older Phoenix "detailed" export |

The app stays usable while it imports (a 3.8 GB export takes about a minute).
It refuses a log it has already imported, before spending time on it.

**Name the CAN ids, once per robot.** A log only ever says "TalonFX 11". Type
the English name and subsystem into the **CAN ID → name** table. Names persist
across every import. Get the unnamed count to zero and you never think about it
again that season.

Imported telemetry lives in its own file (`pit_display_samples.db`) and is
disposable. Delete a session (admin) or the whole file while the app is closed;
CAN names are untouched. **Keep the original log files**, since they're the only
way to re-import.

---

## Checklists

Control → Presentation A or B → **Standard content → Checklist** puts a list on
that overhead screen. Write and tick items from the same panel. Ticking is
always done on the control screen, never on the display. Each screen can show a
different list.

---

## The event feed (Nexus)

Live queuing from [frc.nexus](https://frc.nexus): which match is queuing, when
ours is on deck, our pit, inspection, and alliances. It drives the **Next Match**
board, the queue banner on both overhead screens, and the LED queue flash.

**Setup, once per machine:** Control → Pit Systems → **Event Feed**.

1. Give it the **relay token**, the only credential a pit machine needs. The
   easy way is a setup file made on the dev Mac ([`DEPLOYMENT.md`](DEPLOYMENT.md#b--install-on-a-new-pit-machine));
   or unlock admin and paste it under *Nexus access*.
2. Type the **event key** (the code on frc.events), or press *List events* and pick
   it. Any event works immediately.
3. The dot goes green. The relay pushes every change the moment Nexus sends it.

Nobody logs in to anything at the event. The relay at `nexus.bh-stack.com`
receives Nexus's webhooks for every event all season. How it works and how to
maintain it: [`NEXUS.md`](NEXUS.md).

**If it's not green:** Control → Pit Systems → **Telemetry → Event relay**
says why, in words. If the relay can't be reached for a minute, the app polls
by itself: through the relay's plain HTTPS route first, then frc.nexus directly
if an API key was also pasted.

---

## Screens over Ethernet

The overhead panels can be driven by a Raspberry Pi on the pit switch instead of
an HDMI run. The pit machine then needs no video output for them.

1. Control → Presentation A (or B) → **Where this screen appears → Network**,
   and switch the screen on in the sidebar. The panel shows the address with a
   **Copy** button.
2. Windows asks about the firewall the first time: **allow on Private
   networks**.
3. On the Pi, open that address in Chromium, full screen:

```bash
# ~/.config/lxsession/LXDE-pi/autostart, or any startup script: wait for the
# pit machine, then open the screen.
until curl -sf http://<pit machine>:3938/health >/dev/null; do sleep 2; done
chromium-browser --kiosk --noerrdialogs --disable-infobars \
  http://<pit machine>:3938/screen/presentation_a
```

Pages are on port **3938** (the team number plus one), live data on **3939**.
A display reconnects on its own after a restart. A screen that's switched off
or unreachable shows one branded card, *"Breakaway welcomes you to our pit"*.
**Keep this on the pit-local switch, never event wifi.** One limit: the CAD
viewer in Judges mode can't be shown over the network.

**Telemetry** lists every connected display, its address, uptime and drops.

---

## When something looks wrong

**Control → Pit Systems → Telemetry** is the first place to look. It covers the
event relay, which route event data came through, every networked display, the
LED controller, recent drops, and robot logs.

For the whole app, run `--self-check` (see [`DEPLOYMENT.md`](DEPLOYMENT.md)).
It writes `selfcheck.log` in the data directory, which is the file to send.

---

## Pre-event checklist

Do this at the shop, the week before. For Ozark Mountain Brawl, set the event
key the night before.

- [ ] **Smart App Control off** (`--self-check` has no `appcontrol` warning)
- [ ] App on the latest release; **automatic update checks turned off** for the event
- [ ] `--self-check` passes on the pit machine
- [ ] Admin password changed from `Password`
- [ ] Music folder curated, **played end to end**, scanned; volume cap set
- [ ] Judges slides in place, numbered with leading zeros, reloaded and clicked through
- [ ] Robot `.glb` uploaded; every subsystem isolates
- [ ] Latest robot log imported; **every CAN id named**
- [ ] Relay token on the machine; event key set; Event Feed dot green
- [ ] Each screen on its monitor (or its Pi), filling it
- [ ] LED box switch in the middle; strips follow the app
- [ ] Whole setup run **with the internet unplugged** for ten minutes: everything
      but the event feed must keep working
