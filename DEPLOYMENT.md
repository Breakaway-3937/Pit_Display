# Deploying the Pit Display

How the app gets from this repo onto a machine at an event, and how it stays
current afterwards.

The short version, in the order you will actually want them:

| | |
|---|---|
| **Ship an update** | `git tag v1.4.2 && git push --tags`. CI builds both platforms, self-checks each packaged app, and publishes a release. Pit machines take it themselves within six hours. |
| **Set up a new machine** | Download `…-Setup.exe` from the release page onto the pit machine and double-click it. One file, no administrator, done. |
| **Build by hand** | `uv run tools/build_app.py --zip` → `dist/Breakaway Pit Display/`. Add `--installer` on Windows for the Setup.exe. |

Everything the app needs is in the package except the music.

---

## What is in the package

| | |
|---|---|
| The app | all of `app/`, `main.py`, and a Python runtime |
| Qt | PyQt6 plus QtWebEngine (Chromium) for the CAD viewer |
| Fonts | Chakra Petch, Roboto, JetBrains Mono — the app never relies on the machine having them |
| CAD | the Three.js viewer, `subsystems.json`, and the season's `robot.glb` |
| Robot logs | the `owlet` extractors for **both** Windows and macOS |
| Database | `data/pit_display.db` as a **seed** — settings, presets, the CAN-id name map, imported-log metadata |

**Not in the package: the music library.** The library is an index of file paths
in the database; the audio itself stays on the pit machine. Shipping a few
gigabytes of audio inside an app that then can't be updated without a rebuild is
the wrong shape. After installing, point the app at the folder from
**Control → Music → Add folder**.

Also not included: `data/pit_display_samples.db`. That is the bulk telemetry —
80 MB and growing, disposable by design, and rebuilt by re-importing a log.

### Size

About **1.4 GB installed**, ~750 MB as the installer or the zip. Roughly a quarter of that is the
CAD model and most of the rest is Chromium. `--no-model` drops ~340 MB; the app
then runs with no robot on screen until somebody uploads one from
Control → Project → CAD Viewer Config.

---

## Building

```bash
uv sync --group build            # PyInstaller, once
uv run tools/build_app.py        # → dist/Breakaway Pit Display/
uv run tools/build_app.py --zip  # …and a zip beside it
uv run tools/build_app.py --no-model --clean
```

The build refuses to start if the seed database or the owlet binaries are
missing, rather than producing a package that is quietly broken.

### Windows

**PyInstaller does not cross-compile.** A Windows executable must be built on
Windows. Two ways:

1. **On a Windows machine** — install [uv], clone, then the commands above.
   The result is `dist\Breakaway Pit Display\Breakaway Pit Display.exe`.
2. **In CI, with no Windows machine** — push a tag (`git tag v1.0 && git push
   --tags`) or run the *Build app* workflow by hand. It builds, self-checks the
   packaged app, compiles the installer, and — for a tag — publishes a GitHub
   Release the installed apps find by themselves. A manual run stops at
   artifacts and releases nothing, which is how you find out a change broke
   packaging without spending a version number on it.
   CI builds `--no-model` because the model is not in git. The model lives in
   the *data* directory, so a machine that has one keeps it through every
   update.

**CI is Windows-only, deliberately.** Every pit machine is Windows, the Mac runs
from a checkout and never installs a build, and a macOS runner bills at *ten
times* the Linux rate against a private repo's monthly minutes. Building both
put one release at ~180 billed minutes out of 2,000/month; this is ~30. `uv run
tools/build_app.py` still works fine on the Mac if you want a local copy.

**Nobody is ever charged by surprise.** The default spending limit on a free
account is $0, so exceeding the monthly minutes means jobs stop starting until
the month rolls over — not a bill.

**The version is stamped from the tag, never edited by hand.**
`tools/stamp_version.py` writes it into `app/version.py` between checkout and
build, so what a pit machine reports is the tag that produced it. A build
nobody stamped reports `0.0.0+dev`, which `version.is_release()` refuses — so a
hand-built or dispatch-built app can never be mistaken for a release, and will
never try to update itself.

[uv]: https://docs.astral.sh/uv/

---

## Installing on the pit machine

On the pit machine, open the repo's **Releases** page in a browser, sign in to
GitHub, and download **`Breakaway-Pit-Display-<version>-Setup.exe`**. Double-click
it.

That is the whole install — one file, the same as any application off the
internet. It asks two things (start with Windows? a token for updates?), and
puts the app in the versioned layout below with a Start Menu entry and an
Add/Remove Programs entry.

**No administrator, at install or at update time.** Everything is per-user,
under `%LOCALAPPDATA%`.

Windows SmartScreen will warn on first run, because the installer is not
code-signed: **More info → Run anyway**. It says that once, per machine.

Two files on the release page, and only one is for you:

| | |
|---|---|
| `…-Setup.exe` | **This one.** What a person downloads and runs |
| `…-windows.zip` | Not for people — what an installed app downloads when it updates itself |

Then, once, by hand:

1. Point it at the music folder: **Control → Music → Add folder**.
2. Assign each screen a monitor: **Control → (screen) → Display / Fill the
   display**. With more than one monitor the audience screens default to
   filling their own; with one monitor they stay windowed so the control panel
   can never end up buried.
3. Upload the season's CAD model: **Control → Project → CAD Viewer Config**.
   CI builds without it, and it lives in the data directory from then on.

### The layout, and why

```
%LOCALAPPDATA%\Programs\Breakaway Pit Display\
    pointer.json              which version is current, and what preceded it
    current  ─────────────▶   versions\1.4.2        (a directory junction)
    versions\
        1.4.1\                the previous one, kept for rollback
        1.4.2\Breakaway Pit Display.exe
```

Every shortcut points at `current\Breakaway Pit Display.exe`. Windows resolves
the junction at launch, so the running process holds handles on
`versions\1.4.2\` and the junction itself is locked by nothing — **repointing
it while the app is running is safe**, and the new version is simply what the
next launch gets. That is the whole trick: Windows will not let you overwrite a
running `.exe`, so nothing ever tries to.

A copy somebody unzipped onto the desktop instead is not this shape, is
detected as such, and is left completely alone — it just cannot update itself.
Running the Setup.exe converts it, and touches no data.

**Uninstalling** is the normal Add/Remove Programs entry. It removes every
version and the shortcuts, and *asks* whether to delete this machine's data —
the database, checklists, CAN-id names, imported logs and uploaded CAD model —
defaulting to no, because the usual reason to uninstall is to reinstall.

### Where its data lives

Not in the install folder — that is read-only on Windows under `Program Files`,
and is replaced wholesale on every upgrade.

| | |
|---|---|
| Windows | `%LOCALAPPDATA%\Breakaway Pit Display\` |
| macOS | `~/Library/Application Support/Breakaway Pit Display/` |
| Linux | `~/.local/share/breakaway-pit-display/` |
| Anywhere | `PIT_DISPLAY_DATA=<path>` overrides it — a USB stick, a shared drive |

On first run the app copies its seed database there. **It never overwrites an
existing one**, so upgrading keeps the CAN-id names, the checklists, the EQ
presets and the imported-log history.

Two of the updater's own files live here rather than in the database, so they
survive an install *and* remain reachable when the database is the thing that
is broken: `update.json` (channel, auto-check, last result) and `update_token`
(the GitHub credential).

### Upgrading

**It updates itself.** Tag a release and the machine has it within six hours.
The rest of this section is what that actually does, and how to stop it.

The full sequence, from the Mac to the pit:

1. `git tag v1.4.2 && git push --tags`.
2. CI stamps the version, builds Windows, and runs `--self-check` on the
   *packaged* app. A build that does not boot on the runner never becomes a
   release.
3. It publishes a release carrying the installer, the update payload and a
   `manifest.json` naming the payload and its SHA-256.
4. Every pit machine checks the release feed on a six-hour timer and says, on
   **Control → Pit Systems → Software Updates**, that something newer exists.
   **It does not download it.** A 400 MB download that starts itself is a
   download that starts during a match cycle on an event's wifi.
5. Somebody presses *Download and install*. That runs in the background: the
   zip is fetched and checked against its SHA-256, unpacked into
   `versions\<new>`, and then — the part that matters — **the new build is put
   through its own `--self-check` on this machine** before it is allowed
   anywhere near the pointer. It runs against a scratch data directory, a spare
   CAD port and a fake LED link, so the app that is on screen in the pit
   notices nothing.
6. Only if that passes does `current` swing across. Nothing on any audience
   screen flickers; the new version is what the next launch runs.

So a broken build can be released, downloaded and unpacked and still never
become the app that opens tomorrow morning.

**Rolling back.** The previous version stays on disk until it is two updates
behind. *Roll back to the previous version* on the same panel repoints the
link, and if the app will not open at all:

```
"%LOCALAPPDATA%\Programs\Breakaway Pit Display\current\Breakaway Pit Display.exe" --rollback
```

**Do not update during an event.** Turn *Look for updates automatically* off
before you leave for a competition and back on when you get home. The whole
feature is for the week between events.

The data directory is untouched by any of this, so the machine keeps its
database, checklists, CAN-id names, imported logs, EQ presets and uploaded CAD
model across every update. To start clean, delete the data directory.

### Setting up updates

The repository is private, so a pit machine needs a token to see releases at
all — there is nothing to configure on a machine that will never update.

1. On GitHub: **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Resource owner **Breakaway-3937**, repository access **Only select
   repositories → Pit_Display**, permissions **Contents: Read-only**. Nothing
   else. Give it an expiry you will remember — a year is reasonable, and the
   panel says plainly when GitHub starts refusing it.
3. Paste it into the installer when it asks, or into **Control → Pit Systems →
   Software Updates** after unlocking the admin bar (click the Breakaway mark,
   top left).

It is stored as `update_token` in the data directory — beside the database, not
inside the app folder, so upgrading never loses it. It can read this one
repository and do nothing else whatsoever if it leaks off the machine.

**Channels.** `stable` takes tagged releases only and is where every pit
machine belongs. `beta` also takes prereleases — a tag with a `-` in it, like
`v1.4.2-beta.1` — which is how one machine can try a build before the rest of
the pit gets it.

---

## Verifying an install

```
"Breakaway Pit Display" --self-check
```

Builds all four windows offscreen, renders each one, tears them down, and
reports. Exit status 0 means everything critical passed, so it can be the last
line of an install script.

```
[PASS] paths        frozen / resources / data, and whether data is writable
[PASS] database     schema version, table count, sample count
[PASS] webengine    Chromium's helper process and resources are in the bundle
[PASS] cad          the local asset server answers; is there a model
[PASS] fonts        all three families actually loaded
[PASS] owlet        which extractor this platform will use
[PASS] audio        libVLC present
[PASS] updates      version, channel, install layout, whether the token works
[PASS] windows      all four built and rendered
```

**Warnings are not failures.** No VLC means no music and a working pit display;
no owlet means no `.hoot` import and a working pit display. Only things that
would leave an operator with nothing fail the run.

### Things it will tell you about

**`[WARN] audio — libVLC not available`**
Windows has no VLC runtime by default. Install [VLC] (64-bit, matching the
app) and music plus the equaliser come back. Everything else already works.

**`[WARN] owlet — No binary for this platform`**
`.hoot` import is dead on this machine; `.wpilog` and Phoenix `.txt` exports
still import. Put the right binary in `tools/owlet/` and rebuild —
see `tools/owlet/README.md`.

**`[FAIL] webengine — missing: helper process`**
A packaging fault, not a machine fault. PyInstaller drops Chromium's helper and
its resources on both platforms unless the spec places them by hand; see
`_webengine_support()` in `packaging/pit_display.spec`.

**`[FAIL] paths — data directory is NOT writable`**
A locked-down account or a redirected `%LOCALAPPDATA%`. Set `PIT_DISPLAY_DATA`
to somewhere the operator owns.

**`[WARN] updates — self-update is off: …`**
This machine will never take a release by itself. Four different causes, and
the line names which: it is a checkout, the build carries no stamped version,
it was unzipped by hand rather than installed, or there is no token. All four
look identical from the outside — nothing ever appears — which is why the check
exists. Run the Setup.exe for the third; add a token for the fourth.

[VLC]: https://www.videolan.org/vlc/

---

## Known limits

- **Not code-signed.** Windows SmartScreen warns the first time the installer
  runs ("More info → Run anyway"). Once installed, updates are downloaded by
  the app itself rather than by a browser, so no mark-of-the-web is attached
  and SmartScreen never asks again. A signing certificate is the fix and the
  team would have to buy one.
- **One architecture per build.** A build made on Apple Silicon runs on Apple
  Silicon. Build on the architecture you are shipping to.
- **The LED controller is USB serial**, discovered by VID/PID at runtime — it
  needs no driver on Windows 10/11, and nothing about it is baked into the
  package.
- **An update is a whole package, ~400 MB.** Almost all of it is Chromium and
  Qt, byte-identical between builds, but there is no delta mechanism — the
  download is the entire app every time. Fine at the shop; do not do it on
  event wifi. If it ever becomes painful the fix is a per-file SHA-256 manifest
  so only the changed files come down, which would typically be a few MB.
- **Two versions on disk is about 2 GB.** `install.prune()` keeps the current
  one and its predecessor and deletes the rest at startup, when the folder
  being replaced is no longer open.
