# Deploying the Pit Display

How the app gets from this repo onto a machine at an event.

The short version: **`uv run tools/build_app.py --zip`** produces
`dist/Breakaway Pit Display/` — one folder, copy it to the target machine, run
the executable inside it. Everything the app needs is in there except the music.

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

About **1.4 GB installed**, ~750 MB zipped. Roughly a quarter of that is the
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
   --tags`) or run the *Build app* workflow by hand. It builds Windows and
   macOS, self-checks each packaged app, and attaches the zips as artifacts.
   CI builds `--no-model` because the model is not in git.

[uv]: https://docs.astral.sh/uv/

---

## Installing on the pit machine

1. Copy the folder anywhere the operator can write to, or `C:\Program Files\`.
   Either works — the app never writes inside its own folder.
2. Run the executable once with `--self-check` (below) and read the output.
3. Start it normally. Make a shortcut; on Windows put it in
   `shell:startup` if the pit machine should come up into the display.
4. Point it at the music folder: **Control → Music → Add folder**.
5. Assign each screen a monitor: **Control → (screen) → Display / Fill the
   display**. With more than one monitor the audience screens default to
   filling their own; with one monitor they stay windowed so the control panel
   can never end up buried.

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

### Upgrading

Replace the folder. The data directory is untouched, so the machine keeps
everything it has accumulated. To start clean, delete the data directory.

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

[VLC]: https://www.videolan.org/vlc/

---

## Known limits

- **Not code-signed.** Windows SmartScreen will warn on first run ("More info →
  Run anyway"); macOS Gatekeeper will need Right-click → Open, or
  `xattr -dr com.apple.quarantine "Breakaway Pit Display.app"`. Signing needs
  certificates the team would have to buy.
- **One architecture per build.** A build made on Apple Silicon runs on Apple
  Silicon. Build on the architecture you are shipping to.
- **The LED controller is USB serial**, discovered by VID/PID at runtime — it
  needs no driver on Windows 10/11, and nothing about it is baked into the
  package.
