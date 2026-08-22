# Adding media to the Pit Display

Everything the display shows comes from a folder on the pit machine. Nothing is
downloaded at an event — the venue Wi-Fi is not trustworthy, so **every file has
to be on the machine before you leave**.

There are four kinds of media. Each has its own folder and its own rules.

| What | Where it goes | Who can change it at an event |
|---|---|---|
| [Music](#1-music) | any folder you pick | Any operator |
| [Judges slides](#2-judges-slides) | `assets/judges_slides/` | Any operator |
| [Robot CAD](#3-robot-cad) | uploaded through the app | Any operator |
| [Robot logs](#5-robot-logs) | imported through the app | Any operator |
| [Fonts](#4-fonts) | `assets/fonts/` | Developer only |

---

## 1. Music

### Before the event

**The team pre-curates this folder.** There is no explicit-content filter in the
app and no per-track blocking — whatever is in the folder can play, in front of
judges, sponsors, and other teams' parents. Curating happens once, here, not
during a match.

1. Make a folder anywhere on the pit machine. Something obvious:
   `C:\PitDisplay\Music` on Windows, `~/PitDisplay/Music` on a Mac.
2. Copy audio files in. Sub-folders are fine — the scanner walks them.
3. **Play the folder through once** before the event. This is the curation step,
   and it is the only one that catches a track nobody remembered adding.

**Formats:** `.mp3` `.m4a` `.aac` `.flac` `.ogg` `.opus` `.wav` `.wma`.
Anything else in the folder is ignored, so a stray PDF or cover image is
harmless.

**Titles come from the file's tags**, falling back to the filename when a file
has none. If the library shows `track07` instead of a real name, the file has no
tags — fix them in any tag editor, or rename the file, then re-scan.

### Loading it into the app

**Control Screen → Pit Systems → Music → Add folder…**

Pick the folder. The count under the button tells you what happened
("Scanned 143 file(s), 143 new").

### Re-scanning

Scanning again is always safe:

- Files already indexed are **left alone** — playlists and queues keep working.
- New files are **added**.
- Files you deleted are **hidden**, not erased from the database, so anything
  that referenced them does not break.

So the normal workflow is just: drop new songs in the folder, hit **Add folder…**
again, pick the same folder.

### Playing it

- **Double-click** a track — queues the whole visible list and starts there, so
  the rest keeps playing afterwards.
- **Play all** — queues everything currently listed (respects the search box).
- **Add to queue** — appends the selected tracks.
- **Search** filters by title, artist, or album.

### Volume, and the two controls that matter at an event

- **Volume cap** is the real ceiling. The volume slider cannot go above it. Set
  it once at the start of the event, at the level you would be comfortable with
  if an event volunteer walked past, and then forget it.
- **Duck for a visitor** drops to 20% in one click, for when a judge or a queuer
  walks up mid-song. Click again to restore.

**Judges mode ducks the audio automatically.** You do not have to remember.

### Requirements

Music playback needs the **VLC runtime** on the pit machine. Install VLC from
[videolan.org](https://www.videolan.org/) — the normal desktop install is enough,
you do not have to use it. Without it the app still runs and the Music panel
explains what is missing, but nothing will play.

---

## 2. Judges slides

Drop numbered PNG or JPG files into `assets/judges_slides/`:

```
assets/judges_slides/
  01_intro.png
  02_robot.png
  03_outreach.png
```

**Files are sorted alphabetically**, which is why the numbers matter — and why
they need leading zeros. `10_x.png` sorts before `9_x.png`; `09_x.png` does not.

Click **Reload** on the control screen to pick up changes without restarting.

Design them at the presentation screen's resolution so text stays crisp.

---

## 3. Robot CAD

Export a `.glb` from Onshape, then **Control Screen → Project → CAD Viewer
Config → Upload Model (.glb)**. Subsystem names must match the Onshape
sub-assembly names exactly, including case.

The full season workflow — export settings, camera presets, subsystem facts —
is in [`CAD_GUIDE.md`](CAD_GUIDE.md).

---

## 4. Fonts

`assets/fonts/*.ttf`, loaded automatically at startup. Currently Chakra Petch
(display), Roboto (body), and JetBrains Mono (numbers).

This is a developer task, not an event task — the brand specifies these three
faces and swapping them makes the display off-brand. See `CLAUDE.md`.

---

## 5. Robot logs

Telemetry pulled off the robot as a Phoenix 6 **"detailed" text export** (`.txt`).

**Control Screen → Pit Systems → Robot Logs → Choose log file…**

A 3.8 GB export takes about a minute and the app stays usable while it runs. The
importer keeps only the values that actually changed, so that 3.8 GB becomes
about 83 MB on disk without losing anything you can ask for.

### Name the CAN ids — do this once

A log only ever says *"TalonFX 11"*. It has no idea that is the front-left drive
motor, and it never will. The **CAN ID → name** table on that panel is where you
type the English names.

- Rows appear on their own the first time a device shows up in an import.
- Type the name and subsystem straight into the table; it saves as you go.
- **Names are per robot, not per log.** They persist across every future import
  and survive deleting the session that created them.
- Until a device is named, screens fall back to showing `TalonFX 11`.

The counter in the corner tells you how many are still unnamed. Get it to zero
once and you never think about it again that season.

### Keeping the file size down

Imported telemetry lives in its own file, `data/pit_display_samples.db`, apart
from the app's settings. If it grows too large, delete a session from the panel
(admin only) or delete that whole file when the app is closed — the app rebuilds
it empty on the next start and your CAN names are untouched.

**Archive the original exports.** They are the only way to re-import a session
you have deleted.

---

## Pre-event checklist

- [ ] Music folder curated and **listened to end to end**
- [ ] VLC installed on the pit machine
- [ ] Music folder scanned in the app; track count looks right
- [ ] Volume cap set
- [ ] Judges slides in `assets/judges_slides/`, numbered with leading zeros
- [ ] Slides **Reload**ed and clicked through
- [ ] Robot `.glb` uploaded, subsystems named and checked
- [ ] Robot log imported and **every CAN id named** (Robot Logs panel shows "all named")
- [ ] Admin password changed from the shipped default (see `ADMIN_GUIDE.md`)
- [ ] Whole thing run **unplugged from the internet** for ten minutes to prove
      nothing quietly depends on it
