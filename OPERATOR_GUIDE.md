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
- **Violet breathing on the sides, steady white down the centre** means the
  controller has power but no app talking to it. That's normal at boot, and
  five seconds after the app closes.
- **The centre run is the work light.** In every animated look it holds
  steady white and only the sides move. The centre's supply sags with the
  music's bass, and anything changing on the centre flickers while the amp is
  loud. **Don't put a colour down the centre while music is playing**: a
  solid colour look still colours it, and it will flicker.
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

**Import a folder** (Telemetry → Robot logs) is the fast way off the robot:
pick the folder (the whole USB stick, or the roboRIO's log dump) and every
`.hoot` and `.wpilog` in it, and in every folder inside it, is copied onto
this machine first. When the status line says "The drive can come out", it
can. Logs are recognised by what's in them, not their names: one imported
before (from any drive, folder or pit) is listed in a pop-up as **Same file**
(identical) or **Same recording** (a longer or shorter copy; a longer one is
ticked for you). Tick any to import again (that replaces the earlier
session; admin only), or leave them unticked to skip. The rest import newest
first while you keep working; **Stop** stops after the current file, and
running the same folder again picks up where it left off. Each copy is
deleted from this machine as soon as it's in the database (the drive itself
is never touched); a log that failed, or a different copy you didn't import,
stays in the `robot_logs` folder beside the database.

**Control → Pit Systems → Telemetry → Robot telemetry → Choose log file…**

| File | Where it comes from |
|---|---|
| `.hoot` | straight off the CTRE controller: **the one to use** (extracted with owlet first) |
| `.wpilog` | the roboRIO's own log: the team's application signals, PDH currents |

The app stays usable while it imports (a multi-gigabyte log takes a minute or more).
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

## Analysis

**Control → Pit Systems → Analysis**

A model on this machine reads each newly imported log and writes the crew a
board: what to look at before the next match. It takes a few minutes; the
LED strips **flash purple** when a board is ready. Nothing leaves the pit to
do it. Every figure on a board is checked against the log before it's shown;
a run that invents a number is thrown out, and the panel says why.

The engine is built into the app; it needs its model once: **Download the
model (5.0 GB)** on the panel, on a good connection, before the event. It
resumes if interrupted and stays through every update. The top line says what
it's doing or what's missing.

**Put a board on an overhead screen:** pick the screen in the sidebar →
Content → **Analysis board**. It shows the newest board, with its charts, and
updates by itself when the next one is ready.

**The scoreboard** at the top shows, for each model, what share of rated
findings the crew called useful or wrong, how many it acted on, and the mean
rank. That's how to tell whether a change made it better.

**Rate what it found.** Under each run, mark every finding **Useful**,
**Not useful** or **Wrong**, tap **Acted on** if the crew did something
because of it, and rank the run **1–5**. Tap again to undo. Ratings sync to
every machine, so they can be done later from anywhere. **"All clear" is a
useful finding when it's right**: rate what saved the crew time, not what
found the most problems.

---

## Screen wording

Control → Pit Systems → **Screen Wording** (unlock with the Breakaway mark
first) changes what the pit-front panel and the overhead slides say, with no
new build. Pick a part of a screen, edit, and press **Save to every screen**:
the screens change straight away, and every other pit gets the edit with team
sync.

- Each box shows how many characters it holds against its limit. The limit
  keeps the words inside their place on screen; Save refuses while one is over.
- **Shipped text** puts a box back to what the build came with. Emptying a box
  does the same.
- "Tap-for-more text" is what opens when a visitor taps a card or the red Act
  472 plate.
- Only words change here. Adding a new card or slide, or moving one, is
  still a code change.

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

## Team sync

Every pit machine shares the team's settings and robot logs through
`sync.bh-stack.com`, and a copy of everything is kept on the team's server at
home. Checklists, EQ presets, CAN names, the admin password, the event key,
judges slides, the CAD model and imported logs all follow automatically,
about once a minute and a few seconds after any change. **Checklist ticks
don't**: each pit ticks its own. Neither do the music library, which screens
are on which monitor, or anything on the Pi cabling.

**Setup:** the pit setup file carries the sync token (made on the dev Mac,
as for the relay token), or paste it under **Telemetry → Team sync → This
machine** (admin). With no token the machine works exactly as before and
keeps everything to itself. The same place renames the machine (what the
other pits and home call it), turns sync off, and picks what it downloads.

**Big files move at night.** CAD, judges slides and music copy between
machines only from 00:00 to 05:00 (laptops stay on at an event, and the signal
is best then); everything else syncs all day. Telemetry → Team sync shows how
many files are waiting. On good venue or hotel Wi-Fi, an admin can press
**Sync files now** to move them at once. Every song any machine scans is
shared with the team; **Team music** turns that off for one machine.

**Deleting a song for the team** (Music → select it → *Delete for the team…*,
admin) hides it on every machine. The file stays until the deletion is
approved at home.

**The two overhead screens are one set.** Each screen's settings have a
"Both screens show" box: pick Next match, and one screen shows the queue while
the other shows the event's schedule with our matches marked (and our record
once results arrive); pick Robot diagnostics, and one shows diagnostics, the
other robot info. On Checklists, choose a list for each screen. Power, theme
and which monitor stay separate for each screen.

**"Did you know?"** is one of the things an overhead screen can show (pick it
in that screen's content list): Breakaway's award history and records from
across the league, sent from home. It turns to the next facts every 12 seconds
when there are more than fit.

**Every morning home checks each machine** against the master copy:
Telemetry → Team sync says "In sync with home" or lists what's missing.

**Machine id** is how the hub tells machines apart. Changing it (admin, with a
warning) is for giving a machine an id you chose or taking back a reinstalled
machine's old one. **Never give two running machines the same id**: the hub
can't see their conflicts, so they silently overwrite each other.

**If two machines change the same thing** before either syncs, the first to
reach the hub wins and the other shows the change it lost under **Telemetry →
Team sync**. Make the edit again if it mattered.

**Without internet** everything keeps working; changes wait and go out when
the machine is back online. Telemetry → Team sync says what's waiting.

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

For the LEDs it shows two halves: what this machine measures (when the
controller last answered, round-trip time, how long commands wait before
they're sent) and, with controller firmware 2.5 or newer, what the controller
reports about itself (strip-write time, command-to-visible latency, receive
errors, the live switch position, reboots). Rejected frames or a controller gone silent almost
always mean the USB cable or port: swap it. It also shows the box's switch
position once the switch has been moved since connecting.

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
- [ ] Sync token on the machine; **Telemetry → Team sync** says "In step", and
      every other pit machine is listed as seen recently
- [ ] Each screen on its monitor (or its Pi), filling it
- [ ] LED box switch in the middle; strips follow the app
- [ ] Whole setup run **with the internet unplugged** for ten minutes: everything
      but the event feed must keep working
