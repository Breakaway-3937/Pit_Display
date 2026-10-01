# Deployment

How a change on the Mac becomes the app on a Windows pit machine, and how a
new machine gets it. Using the app once it's installed:
[`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md).

```
git tag v1.4.2 ─▶ Actions: stamp · build · --self-check ─▶ Release (Setup.exe + zip + manifest.json)
                                                                     │
          pit machine: checks every 6 h, says so ─▶ someone presses Download ─▶ verify · unpack · --self-check · swap
```

---

## A · Ship a release

1. **Commit and push.** CI builds the tag, not your working directory.
2. **Pick a version:** fix → `v0.1.1`, feature → `v0.2.0`, changes how the pit
   works → `v1.0.0`. **A `-` makes it a prerelease** (`v0.2.0-beta.1`), which
   only machines on the *beta* channel see. Never reuse a number.
3. **Tag and push the tag.** This is what starts the build.
   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```
4. **Watch GitHub → Actions → *Build app*** (20–35 min). *build* red means
   packaging broke; *release* red means publishing failed. Nothing is published
   unless the packaged app passed `--self-check` on the runner.
5. **Check the release:** `…-Setup.exe` (for people), `…-windows.zip` (for the
   updater) and `manifest.json` (its SHA-256). No Setup.exe means Inno Setup
   failed; read the end of the log.

Every installed machine on that channel notices within six hours. **You don't
touch the pit machines.**

**Running *Build app* by hand** (Actions → Run workflow) builds and self-checks
without releasing. Use it to find out whether a change broke packaging without
spending a version number.

**The Nexus relay is not part of this.** It deploys itself when `nexus-relay/`
changes on `main` (`.github/workflows/relay.yml`: typecheck, dry run, deploy,
health check), about a minute of Linux runner time; see
[its README](nexus-relay/README.md).

---

## B · Install on a new pit machine

0. **Turn Smart App Control off:** Windows Security → App & browser control →
   Smart App Control → **Off**. The app isn't code-signed, and SAC blocks
   unsigned apps outright, with no "Run anyway". If it's in **Evaluation**,
   turn it off anyway; Windows can switch it to blocking on its own later. The
   installer warns if it finds Evaluation, and `--self-check` reports it
   (`appcontrol`). See [Code signing](#code-signing) for why there's no free
   way around this.
1. On the pit machine, sign in to GitHub (the repo is private) → **Releases** →
   download **`Breakaway-Pit-Display-<version>-Setup.exe`**. Not the zip.
2. Run it. SmartScreen warns once because we aren't code-signed: **More info →
   Run anyway**. No administrator is needed.
3. Tick **Start the pit display when Windows starts**. Paste the **update
   token** if you have it (below); you can also add it later.
4. Set the machine up (music, monitors, CAD model, relay token, event):
   [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md).
5. **Download the analysis model once:** Control → Pit Systems → Analysis →
   **Download the model (5.0 GB)**. Do it on a good connection before the
   event; it resumes if interrupted, is checked against its published
   fingerprint, and survives every update. Without it the app runs normally
   and simply doesn't analyse logs.

**Moving keys without typing them.** On the Mac, with the keys in `secrets/`:

```bash
uv run python tools/pit_setup.py make ~/Desktop/pit-setup.json --event <event key>
```

Drop the file into the pit machine's data directory as `pit-setup.json` before
launching. It's applied once and renamed `.imported`. Or use Control → Event
Feed → **Import setup file…** (admin), or `--provision <file>`. It only ever
writes, never clears a key, and it carries keys in plain text: stick → import →
delete.

**The update token.** GitHub → Settings → Developer settings → Fine-grained
tokens → owner **Breakaway-3937**, only **Pit_Display**, **Contents:
read-only**, nothing else, expiry ~1 year. Paste it in the installer or in
Control → Pit Systems → Software Updates (admin). It's stored as `update_token`
in the data directory.

**Confirm:** Software Updates shows the version, `channel stable`, the install
path, and a **green dot**, meaning GitHub is reachable, the token works and the
next release will arrive by itself.

---

## C · Updates and rollback

- **Checking is automatic; downloading never is.** A 400 MB download that
  starts on its own is one that starts during a match cycle on event wifi.
  Somebody presses *Download and install*.
- **The new build self-checks on this machine before it's used**, against a
  scratch data directory, a spare CAD port and a fake LED link, so the running
  app notices nothing. Only then does `current` switch over, and the new version
  is what the next launch runs.
- **Roll back:** Software Updates → *Roll back*, or if the app won't open,
  `"…\current\Breakaway Pit Display.exe" --rollback`. The previous version
  stays on disk until it's two updates behind.
- **Channels:** `stable` (every pit machine) takes tagged releases; `beta` also
  takes prereleases.
- **Turn automatic checks off before an event** and back on at home.

### The install layout

```
%LOCALAPPDATA%\Programs\Breakaway Pit Display\
    current ──▶ versions\1.4.2      a directory junction; every shortcut points through it
    versions\1.4.1\                 kept for rollback
    pointer.json
```

Windows won't overwrite a running `.exe`, so nothing ever tries to. The running
process holds `versions\1.4.2\` open, and the junction is locked by nothing, so
repointing it mid-session is safe. A copy somebody unzipped by hand isn't this
shape, is detected, and is left alone (it just can't update itself). Running
Setup.exe converts it without touching data.

### Where data lives

Never in the install folder. Updates and reinstalls don't touch it.

| | |
|---|---|
| Windows | `%LOCALAPPDATA%\Breakaway Pit Display\` |
| macOS | `~/Library/Application Support/Breakaway Pit Display/` |
| Any | `PIT_DISPLAY_DATA=<path>` overrides it |

Inside: the database (seeded on first run, **never overwritten**), the imported
telemetry file, the uploaded CAD model, judges slides, `secrets/` (one file per
credential), `update_token`, the analysis model (`models/`, 5 GB, downloaded
once), and the per-machine JSON settings (`update.json`, `nexus.json`,
`webcast.json`, `ai.json`, `sync.json`, which holds this machine's sync
identity and so must never be copied to another machine). The JSON settings live outside the database so
they're reachable when the database is the broken thing. Uninstalling *asks*
whether to delete this folder, and defaults to no.

`uv run tools/upgrade_check.py` proves an upgrade keeps every row a person
typed; see [`DATABASE.md`](DATABASE.md).

---

## D · Building

```bash
uv sync --group build                          # PyInstaller, once
uv run tools/build_app.py --no-model --clean   # → dist/Breakaway Pit Display/
uv run tools/build_app.py --zip --installer    # on Windows: the release artefacts
```

- **Windows builds happen in CI.** PyInstaller doesn't cross-compile, every pit
  machine is Windows, and a macOS runner bills at 10× against the private repo's
  minutes (≈30 billed minutes a release as it stands). The spending limit is
  $0, so running out stops jobs; it never bills.
- **One folder, never one file.** A one-file build re-extracts ~1 GB on every
  launch. "One file" is the installer.
- **CI builds `--no-model`.** The CAD model isn't in git and lives in the data
  directory.
- **The version is stamped from the tag** (`tools/stamp_version.py`). An
  unstamped build reports `0.0.0+dev` and never updates itself.
- **Startup splash:** on Windows the launcher shows `packaging/splash.png` before
  Python starts, then the live boot screen takes over and shows each startup
  step. The PNG is rendered from the boot screen by `tools/make_splash.py`; rerun
  it after changing `app/widgets/boot_splash.py`. If a build can't make the
  launcher splash (no Tcl/Tk), it prints why and ships without it.
- The build refuses to start without the seed database or the owlet binaries.
  `tools/check_installer.py` lints `packaging/installer.iss` first, because Inno
  can only compile on Windows.

**What's in the package:** the app and a Python runtime; PyQt6 with QtWebEngine
(Chromium) for the CAD viewer; QtNetwork's TLS plugin and QtWebSockets for the
relay's `wss://`; the bundled fonts; the CAD viewer and `subsystems.json`; owlet
for Windows and macOS; on Windows, the libVLC runtime (`tools/fetch_vlc.py`,
SHA-256-checked) and llama.cpp's `llama-server`, the analysis engine
(`tools/fetch_llama.py`, Vulkan build, pinned and SHA-256-checked, ~85 MB);
the analysis contracts (`home/contracts/`); the seed database. **Not in it:**
the music (paths only), the CAD model in CI builds, **the analysis model**
(5 GB, downloaded once into the data directory), the telemetry file, and the
relay. About 1.1 GB
installed without the model, ~400 MB as the update zip.

---

## E · `--self-check`

```
"%LOCALAPPDATA%\Programs\Breakaway Pit Display\current\Breakaway Pit Display.exe" --self-check
```

Boots everything offscreen, renders all four windows, and reports. It exits
0/1 and writes `selfcheck.log` to the data directory, which is the file to ask
for over the phone. It's also the gate every update has to pass on the pit
machine. **`WARN` is not a failure.** Only what would leave an operator with
nothing fails.

| Line | Checks | If it's wrong |
|---|---|---|
| `paths` | resource/data dirs, data writable | `FAIL`: set `PIT_DISPLAY_DATA` to a folder the operator owns |
| `database` | schema version, tables | |
| `webengine` | Chromium's helper process and resources | `FAIL` is a packaging fault: `_webengine_support()` in the spec |
| `cad` | asset server answers; is there a model | upload the `.glb` |
| `fonts` | all three families loaded | |
| `owlet` | the extractor for this platform | `.hoot` import dead; `.wpilog`/`.txt` still work (`tools/owlet/README.md`) |
| `audio` | libVLC **and** its plugins | "No plugins" means silent playback; the build skipped `fetch_vlc.py` |
| `updates` | version, channel, layout, token | names which of four reasons self-update is off |
| `appcontrol` | Windows Smart App Control state | `WARN` on Evaluation or On: turn it off before an event, or it may block the app |
| `network` | Qt TLS backend (schannel on Windows) + Python `ssl`, and that HTTPS verifies through the OS (`truststore`) | `FAIL` means the relay socket can never connect; a packaging fault (`PyQt6.QtNetwork` in the spec's `hiddenimports`) |
| `nexus` | relay/key present, models parse the bundled examples | `WARN` with no relay token and no key means the feed is off |
| `sync` | every synced table still has its three triggers, the guard is down, token present | `FAIL` means local edits silently stop reaching other pits; `WARN` with no token means this machine doesn't sync |
| `ai` | the analysis contracts load, the run log, the bundled engine (`llama/llama-server`), the model downloaded and verified, whether Ollama answers | `FAIL` is a packaging fault: contracts missing (`tree("home/contracts")`) or, on a Windows build, no `llama/` (`fetch_llama.py`); `WARN` means no engine is ready yet: download the model from Control → Analysis |
| `webcast` | which screens are published, their exact URLs, and that the pages are in the bundle | try the printed URL from the Pi's browser |
| `qt` | Qt's own warnings this run | full text in `qt_warnings.log` |
| `crashlog` | the last crash recorded in `crash.log` | `WARN` quotes it; that file is the one to send |
| `windows` | all four built and rendered | |

---

## Claude on a pit machine (`--mcp`)

Any machine running the pit app can serve its robot logs and analysis runs to
Claude Desktop or Claude Code over MCP, read-only, from its own synced copy:
no tunnel, works offline. Claude Desktop → Settings → Developer → Edit Config:

```json
{ "mcpServers": { "breakaway-pit": {
    "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\Breakaway Pit Display\\current\\Breakaway Pit Display.exe",
    "args": ["--mcp"] } } }
```

From a checkout (the dev Mac): `"command": "uv"`, `"args": ["run",
"--directory", "/path/to/Pit_Display", "main.py", "--mcp"]`. Twelve tools: the
ten the analyst uses, `analysis_runs` (findings and the crew's verdicts) and
`analysis_scoreboard`. If the host can't start it, `mcp.log` in the data
directory says why.

## "Certificate verify failed"

Updates or the event feed fail on one machine with a certificate error, while
another machine (and the browser on the same machine) works. Run:

```
"%LOCALAPPDATA%\Programs\Breakaway Pit Display\current\Breakaway Pit Display.exe" --net-check
```

It tries GitHub, frc.nexus and the relay and says which of three causes it is:

| It says | Cause | Fix |
|---|---|---|
| expired / not yet valid | the machine's clock is wrong | set the date and time |
| re-signing secure traffic | a school/venue web filter | a phone hotspot; or ask for the hosts to be allowed |
| doesn't have the certificate authority | Windows hasn't fetched that root yet | open the site once in Edge; builds from 2026-09-24 on fetch it themselves |

Builds from 2026-09-24 on verify through Windows itself (`truststore`,
`app/net.py`), which handles the last two the way the browser does. A machine
on an older build whose updater is failing can't update itself to the fix:
install the new Setup.exe over it (data is untouched).

## Code signing

**The build is not signed, deliberately, because there's no free way to do
it that Windows accepts.** Checked 2026-09-23 against Microsoft's docs:

- **Smart App Control** accepts only binaries its cloud already trusts, or
  ones signed by a CA in Microsoft's **Trusted Root Program**. A self-signed
  certificate doesn't count, however it's installed. It also judges every DLL
  in the bundle, not just the `.exe`.
- The trusted routes all cost money: Azure Artifact Signing (~$10/month, US
  organisations or self-employed individuals, identity-verified, drops into
  GitHub Actions) or a CA certificate on a cloud HSM (~$200–500/year).

So pit machines run with **Smart App Control off**, and SmartScreen shows
"Windows protected your PC" once on the downloaded Setup.exe (**More info →
Run anyway**). Updates are fetched by the app itself and never see
SmartScreen. If the team ever funds signing, it's one step in `build.yml`
signing every `.exe`/`.dll`/`.pyd` in `dist/` and the installer before
`--self-check`.

## Known limits

- **Not code-signed**; see above.
- **Updates are the whole package**, with no deltas. Fine at the shop; not on
  event wifi.
- **Two versions on disk ≈ 2 GB.** Older ones are pruned at startup.
- **One architecture per build.** Build on what you ship to.
