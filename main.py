from __future__ import annotations

import os
import sys
from pathlib import Path

# First, before any other app import: an import-time failure in a
# console-less build must still land somewhere. See app/crash_log.py.
from app import crash_log
crash_log.install()

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Must be set before QApplication is created when using QtWebEngine
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

from app import paths
from app import qt_log
from app.console import use_utf8

# Everything else — every service, every window, QtWebEngine — is imported
# inside main(), *after* the boot screen is up, so that the first thing a
# person sees is the splash rather than a second of nothing. See _boot().


def _close_launcher_splash() -> None:
    """
    Close PyInstaller's launcher splash, if this build has one and it is up.

    It is drawn by the bootloader before Python starts (Windows builds only,
    see packaging/pit_display.spec) and stays until something closes it — so
    every path out of here must: the boot screen taking over, and every CLI
    flag that answers without a window.
    """
    if not _launcher_splash_up():
        return
    try:
        # Its import can raise ConnectionError if the bootloader's side
        # failed. A splash must never be the reason the app doesn't start, so
        # any failure means "no splash".
        import pyi_splash
    except Exception:
        return
    try:
        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass


def _launcher_splash_up() -> bool:
    """
    Did the bootloader actually put a splash up? It says so in this variable
    (consumed when `pyi_splash` is first imported, hence also the module
    check). Importing `pyi_splash` without it prints a traceback to stderr —
    noise in every `--self-check` of a build with no splash.
    """
    return "_PYI_SPLASH_IPC" in os.environ or "pyi_splash" in sys.modules


def _launcher_status(text: str) -> None:
    if not _launcher_splash_up():
        return
    try:
        import pyi_splash
        if pyi_splash.is_alive():
            pyi_splash.update_text(text)
    except Exception:
        pass


def _load_fonts(app: QApplication) -> None:
    from PyQt6.QtGui import QFont, QFontDatabase
    fonts_dir = paths.resource("assets", "fonts")
    for ttf in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))
    # App default font — set here, NOT via a universal QSS rule, so widgets
    # that size their own type with setFont() (impact board, brand widgets)
    # aren't overridden by the stylesheet.
    default = QFont("Roboto")
    default.setPixelSize(13)
    app.setFont(default)


def _cli(argv: list[str]) -> int | None:
    """
    The flags that answer without opening a window. None = start normally.

    All three exist for a machine nobody is standing at: an install script that
    wants an exit status, a pit laptop being asked what it is running over the
    phone, and — the one that matters — a display that has come up wrong and
    has to be put back the way it was without a working GUI to do it from.
    """
    if "--version" in argv:
        from app import version
        from app.update import install
        print(version.describe())
        root = install.install_root()
        if root is not None:
            print(f"installed at {root}, running {install.running_version()}")
        return 0

    if "--rollback" in argv:
        from app.update.install import rollback
        from app.update.release import UpdateError
        try:
            print(f"Rolled back to {rollback()} — it starts on the next launch.")
            return 0
        except UpdateError as exc:
            print(f"Could not roll back: {exc}", file=sys.stderr)
            return 1

    if "--net-check" in argv:
        # Can this machine make the app's HTTPS connections, and if not, why?
        # For "updates fail on this machine but not that one". See app/net.py.
        from app import net
        from app.selfcheck import _attach_console
        _attach_console()
        ok, lines = net.check()
        print("\n".join(lines))
        return 0 if ok else 1

    if "--provision" in argv:
        # Apply a pit setup file — keys and the event — from a script or a
        # prompt. The GUI build has no console of its own; borrow the caller's
        # so the report is seen. See app/provision.py.
        from app import provision
        from app.selfcheck import _attach_console
        _attach_console()
        i = argv.index("--provision")
        if i + 1 >= len(argv):
            print("usage: --provision <pit-setup.json>", file=sys.stderr)
            return 2
        try:
            for line in provision.apply_file(Path(argv[i + 1])):
                print(f"  {line}")
            print("Applied. Start the app normally.")
            return 0
        except provision.ProvisionError as exc:
            print(f"Not applied: {exc}", file=sys.stderr)
            return 1
    return None


def main():
    # Before anything prints: Windows opens a redirected stdout as cp1252, and
    # a single em-dash in a status line is then a crash. See app/console.py.
    use_utf8()

    status = _cli(sys.argv[1:])
    if status is not None:
        _close_launcher_splash()
        sys.exit(status)

    # `--self-check` boots everything offscreen and reports, so an install can
    # be verified on the pit machine without anyone watching a screen. See
    # app/selfcheck.py.
    if "--self-check" in sys.argv:
        _close_launcher_splash()
        from app.selfcheck import run as self_check
        status = self_check()
        # Exit *hard*. The check has already printed everything it has to say,
        # and a normal interpreter shutdown here tears down QtWebEngine's and
        # the asset server's threads in an order Qt aborts on — turning a clean
        # pass into exit code 134 and making the status useless to a script.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)

    _launcher_status("Starting Qt…")
    app = QApplication(sys.argv)
    app.setApplicationName("Pit Display")
    app.setOrganizationName("FRC Pit")

    # Qt's own warnings, to a file. The shipped app has no console behind the
    # screens, so anything Qt writes to stderr is lost — including the object
    # lifetime complaints that name a class and nothing else. See app/qt_log.py.
    qt_log.install()
    _load_fonts(app)

    from app import version
    from app.widgets.boot_splash import BootSplash
    splash = BootSplash(version.describe())
    splash.show()
    splash.step("Starting…")
    # The launcher splash is the same picture at the same size; closing it
    # only now means there is never a frame with neither on screen.
    _close_launcher_splash()

    control = _boot(app, splash)
    control.show()
    splash.close()
    sys.exit(app.exec())


def _boot(app: QApplication, splash):
    """
    Every startup step, each named on the boot screen before it runs.

    The order is load-bearing (CLAUDE.md, "Singletons"): config first; the
    database before anything that reads it; `cad_assets` before `leds`;
    `nexus` and `leds` before `alerts`; `checklist` and `webcast` before the
    control screen is built. A step that hangs leaves its own name on screen.
    """
    mods: dict = {}
    services: dict = {}

    def load_modules():
        # Imported here, not at the top, so the boot screen is already up.
        import app.db.migrations  # noqa: F401 — registers migrations before init_db()
        from app import admin, batteries, cad_assets, checklist, config, db, judges_slides
        from app import leds, music, nexus, rotation, theme, touch, update, webcast
        from app.nexus import alerts
        from app.db.sync import service as sync_service
        from app.windows.control_screen import ControlScreen
        from app.windows.presentation_a import PresentationScreenA
        from app.windows.presentation_b import PresentationScreenB
        from app.windows.project_screen import ProjectScreen
        mods.update(locals())

    def seed_and_provision():
        # A fresh install starts with the database, CAD model and judges
        # slides that shipped with it — copied out of the read-only bundle
        # into the writable tree, once, before anything opens them.
        seeded = paths.seed_user_data()
        if seeded:
            print(f"Seeded user data into {paths.data_root()}: "
                  f"{', '.join(seeded)}", flush=True)
        # A `pit-setup.json` dropped beside the database carries the keys and
        # the event; apply it before any singleton goes looking for them.
        from app import provision
        for line in provision.auto_import():
            print(f"pit-setup: {line}", flush=True)

    def services_core():
        mods["config"].init_config()
        mods["db"].init_db()

    def services_feed():
        # Updates need neither config nor the database — deliberately, since
        # the states worth updating out of are the ones where those are broken.
        # It goes here only because the control screen's panel reads it.
        mods["update"].init_update()
        # The Nexus event feed reads the active team from config and nothing
        # else; the relay socket and timers start once it is configured.
        services["nexus"] = mods["nexus"].init_nexus()
        # Admin gates the LED/EQ controls; needs the DB for its credential row.
        mods["admin"].init_admin()
        # Checklists are read while the presentation screens are being built.
        mods["checklist"].init_checklist()
        mods["rotation"].init_rotation()
        mods["judges_slides"].init_judges_slides()

    def services_hardware():
        mods["cad_assets"].init_cad_assets()
        # LEDs after cad_assets — the service subscribes to subsystem_focused
        # so the strips can echo whichever subsystem the CAD viewer flies to.
        services["leds"] = mods["leds"].init_leds()
        # The charging cart's BFGs (mock-up): idle unless a bus or the
        # simulation is configured. Before the control screen, which subscribes.
        services["batteries"] = mods["batteries"].init_batteries()

    def services_music():
        services["music"] = mods["music"].init_music()

    def services_last():
        # Queue and inspection alerts: reads the feed, drives the strips, and
        # hands the overhead screens their banner. After both of those.
        mods["alerts"].init_alerts()
        # Sync after every service it refreshes when a pull lands, and before
        # the control screen (its Telemetry panel subscribes). Off with no
        # token. See app/db/sync/.
        mods["sync_service"].init_sync()
        # Before the control screen, because its Telemetry panel subscribes to
        # this service while it is being built.
        services["webcast"] = mods["webcast"].init_webcast()
        app.setStyleSheet(mods["theme"].dark_qss())

    def build_control():
        services["control"] = mods["ControlScreen"]()

    def wire_up():
        control = services["control"]
        install_touch = mods["touch"].install

        # The audience screens are **built on demand and destroyed when
        # powered off**, so the control screen gets factories rather than
        # instances. A screen that is off costs nothing: no timers, no
        # subscriptions, and for the project screen no Chromium process.
        def _project():
            window = mods["ProjectScreen"]()
            # The two touch panels route touch per point rather than through
            # Qt's single synthesized mouse, so a finger on one screen can't
            # latch a widget on the other. Presentation screens keep Qt's own.
            install_touch(app, window)
            return window

        control.set_window_factories({
            "presentation_a": mods["PresentationScreenA"],
            "presentation_b": mods["PresentationScreenB"],
            "project":        _project,
        })
        install_touch(app, control)

        # The overhead screens, optionally published to the pit LAN. The
        # webcast asks the control screen for windows rather than building
        # any — window lifetime has one owner. See CLAUDE.md, "Pit LAN screens".
        webcast_service = services["webcast"]
        webcast_service.set_window_provider(control.managed_window)
        control.set_webcast(webcast_service)
        webcast_service.start()

        # Leave the hardware in a known state on the way out: strips blanked
        # (the firmware watchdog takes over from there) and audio released.
        app.aboutToQuit.connect(control.shutdown_managed)
        app.aboutToQuit.connect(services["leds"].shutdown)
        app.aboutToQuit.connect(services["batteries"].shutdown)
        app.aboutToQuit.connect(services["music"].shutdown)
        app.aboutToQuit.connect(services["nexus"].shutdown)
        app.aboutToQuit.connect(webcast_service.stop)
        # Closing the control panel is quitting the app: it is the only window
        # an operator can reach, and the audience screens have no chrome.
        app.setQuitOnLastWindowClosed(True)

    steps = [
        ("Loading the app…",                   load_modules),
        ("Preparing this machine's data…",     seed_and_provision),
        ("Opening the database…",              services_core),
        ("Starting the event feed and settings…", services_feed),
        ("Starting the CAD server and LED link…", services_hardware),
        ("Starting music…",                    services_music),
        ("Starting alerts and the pit network…", services_last),
        ("Building the control screen…",       build_control),
        ("Connecting the screens…",            wire_up),
    ]
    splash.set_total(len(steps))
    for text, run in steps:
        splash.step(text)
        run()
        splash.advance()
    splash.step("Ready")
    return services["control"]


if __name__ == "__main__":
    main()
