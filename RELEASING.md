# Shipping a new version

Two jobs, in the order you do them: **cut a release** on the Mac, then
**install it** on the Windows pit machine.

This is the checklist. [`DEPLOYMENT.md`](DEPLOYMENT.md) is the reference — what
is in the package, why the install has the shape it does, and what every
`--self-check` line means.

---

# A · Cut a release

Everything here happens on the Mac. You type four commands and read a web page.

## 1. Commit and push your work

```bash
cd ~/Desktop/Code/Pit_Display
git add -A
git commit -m "what changed"
git push
```

Nothing is built from uncommitted work. **CI builds the tag, not your working
directory** — a change you forgot to commit is a change that is not in the
release, and it will not tell you.

## 2. Pick a version number

Three numbers, and a `-beta.N` suffix when you want it on one machine first.

| You did | Next version |
|---|---|
| Fixed something | `v0.1.1` |
| Added something | `v0.2.0` |
| Changed how the pit works | `v1.0.0` |
| Any of the above, but try it on one machine first | `v0.2.0-beta.1` |

**A tag with a `-` in it is a prerelease**, and only machines on the *beta*
channel ever see it. That is the whole difference between the two channels, and
it is your safety valve: put one laptop on beta, leave the pit on stable.

**Never reuse a number.** If a build is bad, go to the next one. `v0.1.2` costs
nothing; a tag that means two different things costs an afternoon.

## 3. Tag it and push the tag

```bash
git tag v0.1.1
git push origin v0.1.1
```

**Pushing the tag is what starts the build.** Pushing commits does not.

## 4. Watch it

**GitHub → Actions.** A run called *Build app* appears in a few seconds. Give it
**20–35 minutes**.

Two jobs, in order:

| Job | Red means |
|---|---|
| **build** | Packaging broke. Open the failed step and read the log |
| **release** | The build was fine; publishing failed. Usually permissions |

Nothing is published unless the packaged app booted on the runner. A build that
fails leaves the pit machines exactly as they were.

## 5. Check the release

**Code → Releases.** Your tag should be there with three files:

```
Breakaway-Pit-Display-0.1.1-Setup.exe      ← what a person installs
Breakaway-Pit-Display-0.1.1-windows.zip    ← what the app updates itself with
manifest.json                              ← the checksum tying them together
```

If the `Setup.exe` is missing but the zip is there, Inno Setup failed — read the
end of the build log.

## 6. Done

Every installed pit machine on that channel notices within six hours and says so
on **Control → Pit Systems → Software Updates**. Somebody presses
*Download and install*, and the new version starts the next time the app opens.

**You do not need to touch the pit machines.** Section B is only for a machine
that has never had the app on it.

---

# B · Install on a Windows pit machine

For a **new** machine, or one that has only ever had a hand-copied folder.
A machine that is already installed updates itself — do not use this to upgrade.

## What you need

- The pit machine, with internet.
- A GitHub account that can see `Breakaway-3937/Pit_Display`.
- The update token (see below). Optional, but without it the machine will never
  update itself again.

## 1. Get the installer onto the machine

On the pit machine, in a browser:

1. Sign in to GitHub. **The repo is private** — this is not optional.
2. Go to the repo → **Releases**.
3. Download **`Breakaway-Pit-Display-<version>-Setup.exe`**. About 200 MB.

Not the `.zip`. That one is for the app, not for you.

## 2. Run it

Double-click it.

**Windows will warn you: "Windows protected your PC."** Click **More info →
Run anyway**. The installer is not code-signed — a certificate costs money the
team has not spent. It says this once per machine, and never again for updates,
because those are downloaded by the app rather than by a browser.

Then:

| Page | What to do |
|---|---|
| Destination | Leave it. It defaults to a per-user folder that needs no administrator |
| Tasks | Tick **Start the pit display when Windows starts** on the real pit machine |
| Automatic updates | Paste the token. Leave blank to skip — you can add it later |
| Ready to Install | Install |
| Finished | Leave **Start the pit display now** ticked |

## 3. The update token

Only needed once per machine, and only because the repo is private.

**Making one**, if you do not have it: GitHub → your avatar → **Settings →
Developer settings → Personal access tokens → Fine-grained tokens → Generate
new token**. Resource owner **Breakaway-3937**, repository access **Only select
repositories → Pit_Display**, permissions **Contents: Read-only**. Nothing else.
Copy it — GitHub shows it once.

**Three places it can go**, all the same file:

- The installer's *Automatic updates* page.
- In the app: click the **Breakaway mark** (top-left) → admin password →
  **Pit Systems → Software Updates** → paste → **Save**.
- By hand, as the only line of
  `%LOCALAPPDATA%\Breakaway Pit Display\update_token`.

It can read this one repository and do nothing else. It lives beside the
database, not in the app folder, so updates never lose it.

## 4. Set the machine up

Once, after installing:

1. **Music** — Control → Music → Add folder. The audio is not bundled; point it
   at the team's folder on this machine.
2. **CAD model** — Control → Project → CAD Viewer Config → upload `robot.glb`.
   CI builds without it (it is too big for git), and it lives in the data
   directory from then on, so every future update keeps it.
3. **Monitors** — Control → (each screen) → Display / Fill the display.
4. **Channel** — Control → Pit Systems → Software Updates → **Stable**, unless
   this is deliberately your test machine.

## 5. Confirm it worked

**Control → Pit Systems → Software Updates.** You want:

- The version you just installed, and `channel stable`
- `Installed at C:\Users\…\AppData\Local\Programs\Breakaway Pit Display`
- A **green dot**

Green means the whole chain works: this machine can reach GitHub, the token is
good, and it will take the next release by itself.

For the full report — including whether VLC and the CAD viewer are healthy:

```powershell
& "$env:LOCALAPPDATA\Programs\Breakaway Pit Display\current\Breakaway Pit Display.exe" --self-check
```

It also writes `%LOCALAPPDATA%\Breakaway Pit Display\selfcheck.log`, which is
the file to ask for over the phone. `[WARN]` lines are not failures — `[WARN]
audio` just means VLC is not installed, so music is off and everything else
works.

---

# If something is wrong

| Symptom | What it is |
|---|---|
| Updates panel says "no token" | Step 3. Nothing else is broken |
| Says "unzipped by hand rather than installed" | Someone copied a folder instead of running Setup.exe. Run it; your data is untouched |
| Says "not stamped with a version" | Built locally instead of by CI. Install a real release |
| GitHub "refused the update token" | It expired, or it lost access to the repo. Make a new one |
| New version looks wrong on the pit screens | Control → Pit Systems → Software Updates → **Roll back**. If the app will not even open: `"…\current\Breakaway Pit Display.exe" --rollback` |

**Do not update during an event.** Turn *Look for updates automatically* off
before you leave and on again when you get home. The whole feature is for the
week between competitions.
