# Installing on a pit machine

One page. Everything else about deployment is in [`DEPLOYMENT.md`](DEPLOYMENT.md),
and you do not need it to put this on a laptop.

---

## The install

1. On the pit machine, open the repo's **Releases** page, sign in to GitHub, and
   download **`Breakaway-Pit-Display-<version>-Setup.exe`**.
2. Double-click it. Windows SmartScreen warns once, because we do not own a
   code-signing certificate: **More info → Run anyway**.
3. It asks two things. Both have sensible answers:
   - **Start the pit display when Windows starts** — yes, on a pit machine.
   - **GitHub token (optional)** — paste one if you want this machine to take
     updates by itself. Skip it and it never will. See the last section.
4. Finish. There is a Start Menu entry and an Add/Remove Programs entry.

No administrator, at install or at update time — everything is per-user under
`%LOCALAPPDATA%`.

**That is the install.** If it launched and you can see the control screen, the
app is working. The rest of this page is the three things only you can tell it.

Ignore `…-windows.zip` on the release page — that one is not for people, it is
what the app downloads when it updates itself.

---

## The three things to set up, once

Nothing below is required for the app to run. Each one lights up a feature.

**Music** — Control → Music → **Add folder**, pointed at the team's audio.
The library is an index of paths, so the audio itself stays where it is and is
not copied. (The VLC runtime is inside the app; you do not install VLC.)

**The monitors** — Control → (each screen) → **Display** and **Fill the
display**. With more than one monitor the audience screens default to filling
their own. With exactly one, they stay windowed on purpose, so the control
panel can never end up buried behind a full-screen display you cannot close.

**The CAD model** — Control → Project → **CAD Viewer Config**, upload the
season's `.glb`. Releases are built without it (it is ~340 MB and not in git).
It lives in the data directory from then on, so it survives every update.

---

## Screens over Ethernet (optional)

The two overhead panels can be shown by a Raspberry Pi on the pit switch
instead of a monitor on the end of an HDMI run. **The pit machine then needs no
video output for them at all** — only the control screen needs a real monitor.

**On the pit machine:** Control → Presentation A (or B) → **Show on the pit
network**, then switch the screen on with its normal power toggle. The panel
prints the exact address and has a **Copy** button beside it.

**The power switch still means what it always did** — is this screen on. All
publishing changes is where it comes out: a published screen never opens a
window on the pit machine, so nothing lands on top of the control panel. Turn
it off and the browser across the pit says so rather than freezing on the last
slide.

Windows will ask about the firewall the first time — **allow it on Private
networks**; refusing leaves the port bound but unreachable, and the only
symptom is a dark panel.

**On each Pi:** open that address in Chromium and put it full screen. That is
the whole setup. It reconnects by itself if the pit machine restarts, so it
survives a power cut with nobody walking over to it.

```
http://<pit machine>:3938/                     lists every published screen
http://<pit machine>:3938/screen/presentation_a
```

The port is **3938** — the team number plus one — so it is one less thing to
look up. Live data rides a websocket on **3939** (always the page port plus
one); the page is told where to dial, so there is nothing to configure. Windows
prompts about the firewall once, per application, and that one answer covers
both ports.

To start a Pi straight into a screen, add to `~/.config/lxsession/LXDE-pi/autostart`:

```
@chromium-browser --kiosk --noerrdialogs --disable-infobars http://<pit machine>:3938/screen/presentation_a
```

**What it costs the pit machine: essentially nothing.** The browser does the
drawing; the pit machine only sends state when state changes, which between
slides is never. Measured at **0.1% of one core** for two published screens
with a viewer attached. The page renders at the panel's own resolution, so
type is as crisp as the display can show.

**Keep this on a pit-local switch.** There is no password on it — nothing
served is a credential and nothing is writable — but a stranger's browser
holding a stream open costs the pit machine real CPU during a match cycle.
Never put this port on event wifi or a hotspot.

One limitation: **in judges mode with the CAD viewer active**, a network screen
shows a notice instead of the model. Chromium renders the CAD in its own
process, which cannot be captured. Open the CAD viewer directly in the Pi's
browser if you need it there.

---

## Checking it

```
"%LOCALAPPDATA%\Programs\Breakaway Pit Display\current\Breakaway Pit Display.exe" --self-check
```

Builds all four windows offscreen, renders them, and reports. It also writes
`selfcheck.log` into the data directory, which is the file to ask an operator
for over the phone.

**Warnings are not failures.** No Nexus key means no event feed and a working
pit display. Only things that would leave an operator with nothing fail the run.

---

## The event feed (optional)

Paste the key from frc.nexus/api into **Control → Pit Systems → Event Feed**,
and set the event key. Polling every 30 s brings everything; that is the whole
setup.

**Push is an accelerator, not a requirement**, and it needs a public URL because
Nexus has to POST *into* a laptop sitting behind event wifi. The setup is in
[`NEXUS.md`](NEXUS.md) under "Giving Nexus a route in" — a Cloudflare tunnel,
done once against a domain the team owns. Skip it and nothing is missing except
a few seconds of latency.

---

## Updates (optional)

The repo is private, so a machine needs a token to see releases at all.

GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**. Resource owner **Breakaway-3937**,
repository access **Only select repositories → Pit_Display**, permissions
**Contents: Read-only**, and nothing else. It can read this one repo and do
nothing whatsoever if it leaks off the machine.

Paste it into the installer when it asks, or afterwards into **Control → Pit
Systems → Software Updates** (click the Breakaway mark, top left, to unlock).

The machine then checks every six hours and *says* when something newer exists.
**It never downloads by itself** — a 400 MB download that starts on its own is
one that starts during a match cycle on event wifi. Somebody presses the button.

**Turn auto-check off before a competition** and back on when you get home. The
whole feature is for the week between events.

---

## If you have to reinstall

Re-running the Setup.exe touches no data. The database, checklists, CAN-id
names, imported logs, EQ presets, keys and the uploaded CAD model all live in
`%LOCALAPPDATA%\Breakaway Pit Display\`, which is a different folder from the
app and is never replaced by an install or an update.

Uninstalling *asks* whether to delete that folder, and defaults to no.

To put keys on a machine without typing them, make a setup file on the Mac
(`uv run tools/pit_setup.py make`) and drop it into the data directory as
`pit-setup.json` before launching. The app applies it once and renames it.
