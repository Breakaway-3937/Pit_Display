"""
`--self-check` — does this install actually work on this machine?

Deployment failures here are not code bugs; they are *machine* facts. The
Windows pit laptop has no VLC runtime, or the CAD viewer's Chromium helper
never made it into the bundle, or `%LOCALAPPDATA%` is redirected somewhere the
app cannot write, or nobody put an owlet binary in the build. Every one of
those looks identical from the outside — an app that starts and then quietly
cannot do one thing — and every one of them is found in about two seconds if
something goes looking.

So the shipped app can be asked:

    "Breakaway Pit Display" --self-check

It builds all four windows offscreen, renders each one, tears them down, and
reports. Exit status is 0 when everything critical passed, 1 otherwise, so it
can be the last line of an install script.

**Where the report goes is not obvious on Windows.** The shipped app is built
`console=False` — a pit display must never open a terminal behind the screens —
and a GUI-subsystem executable is not attached to the console that launched it,
so everything printed here would go nowhere at all. Two things fix that:
`_attach_console()` borrows the parent's console when there is one, and the
whole report is written to `selfcheck.log` in the data directory regardless. An
operator on the phone can be asked for that file.

**Warnings are not failures.** No VLC means no music and a working pit display;
no owlet means no `.hoot` import and a working pit display. Only the things
that would leave an operator with nothing — an unwritable data directory, a
database that will not open, a window that cannot be built — fail the run.
"""

from __future__ import annotations

import os
import platform
import sys
import traceback
from dataclasses import dataclass, field

from app.console import use_utf8


class _Tee:
    """Write to the console and to the log file, so neither can be the only copy."""

    def __init__(self, stream, log):
        self._stream = stream
        self._log = log

    def write(self, text):
        if self._stream is not None:
            try:
                self._stream.write(text)
            except (OSError, ValueError):
                pass
        if self._log is not None:
            try:
                self._log.write(text)
            except (OSError, ValueError):
                pass

    def flush(self):
        for target in (self._stream, self._log):
            try:
                target.flush()
            except (OSError, ValueError, AttributeError):
                pass


def _attach_console() -> None:
    """
    Borrow the console this was launched from, on Windows.

    A `console=False` build has no console of its own and is not attached to
    the caller's, so `print()` lands nowhere — which made the documented
    `--self-check` a command that appeared to do nothing at all. `AttachConsole
    (ATTACH_PARENT_PROCESS)` fixes that when there *is* a parent console, and
    fails harmlessly when there is not (double-clicked, or run by the updater).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
        for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
            if stream is None or stream.fileno() < 0:
                setattr(sys, name, open("CONOUT$", "w", encoding="utf-8",
                                        errors="replace", buffering=1))
    except (OSError, ValueError, AttributeError, ImportError):
        pass


def _open_log():
    """The log file, or None if the data directory will not take one."""
    try:
        from app import paths
        return open(paths.data("selfcheck.log"), "w", encoding="utf-8")
    except OSError:
        return None


# The QApplication is held here rather than in a local. A local goes out of
# scope when `run()` returns, Qt tears the application down on the way out, and
# the asset-server and QtWebEngine threads abort the process — turning a clean
# pass into exit code 134 before `main` ever reaches its hard exit.
_APP = None


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = True
    warnings: list[str] = field(default_factory=list)


def _check_paths() -> Result:
    from app import paths
    lines = [
        f"frozen      {paths.is_frozen()}",
        f"resources   {paths.resource_root()}",
        f"data        {paths.data_root()}",
    ]
    try:
        probe = paths.data("data", ".writetest")
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return Result("paths", False,
                      "\n".join(lines) + f"\ndata directory is NOT writable: {e}")
    seeded = paths.seed_user_data()
    if seeded:
        lines.append(f"seeded      {', '.join(seeded)}")
    return Result("paths", True, "\n".join(lines))


def _check_database() -> Result:
    try:
        import app.db.migrations  # noqa: F401
        from app.db import db, init_db
        init_db()
        version = db.fetchone("PRAGMA user_version")[0]
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        samples = db.fetchone("SELECT COUNT(*) c FROM samples.sample")["c"]
        return Result("database", True,
                      f"schema v{version}, {len(tables)} tables, "
                      f"{samples:,} telemetry samples\n"
                      f"file        {db.path if hasattr(db, 'path') else '—'}")
    except Exception as e:
        return Result("database", False, f"{type(e).__name__}: {e}")


def _check_fonts() -> Result:
    from PyQt6.QtGui import QFontDatabase
    from app import brand, paths
    families = set(QFontDatabase.families())
    missing = [f for f in (brand.FONT_DISPLAY, brand.FONT_BODY, brand.FONT_MONO)
               if f not in families]
    ttfs = list(paths.resource("assets", "fonts").glob("*.ttf"))
    detail = f"{len(ttfs)} ttf files bundled"
    if missing:
        return Result("fonts", True, f"{detail}; NOT loaded: {', '.join(missing)} "
                      "— the app will fall back and look wrong",
                      critical=False)
    return Result("fonts", True, f"{detail}; all three families present")


def _check_sponsors() -> Result:
    # A missing or unreadable mark paints an empty plate on the pit-front
    # panel and nothing else complains, so name each one.
    from PyQt6.QtGui import QImageReader
    from app import paths
    from app.widgets.interactive_board import SPONSOR_DIR, _SPONSORS
    bad = [name for name, _ in _SPONSORS
           if not QImageReader(str(paths.resource(*SPONSOR_DIR, name))).canRead()]
    if bad:
        return Result("sponsors", False,
                      f"missing or unreadable: {', '.join(bad)}", critical=False)
    return Result("sponsors", True, f"{len(_SPONSORS)} logos readable")


def _check_owlet() -> Result:
    try:
        from app.robot import owlet
        described = owlet.describe()
        binary = owlet.find_owlet()
        if binary is None:
            return Result("owlet", True,
                          f"{described}\nNo binary for this platform — .hoot "
                          "import will not work here (.wpilog and .txt still will)",
                          critical=False)
        return Result("owlet", True, f"{described}")
    except Exception as e:
        return Result("owlet", True, f"unavailable: {type(e).__name__}: {e}",
                      critical=False)


def _check_audio() -> Result:
    """
    Is there a libVLC, and — the part that catches a bad Windows build — did
    its *plugins* come along?

    Importing `app.music.engine` rather than `vlc` directly is deliberate: that
    module is what points python-vlc at the bundled runtime, and doing it in
    the other order would test a code path the app never takes. A runtime with
    no plugin directory loads fine and then plays nothing at all, so the plugin
    count is reported rather than assumed.
    """
    from app.music import engine
    note = engine.VLC_RUNTIME_NOTE
    if not engine.HAVE_VLC:
        detail = (f"libVLC not available ({engine.VLC_ERROR or 'unknown'}) — "
                  "music and the equaliser are disabled; everything else works.")
        if sys.platform == "win32":
            detail += ("\nThis build should have carried one: check that CI ran "
                       "tools/fetch_vlc.py.")
        else:
            detail += "\nInstall VLC on this machine to enable them."
        if note:
            detail += f"\n{note}"
        return Result("audio", True, detail, critical=False)

    lines = ["libVLC present"]
    if note:
        lines.append(note)
    plugins = os.environ.get("PYTHON_VLC_MODULE_PATH")
    if plugins:
        from pathlib import Path
        count = len(list(Path(plugins).rglob("*.dll")))
        lines.append(f"plugins     {count} in {plugins}")
        if count == 0:
            # Loads, returns an Instance, and is silent on every play().
            return Result("audio", True,
                          "\n".join(lines) + "\nNo plugins — playback would be "
                          "silent with no error. The build is incomplete.",
                          critical=False)
    return Result("audio", True, "\n".join(lines))


def _check_webengine() -> Result:
    """
    Is Chromium's helper process and resource set actually in this build?

    Both are things PyInstaller gets wrong silently — the app starts, and only
    a screen showing the CAD viewer ever finds out. Naming the missing file
    here turns a mystifying blank panel at an event into one line of output.
    """
    import PyQt6
    from pathlib import Path
    qt6 = Path(PyQt6.__file__).parent / "Qt6"

    if sys.platform == "darwin":
        fw = qt6 / "lib" / "QtWebEngineCore.framework"
        wanted = {
            "helper process": fw / "Helpers" / "QtWebEngineProcess.app"
                              / "Contents" / "MacOS" / "QtWebEngineProcess",
            "resources":      fw / "Resources" / "qtwebengine_resources.pak",
            "icu data":       fw / "Resources" / "icudtl.dat",
        }
    elif sys.platform == "win32":
        wanted = {
            "helper process": qt6 / "bin" / "QtWebEngineProcess.exe",
            "resources":      qt6 / "resources" / "qtwebengine_resources.pak",
            "icu data":       qt6 / "resources" / "icudtl.dat",
        }
    else:
        wanted = {
            "helper process": qt6 / "libexec" / "QtWebEngineProcess",
            "resources":      qt6 / "resources" / "qtwebengine_resources.pak",
        }

    missing = [name for name, path in wanted.items() if not path.exists()]
    if missing:
        return Result("webengine", False,
                      f"missing: {', '.join(missing)}\n"
                      "The CAD viewer cannot run. This is a packaging fault — "
                      "see _webengine_support() in packaging/pit_display.spec.")
    return Result("webengine", True,
                  f"helper process and resources present under {qt6}")


def _check_cad(app) -> Result:
    import urllib.error
    import urllib.request
    from app.cad_assets import cad_assets, init_cad_assets
    try:
        init_cad_assets()
    except RuntimeError:
        pass                       # already initialised by an earlier check
    warnings: list[str] = []
    try:
        cad_assets.start_server()
        with urllib.request.urlopen(cad_assets.viewer_url, timeout=10) as r:
            if r.status != 200:
                return Result("cad", False, f"viewer page returned {r.status}")
            size = len(r.read())
    except (urllib.error.URLError, OSError) as e:
        return Result("cad", False, f"local asset server failed: {e}")

    model = "present" if cad_assets.model_exists else "NOT bundled"
    if not cad_assets.model_exists:
        warnings.append("no robot.glb — upload one from Control → Project → "
                        "CAD Viewer Config")
    return Result("cad", True,
                  f"asset server on :{cad_assets.port}, viewer page {size} bytes, "
                  f"model {model}", warnings=warnings)


def _check_windows(app) -> Result:
    """Build, render and destroy every window — the real smoke test."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtGui import QPixmap
    from app.windows.control_screen import ControlScreen
    from app.windows.presentation_a import PresentationScreenA
    from app.windows.presentation_b import PresentationScreenB
    from app.windows.project_screen import ProjectScreen

    built = []
    try:
        for cls, size in ((ControlScreen, (1600, 1000)),
                          (PresentationScreenA, (1920, 1080)),
                          (PresentationScreenB, (1920, 1080)),
                          (ProjectScreen, (1080, 1920))):
            w = cls()
            w.resize(*size)
            w.show()
            for _ in range(4):
                app.processEvents()
            pm = QPixmap(*size)
            w.render(pm)
            if pm.isNull():
                return Result("windows", False, f"{cls.__name__} rendered nothing")
            built.append(cls.__name__)
            w.close()
            w.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    except Exception:
        return Result("windows", False,
                      f"built {built} then: {traceback.format_exc(limit=6)}")
    return Result("windows", True, f"built and rendered {', '.join(built)}")


def _check_updates() -> Result:
    """
    Can this machine take the next build off GitHub by itself?

    Never critical — an install that cannot update is an install somebody
    updates by hand, which is exactly what every install did before. But it is
    the check to read after setting a pit machine up, because all four ways it
    can be wrong (a checkout, an unstamped build, a hand-unzipped copy, a
    missing token) look identical from the outside: nothing ever appears.
    """
    from app import paths, version
    from app.update import install, settings
    from app.update.release import OWNER, REPO, configured

    prefs = settings.load()
    lines = [f"version     {version.describe()}",
             f"channel     {prefs['channel']}, "
             f"auto-check {'on' if prefs['auto_check'] else 'off'}"]

    root = install.install_root()
    if root is None:
        lines.append("layout      not a managed install")
    else:
        lines.append(f"layout      {root}")
        lines.append(f"installed   {', '.join(install.installed_versions()) or '—'}"
                     f"  (running {install.running_version() or '?'})")

    lines.append(f"feed        {OWNER}/{REPO}, "
                 f"token {'present' if configured() else 'MISSING'}")

    blocked = []
    if not version.is_release():
        blocked.append("this build carries no stamped version")
    if root is None and paths.is_frozen():
        blocked.append("it was unzipped by hand rather than installed")
    if not configured():
        blocked.append("there is no update token on this machine")

    if blocked:
        return Result("updates", True, "\n".join(lines),
                      critical=False,
                      warnings=[f"self-update is off: {'; '.join(blocked)}"])
    return Result("updates", True, "\n".join(lines))


def _check_crash_log() -> Result:
    """
    Has this machine recorded a crash? The last one, verbatim.

    `crash.log` (app/crash_log.py) is the only record of an app that died
    before anyone could see why. Printing its tail here puts it in
    `selfcheck.log`, which is the file an operator gets asked for anyway.
    """
    from app import crash_log
    text = crash_log.tail(200)
    blocks = text.split("UNHANDLED")
    faults = text.count("Fatal Python error")
    if len(blocks) == 1 and not faults:
        return Result("crashlog", True, f"no crashes recorded ({crash_log.path()})")
    last = ("UNHANDLED" + blocks[-1]) if len(blocks) > 1 else text
    lines = last.strip().splitlines()[-25:]
    return Result("crashlog", True,
                  f"log         {crash_log.path()}\n" + "\n".join(lines),
                  critical=False,
                  warnings=["this machine has recorded a crash — the last one "
                            "is above"])


def _check_qt_warnings() -> Result:
    """
    Anything Qt complained about, and where the record of it lives.

    The point is the *file*, not this line: an operator who sees a screen
    misbehave can be asked for `qt_warnings.log` and it will have a timestamp
    and a count, rather than a memory of some red text.
    """
    from app import paths, qt_log
    path = paths.data("qt_warnings.log")
    lines = [f"log         {path}"]
    seen = qt_log.summary()
    if seen:
        lines.extend(seen[:4])
        return Result("qt", True, "\n".join(lines),
                      warnings=[f"{len(seen)} distinct Qt warning(s) this run"])
    lines.append("none this run")
    return Result("qt", True, "\n".join(lines))


def _check_webcast() -> Result:
    """
    Are the overhead screens published to the pit LAN, and on what address?

    The whole feature fails silently when it fails: a Windows Firewall prompt
    answered wrong leaves the port bound but unreachable, and the only symptom
    is two dark panels across the pit with nothing logged anywhere. Printing
    the exact URL is the point — an operator can then try it from the Pi's own
    browser and find out in five seconds which half is broken.
    """
    from app.webcast import settings as wset
    from app.webcast.server import lan_address
    prefs = wset.load()
    if not prefs["enabled"]:
        return Result("webcast", True,
                      "off — the overhead screens are on their own monitors.\n"
                      "Control -> a presentation screen -> Show on the pit "
                      "network publishes one.", critical=False)
    if not prefs["screens"]:
        return Result("webcast", True,
                      "on, but no screens are selected — nothing is published.",
                      critical=False)
    host = lan_address()
    lines = [f"page        {prefs['bind']}:{prefs['port']}",
             f"live data   {prefs['bind']}:{wset.socket_port(prefs['port'])} "
             f"(websocket)"]
    for screen_id in prefs["screens"]:
        lines.append(f"screen      http://{host}:{prefs['port']}/screen/{screen_id}")
    # The page is a real file on disk, and a bundle that dropped it would
    # serve a 500 to a panel across the pit with nothing else complaining.
    from app import paths
    from app.webcast.server import WEB_ROOT
    missing = [n for n in ("screen.html", "screen.css", "screen.js")
               if not paths.resource(*WEB_ROOT, n).exists()]
    if missing:
        return Result("webcast", False,
                      "\n".join(lines)
                      + f"\nmissing from the bundle: {', '.join(missing)}")
    warnings = []
    if prefs["bind"] == "127.0.0.1":
        warnings.append("bound to loopback — no other machine can reach it")
    return Result("webcast", True, "\n".join(lines), warnings=warnings)


def _check_app_control() -> Result:
    """
    Will Windows' Smart App Control let this build keep running?

    SAC admits only binaries Microsoft's cloud already trusts or that are
    signed by a CA in the Trusted Root Program, and it judges every DLL, not
    just the .exe. Nothing free can sign this bundle that way, and a
    self-signed certificate doesn't count however it's installed. So on a pit
    machine SAC has to be **Off**.

    The one state worth a line here is **Evaluation**. Windows lets the app
    run today and may switch itself to enforcement later, at which point the
    app — and every update — is blocked with no "run anyway". Better to learn
    that at the shop than at an event. Read-only: Microsoft warns that
    writing this value directly can leave Windows blocking almost everything,
    so the fix is the Windows Security switch, not this code.
    """
    if sys.platform != "win32":
        return Result("appcontrol", True, "not Windows — nothing to check")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\CI\Policy") as key:
            state, _ = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")
    except OSError:
        return Result("appcontrol", True,
                      "Smart App Control not present on this Windows")
    where = ("Windows Security -> App & browser control -> Smart App Control "
             "-> Off")
    if state == 0:
        return Result("appcontrol", True, "Smart App Control is off")
    if state == 2:
        return Result("appcontrol", True,
                      "Smart App Control is in EVALUATION mode",
                      critical=False,
                      warnings=["Windows may switch it to enforcement on its "
                                "own and block this unsigned app and its "
                                f"updates. Turn it off: {where}"])
    return Result("appcontrol", True, f"Smart App Control is ON (state {state})",
                  critical=False,
                  warnings=["it will block unsigned updates of this app. "
                            f"Turn it off: {where}"])


def _check_network() -> Result:
    """
    Can this build speak TLS — both ways the app does?

    The relay socket (`wss://nexus.bh-stack.com`) goes through **Qt's** TLS,
    which is a plugin (`plugins/tls/`: schannel on Windows, securetransport on
    macOS, openssl elsewhere) that PyInstaller has to find and copy. Every
    HTTPS fetch — the relay's HTTP mirror, frc.nexus direct, GitHub updates —
    goes through **Python's** `ssl`, a separate extension with its own DLLs.
    Lose either and the app starts perfectly: the relay just never connects
    ("TLS initialization failed"), or updates and polling fail with an error
    nobody reads. This is the WebEngine helper's failure mode again, and it
    is caught the same way — here, by name, before the update gate lets the
    build anywhere near a pit. Never touches the network.
    """
    lines: list[str] = []
    problems: list[str] = []
    try:
        from PyQt6.QtNetwork import QSslSocket
        from PyQt6.QtWebSockets import QWebSocket  # noqa: F401 — present is the check
        backends = QSslSocket.availableBackends()
        active = QSslSocket.activeBackend()
        lines.append(f"qt tls      {active or 'none'} (available: "
                     f"{', '.join(backends) or 'none'})")
        # "cert-only" can parse certificates but cannot make a connection.
        if not QSslSocket.supportsSsl() or active in ("", "cert-only"):
            problems.append("Qt has no TLS backend — the relay socket cannot "
                            "connect. plugins/tls is missing from this build")
        lines.append(f"websockets  QtWebSockets {'ok' if not problems else 'loaded'}")
    except Exception as exc:
        problems.append(f"QtNetwork/QtWebSockets would not load: {exc}")
    try:
        import ssl
        ctx = ssl.create_default_context()
        lines.append(f"python ssl  {ssl.OPENSSL_VERSION}, "
                     f"{len(ctx.get_ca_certs()) or 'system'} CA certs")
        from app import net
        lines.append(f"https via   {net.verifier()}")
        if "truststore" not in net.verifier():
            problems.append("truststore is missing from this build — HTTPS "
                            "will fail on machines whose certificate store is "
                            "incomplete or behind a filtering proxy (app/net.py)")
    except Exception as exc:
        problems.append(f"Python's ssl module is broken: {exc}")
    if problems:
        return Result("network", False, "\n".join(lines + problems))
    return Result("network", True, "\n".join(lines))


def _check_nexus() -> Result:
    """
    Is the event feed set up, and do the models still fit the API?

    Never touches the network. The bundled example payloads — the spec's own,
    every endpoint and both webhooks — are parsed through the real models, so
    a field renamed upstream that we have not caught up with shows here rather
    than as an empty board at an event. Key and event are reported, not
    required: a pit with no feed is a working pit.
    """
    from app import credentials
    from app.nexus import api, settings

    prefs = settings.load()
    lines = [f"event       {prefs['event_key'] or '— (feed off)'}",
             f"poll        {'on' if prefs['auto_poll'] else 'off'}, "
             f"every {prefs['poll_interval_s']}s / "
             f"{prefs['slow_poll_interval_s']}s",
             f"relay       {prefs['relay_url'] if prefs['relay_enabled'] else 'off'}",
             f"relay token {credentials.source(api.RELAY_TOKEN_SECRET)}",
             f"api key     {credentials.source(api.API_KEY_SECRET)}"
             + ("  (direct fallback)" if credentials.present(api.API_KEY_SECRET) else ""),
             f"secrets     {credentials.folder()}"]

    try:
        fx = api.load_fixtures()
        fake = api.FakeClient(fx)
        n_events = len(fake.events())
        n_matches = sum(len(fake.event_status("demo").matches)
                        for _ in fx["event_status"])
        fake.pit_addresses("demo")
        maps = (fake.pit_map("demo"), api.PitMap.from_json(fx["map_angles"]))
        fake.inspection("demo")
        {k: api.InspectionStatus.from_json(k, v)
         for k, v in fx["inspection_demo"].items()}
        fake.teams("demo")
        fake.alliances("demo")
        pushes = list(fake.match_pushes())
        for snap in fx["event_status"]:
            assert api.classify_push(snap) == "event"
        for push in fx["match_status"]:
            assert api.classify_push(push) == "match"
        lines.append(f"models      parsed {n_events} events, {n_matches} matches, "
                     f"{sum(len(m.pits) for m in maps)} pits, "
                     f"{len(pushes)} match pushes from the bundled examples")
    except Exception:
        return Result("nexus", False,
                      "\n".join(lines) + "\nexamples   "
                      + traceback.format_exc(limit=4), critical=False)

    warnings = []
    if not prefs["event_key"]:
        warnings.append("no event key — the feed is off until one is set")
    relay = prefs["relay_enabled"] and credentials.present(api.RELAY_TOKEN_SECRET)
    if not relay and not credentials.present(api.API_KEY_SECRET):
        warnings.append("no relay token and no Nexus API key — nothing can "
                        "reach Nexus")
    elif not relay:
        warnings.append("no relay token — polling frc.nexus directly, "
                        "with no pushes")
    if warnings:
        return Result("nexus", True, "\n".join(lines),
                      critical=False, warnings=warnings)
    return Result("nexus", True, "\n".join(lines))


def _check_sync() -> Result:
    """
    Is every team-owned table still recording its changes?

    Never touches the network. The triggers are what make a local edit reach
    the other pits; a table that lost one (a migration gone wrong, a hand-
    edited database) would keep working here and silently never sync, which
    is the one failure nobody would see until two pits disagree. The token is
    reported, not required: a pit that doesn't sync is a working pit.
    """
    from app import credentials
    from app.db import db
    from app.db.sync import settings as sync_settings, tables

    prefs = sync_settings.load()
    try:
        # Bundles, team files and raw logs all leave as zstd; a build that
        # lost it can't send a log at all.
        from app.db.sync import codec
        packed = codec.zstd.compress(b"pit display " * 100, 19)
        assert codec.zstd.decompress(packed) == b"pit display " * 100
        zstd_line = f"zstd        ok ({len(packed)} B for 1200 B)"
    except Exception as e:
        return Result("sync", False, f"zstd        missing: {type(e).__name__}: {e} "
                      "(Python's compression.zstd; bundles can't be built)")
    synced = [s.name for s in tables.SPECS] + [tables.LOG_SESSION]
    have = {r[0] for r in db.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'sync_%'")}
    missing = [f"sync_{t}_{k}" for t in synced for k in ("ins", "upd", "del")
               if f"sync_{t}_{k}" not in have]
    outbox = db.fetchone("SELECT COUNT(*) FROM sync_outbox")[0]
    guard = db.fetchone("SELECT applying FROM sync_guard WHERE id = 1")
    lines = [f"machine     {prefs['machine_name']} ({prefs['machine_id']})",
             f"hub         {prefs['url']}" + ("" if prefs["enabled"] else "  (off)"),
             f"token       {credentials.source(sync_settings.TOKEN_NAME)}",
             f"tables      {len(synced)} synced, {len(have)} triggers",
             f"outbox      {outbox} change{'s' if outbox != 1 else ''} not yet sent",
             zstd_line,
             f"last        {prefs['last_sync'] or 'never'}"
             + (f" — {prefs['last_result']}" if prefs['last_result'] else "")]
    if missing:
        return Result("sync", False, "\n".join(lines) + "\nmissing     "
                      + ", ".join(missing))
    if guard is None or guard[0] != 0:
        return Result("sync", False, "\n".join(lines)
                      + "\nguard       stuck on: local edits are not being recorded")
    if not credentials.present(sync_settings.TOKEN_NAME):
        return Result("sync", True, "\n".join(lines), critical=False,
                      warnings=["no sync token: this machine's settings and logs "
                                "stay on this machine"])
    return Result("sync", True, "\n".join(lines))


def run() -> int:
    """Run every check. Returns a process exit status."""
    # Attach first (a windowed build has no stdout until it does), then make
    # whatever we ended up with UTF-8 and unbuffered: this is diagnostic output
    # that has to survive whatever happens next, and Qt teardown can and does
    # abort the process.
    _attach_console()
    use_utf8()
    log = _open_log()
    if log is not None:
        sys.stdout = _Tee(sys.stdout, log)
        sys.stderr = _Tee(sys.stderr, log)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PIT_LEDS_FAKE", "1")
    os.environ.setdefault("PIT_NEXUS_QUIET", "1")
    os.environ.setdefault("PIT_SYNC_QUIET", "1")

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    print("Breakaway Pit Display — self-check")
    print(f"  {platform.system()} {platform.release()} / {platform.machine()}")
    print(f"  Python {sys.version.split()[0]}\n")

    results = [_check_paths()]
    if not results[0].ok:
        _report(results)
        return 1

    global _APP
    _APP = QApplication(sys.argv[:1])
    app = _APP
    from main import _load_fonts
    _load_fonts(app)

    from app.admin import init_admin
    from app.checklist import init_checklist
    from app.config import init_config
    from app.judges_slides import init_judges_slides
    from app.leds import init_leds
    from app.music import init_music
    from app.nexus import init_nexus
    from app.nexus.alerts import init_alerts
    from app.rotation import init_rotation
    from app.theme import dark_qss
    from app.update import init_update

    results.append(_check_database())
    if results[-1].ok:
        init_config()
        init_update()
        init_nexus()
        init_admin()
        init_checklist()
        init_rotation()
        init_judges_slides()
        results.append(_check_webengine())
        results.append(_check_cad(app))
        from app import qt_log
        qt_log.install()
        init_leds()
        from app.batteries import init_batteries
        init_batteries()
        init_music()
        init_alerts()
        # The control screen's Pit Network panel reads this while it builds.
        from app.webcast import init_webcast
        init_webcast()
        from app.db.sync.service import init_sync
        init_sync()
        app.setStyleSheet(dark_qss())
        results.append(_check_fonts())
        results.append(_check_sponsors())
        results.append(_check_owlet())
        results.append(_check_audio())
        results.append(_check_updates())
        results.append(_check_network())
        results.append(_check_app_control())
        results.append(_check_nexus())
        results.append(_check_sync())
        results.append(_check_webcast())
        results.append(_check_qt_warnings())
        results.append(_check_crash_log())
        results.append(_check_windows(app))

    return _report(results)


def _report(results: list[Result]) -> int:
    failed = [r for r in results if not r.ok and r.critical]
    warned = [r for r in results if r.ok and (not r.critical or r.warnings)]

    for r in results:
        mark = "PASS" if r.ok else ("FAIL" if r.critical else "WARN")
        print(f"[{mark}] {r.name}")
        for line in (r.detail or "").splitlines():
            print(f"       {line}")
        for w in r.warnings:
            print(f"       ! {w}")
    print()

    try:
        from app import paths
        print(f"This report: {paths.data_root() / 'selfcheck.log'}\n")
    except OSError:
        pass

    if failed:
        print(f"FAILED — {', '.join(r.name for r in failed)}")
        return 1
    if warned:
        print(f"OK, with notes on: {', '.join(r.name for r in warned)}")
    else:
        print("OK — everything checks out.")
    return 0
