# owlet — CTRE's hoot extractor

`owlet` is the command-line tool CTR Electronics ships for reading `.hoot`
files. The app shells out to it to turn a `.hoot` into a `.wpilog`, which
`app/robot/wpilog.py` then reads directly into the database. Nothing else in
Pit Display can open a `.hoot` — the format is closed, and this binary is the
only reader for it.

| File | Platform |
|---|---|
| `owlet-26.3.0-macosuniversal` | macOS, Intel + Apple Silicon |
| `owlet-26.3.0-windowsx86-64.exe` | Windows x86-64 — **the pit machine** |

Version 26.3.0 (2026 hoots). Both are redistributed as CTRE publishes them; do not modify.

## One owlet per season

Each owlet reads only its own hoot format ("compliancy", the byte at 0x46 of
the file): owlet 26.3.0 reads 2026's format 19 and refuses 2025's format 13
with "Input file is an unsupported version: 0". `owlet.py` reads the byte and
picks the bundled owlet for it (`OWLET_FOR`, from CTRE's index at
`https://redist.ctr-electronics.com/index.json`). To import last season's
hoots, add **owlet 25.4.1** beside 26.3.0 (CTRE's own downloads, SHA-1 in the
index):

    https://redist.ctr-electronics.com/tools/owlet/25.4.1/owlet-25.4.1-macosuniversal
    https://redist.ctr-electronics.com/tools/owlet/25.4.1/owlet-25.4.1-windowsx86-64.exe

Without it, a 2025 hoot is refused with a message naming the version and URL.

## How the app finds them

`app/robot/owlet.py` looks, in order, at `$PIT_OWLET`, then this directory, then
`owlet` on `PATH`. The lookup **globs** (`owlet*macosuniversal`,
`owlet*windowsx86-64.exe`), so upgrading is a drop-in: put the new binaries here,
delete the old ones, change nothing in the code.

Linux is not bundled — CTRE publishes a Linux build, and dropping it here as
`owlet-<version>-linuxx86-64` (or putting `owlet` on `PATH`) is all it takes.

## The two things that go wrong

**macOS quarantine.** A binary downloaded through a browser carries
`com.apple.quarantine` and Gatekeeper kills it before `main()`. `owlet.py`
detects this and says so, but the fix is manual and one-time:

```bash
xattr -d com.apple.quarantine tools/owlet/owlet-26.3.0-macosuniversal
chmod +x tools/owlet/owlet-26.3.0-macosuniversal
```

**Phoenix Pro licensing.** A log containing Pro devices normally requires a
license check to export. `owlet.py` retries once with `--unlicensed` when the
first attempt fails that check, which is enough for the signals this app stores.

## Running it by hand

```bash
tools/owlet/owlet-26.3.0-macosuniversal -f wpilog robot.hoot robot.wpilog
tools/owlet/owlet-26.3.0-macosuniversal --scan robot.hoot   # list signals
```
