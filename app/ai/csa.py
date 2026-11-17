"""
The CSA's knowledge: what each kind of problem looks like in the logs, what
usually causes it, how to tell the causes apart, and what to check by hand.

Brayden, 2026-11-03: "turning the pit into a stationary CSA." A Control System
Advisor diagnoses from experience: voltage dipped and three controllers
rebooted at once, so it's a loose main connection, not the battery. That
experience is written here, as data, from the sources each entry cites (the
WPILib docs, the FMS whitepaper, CTRE's troubleshooting guide, Chief Delphi
and team wiring guides). Code does the bookkeeping, the model the reasoning:

* `match(issue, moment)` finds the entries an issue (`incidents.issues`)
  belongs to and scores each candidate cause against evidence **code
  measured**: which kinds of event happened with it, how often, the readings
  at its worst moment (`incidents.moment`). Each test that held is reported
  with its figure, so the model reasons from facts, not from guesses.
* the model (`diagnose.py`) weighs them, may look closer with the tools, and
  picks a cause or says it can't tell; code checks the pick.
* what the crew confirmed it really was (`confirmations`) is counted per
  cause and shown with the candidates: this robot's own history.

An entry: `id`, `title`, `matches` (primary kinds it covers), `what` (one
line), `causes` [{`id`, `cause`, `supports` / `against` (tests), `check`}],
`sources`. A test is a dict: `{"kind": k}` (an event kind happened in the
issue), `{"kinds_any": [...]}`, `{"count_gte": n}` (occurrences),
`{"devices_gte": n}` / `{"devices_lte": n}`, `{"reading": r, "lt"|"gt": x}`
(a reading at the worst moment), `{"phase": p}`, `{"duration_gte": s}`,
`{"recording_has": k}` (k happened anywhere in the recording, not only with
the issue: console spam behind a loop stall minutes later), `{"recurring_gte": n}` (it happened in at least n of the robot's earlier
recordings, `incidents.history`: a cause that travels with the robot, not
one battery or one field), `{"none_of": [...]}` (none of these happened: how "it was the DS side" is
argued, by the absence of every robot-side cause). A test on the issue's own
kind is skipped: it always holds, so it's no evidence (a real run took
"disabled mid-match" as proof of its own cause, 2026-11-03).

No Qt. Home can import it with the pipeline.
"""

from __future__ import annotations

from typing import Any

WPILIB_BROWNOUT = "https://docs.wpilib.org/en/stable/docs/software/roborio-info/roborio-brownouts.html"
WPILIB_DSLOG = ("https://docs.wpilib.org/en/stable/docs/software/driverstation/"
                "driver-station-log-viewer.html")
WPILIB_DSERR = ("https://docs.wpilib.org/en/stable/docs/software/driverstation/"
                "driver-station-errors-warnings.html")
WPILIB_WIRING = ("https://docs.wpilib.org/en/stable/docs/hardware/hardware-basics/"
                 "wiring-best-practices.html")
WPILIB_CAN = "https://docs.wpilib.org/en/stable/docs/hardware/hardware-basics/can-wiring-basics.html"
FMS = "https://fms-manual.readthedocs.io/en/latest/fms-whitepaper/fms-whitepaper.html"
CTRE_CAN = "https://v6.docs.ctr-electronics.com/en/stable/docs/troubleshooting/canbus-troubleshooting.html"
CD_FAILURES = "https://www.chiefdelphi.com/t/avoidable-common-failures-add-yours/433583"
HOWDY = "https://howdybots.org/wp-content/uploads/2022/01/2022_Kickoff_RobustWiring.pdf"
CD_COMMS = "https://www.chiefdelphi.com/forums/showthread.php?p=1564268"
WPILIB_GC = "https://docs.wpilib.org/en/stable/docs/software/basic-programming/java-gc.html"
WPILIB_RADIO = "https://docs.wpilib.org/en/stable/docs/zero-to-robot/step-3/radio-programming.html"

COMMS_KINDS = ["comms_lost", "lost_comms_msg", "comms_disable", "radio_lost", "packet_loss",
               "high_trip_time", "watchdog", "disabled_mid_match", "fms_lost", "ds_overloaded"]
CAN_KINDS = ["can_bus_off", "can_tx_full", "can_errors", "can_timeout", "can_busy"]

KB: list[dict] = [
    {"id": "brownout", "title": "The robot browned out",
     "matches": ["brownout", "voltage_dip"],
     "what": "the battery fell far enough that the roboRIO disabled every motor output "
             "(stage 2: 6.3 V on a roboRIO 1, 6.75 V by default on a roboRIO 2), until it "
             "recovered above 7.5 V",
     "causes": [
         {"id": "current_draw", "cause": "the robot drew more current than the battery can "
                                         "hold up",
          "supports": [{"reading": "total_current_a", "gt": 180},
                       {"reading": "hardest_motor_a", "gt": 80}, {"recurring_gte": 3}],
          "against": [{"reading": "total_current_a", "lt": 100}],
          "check": "set supply/stator current limits on the drive and the big mechanisms; "
                   "WPILib budgets about 180 A sustained, and four drive motors slipping can "
                   "take most of it"},
         {"id": "weak_battery", "cause": "a weak or under-charged battery",
          "supports": [{"count_gte": 3}, {"reading": "battery_v", "lt": 7.0}],
          "against": [{"reading": "total_current_a", "gt": 240}, {"recurring_gte": 3}],
          "check": "load-test that battery (teams retire ones above about 0.020 Ω internal "
                   "resistance), charge it fully, and write its number down with the match"},
         {"id": "loose_main_power", "cause": "a loose or bad connection between the battery "
                                             "and the PDH",
          "supports": [{"kinds_any": ["device_reset", "roborio_reboot", "radio_lost"]},
                       {"reading": "total_current_a", "lt": 120}],
          "against": [{"reading": "total_current_a", "gt": 220}],
          "check": "wiggle-test battery leads, the Anderson connector, the main breaker and "
                   "the PDH lugs with the robot on; check crimps and bolt torque (WPILib: "
                   "re-check them every 1-3 matches)"}],
     "sources": [WPILIB_BROWNOUT, WPILIB_WIRING, HOWDY, CD_FAILURES]},

    {"id": "branch_power", "title": "One controller's supply sagged",
     "matches": ["device_voltage_dip"],
     "what": "a controller saw its own supply fall while enabled",
     "causes": [
         {"id": "branch_wiring", "cause": "that controller's power wiring (connector, crimp, "
                                          "breaker) is loose or high-resistance",
          "supports": [{"devices_lte": 2}],
          "against": [{"devices_gte": 4}, {"kind": "brownout"}],
          "check": "tug-test its power leads and connectors, reseat its breaker on the PDH"},
         {"id": "whole_robot", "cause": "the whole robot's supply sagged (see brownout)",
          "supports": [{"devices_gte": 4}, {"kind": "brownout"}],
          "against": [{"devices_lte": 1}],
          "check": "treat it as a brownout: the battery and the main connections"}],
     "sources": [WPILIB_WIRING, CTRE_CAN]},

    {"id": "roborio_reboot", "title": "The roboRIO rebooted",
     "matches": ["roborio_reboot"],
     "what": "the DS found the roboRIO freshly booted mid-session (WPILib: a small 'time "
             "since robot boot')",
     "causes": [
         {"id": "power_to_both", "cause": "power cut to the roboRIO and the radio together: "
                                          "the main breaker, the battery leads, or the feed "
                                          "they share",
          "supports": [{"kind": "ping_radio_bad"}, {"kind": "radio_lost"}],
          "against": [{"kind": "ping_rio_bad"}],
          "check": "with the robot on, wiggle the battery leads, the main breaker and the PDH "
                   "lugs, then the roboRIO's and the radio's power connectors; a reboot of "
                   "both is about a minute without comms"},
         {"id": "rio_power", "cause": "the roboRIO lost power: its power connector or the "
                                      "PDH's fused roboRIO slot",
          "supports": [{"reading": "total_current_a", "lt": 150}],
          "against": [{"kind": "brownout"}],
          "check": "check the roboRIO's power connector and ferrules and the PDH's roboRIO "
                   "slot fuse; tug every wire"},
         {"id": "deep_brownout", "cause": "the battery fell below about 4.5 V and the roboRIO "
                                          "blacked out (stage 3)",
          "supports": [{"kind": "brownout"}, {"reading": "battery_v", "lt": 6.0}],
          "against": [],
          "check": "as for a brownout: the battery and current limits"}],
     "sources": [WPILIB_DSLOG, WPILIB_BROWNOUT, WPILIB_DSERR]},

    {"id": "code_crash", "title": "The robot code crashed and restarted",
     "matches": ["code_restart", "exception", "no_robot_code"],
     "what": "the robot program started again partway through, or threw an exception",
     "causes": [
         {"id": "exception", "cause": "an unhandled exception in the robot code",
          "supports": [{"kind": "exception"}],
          "against": [{"kind": "roborio_reboot"}],
          "check": "read the stack trace in the console (the first line is in the event) "
                   "and fix that code path"},
         {"id": "rio_restart", "cause": "the roboRIO itself rebooted",
          "supports": [{"kind": "roborio_reboot"}, {"kind": "brownout"}],
          "against": [{"kind": "exception"}],
          "check": "see the roboRIO reboot checks"},
         {"id": "out_of_memory", "cause": "the roboRIO ran out of memory (a Java "
                                          "OutOfMemoryError ends the program)",
          "supports": [{"recording_has": "low_memory"}],
          "against": [{"kind": "roborio_reboot"}],
          "check": "find what grows each loop: new objects or strings per cycle, a growing "
                   "list, big log buffers; WPILib's heap-dump option shows where"}],
     "sources": [WPILIB_DSERR, WPILIB_GC]},

    {"id": "comms", "title": "The robot lost communication",
     "matches": COMMS_KINDS,
     "what": "the DS ↔ robot link failed; if the DS loses the FMS, the robot's watchdog "
             "disables it (FMS whitepaper)",
     "causes": [
         {"id": "radio_power", "cause": "the robot radio lost power or rebooted",
          "supports": [{"kind": "radio_lost"}, {"duration_gte": 30},
                       {"kinds_any": ["brownout", "voltage_dip"]}, {"kind": "ping_radio_bad"}],
          "against": [{"kind": "roborio_reboot"}, {"kind": "ping_rio_bad"}],
          "check": "check the radio's power (PoE injector or radio power module, barrel "
                   "connector) and wiggle it; a radio reboot is typically 40-50 s without "
                   "comms (WPILib)"},
         {"id": "robot_ethernet", "cause": "the cable between the roboRIO and the radio",
          "supports": [{"kind": "ping_rio_bad"}],
          "against": [{"kind": "radio_lost"}, {"kind": "roborio_reboot"},
                      {"kind": "ping_radio_bad"}],
          "check": "check the roboRIO-radio Ethernet cable and its latching tabs; a bad "
                   "roboRIO ping with a good radio ping points here (WPILib)"},
         {"id": "rio_reboot", "cause": "the roboRIO rebooted",
          "supports": [{"kind": "roborio_reboot"}, {"kind": "code_restart"}],
          "against": [],
          "check": "see the roboRIO reboot checks"},
         {"id": "bandwidth", "cause": "too much network traffic (cameras, dashboards, "
                                      "NetworkTables) for the field's per-team limit",
          "supports": [{"kind": "packet_loss"}, {"kind": "high_trip_time"}],
          "against": [{"kind": "radio_lost"}, {"kind": "roborio_reboot"}],
          "check": "cut camera resolution/frame rate and dashboard data; the field caps each "
                   "team's bandwidth (FMS whitepaper; the game manual has this year's figure)"},
         {"id": "loop_starved", "cause": "the robot code's loop stalled, so control packets "
                                         "went unanswered (the watchdog)",
          "supports": [{"kind": "watchdog"}, {"kind": "loop_stall"}],
          "against": [{"kind": "radio_lost"}],
          "check": "fix the loop overruns (see the loop finding): logging, PDH reads, vision"},
         {"id": "ds_side", "cause": "the Driver Station side: its Ethernet to the field or "
                                    "the laptop",
          "supports": [{"none_of": ["radio_lost", "packet_loss", "roborio_reboot", "brownout",
                                    "code_restart", "loop_stall", "watchdog"]},
                       {"kinds_any": ["ds_overloaded", "ping_ds_radio_bad", "fms_lost"]}],
          "against": [{"kind": "radio_lost"}, {"kind": "packet_loss"},
                      {"kind": "brownout"}, {"kind": "ping_radio_bad"}],
          "check": "use a latching Ethernet cable at the DS, keep the laptop charged and its "
                   "power saving off; ask the FTA for the field's log of the match"}],
     "sources": [WPILIB_DSLOG, WPILIB_DSERR, FMS, CD_COMMS, CD_FAILURES]},

    {"id": "can_bus", "title": "The CAN bus had trouble",
     "matches": CAN_KINDS,
     "what": "frames were lost, the bus went off, or a read timed out",
     "causes": [
         {"id": "can_wiring", "cause": "a loose crimp, connector or wire on the CAN chain",
          "supports": [{"kinds_any": ["can_bus_off", "can_errors"]}, {"devices_gte": 3},
                       {"kind": "device_reset"}],
          "against": [{"kind": "can_busy"}],
          "check": "tug-test every CAN crimp (CTRE: bad crimps are the most common cause), "
                   "jostle the harness watching for red LED blips, check yellow-to-yellow/"
                   "green-to-green, and 60 Ω across CANH-CANL with power off"},
         {"id": "can_load", "cause": "too much traffic on the bus",
          "supports": [{"kind": "can_busy"}, {"kind": "can_tx_full"},
                       {"reading": "can_util_pct", "gt": 85}],
          "against": [{"reading": "can_util_pct", "lt": 60}],
          "check": "lower status-signal update rates, move devices to a CANivore, and don't "
                   "read the PDH every loop"},
         {"id": "termination", "cause": "missing or doubled termination",
          "supports": [{"kind": "can_errors"}, {"devices_gte": 5}],
          "against": [{"devices_lte": 1}],
          "check": "two 120 Ω terminators, one at each end (roboRIO, PDP jumper ON or a "
                   "resistor, CANivore): 60 Ω across CANH-CANL unpowered; 120 Ω means one "
                   "is missing"}],
     "sources": [CTRE_CAN, WPILIB_CAN]},

    {"id": "config", "title": "A controller's configuration didn't apply",
     "matches": ["config_failed"],
     "what": "the code's config call for a controller failed at boot, so it runs on "
             "defaults: no current limits, possibly the wrong direction or neutral mode",
     "causes": [
         {"id": "bus_not_ready", "cause": "the CAN bus or the device wasn't ready when the "
                                          "config was sent",
          "supports": [{"detail_has": "TxFailed|Timeout|timed out"}, {"phase": "disabled"},
                       {"recurring_gte": 3}],
          "against": [{"kinds_any": ["can_bus_off", "can_errors"]}],
          "check": "apply configs with retries and check each StatusCode; power-cycle and "
                   "confirm the config took in Phoenix Tuner"},
         {"id": "bus_fault", "cause": "a CAN wiring fault at boot",
          "supports": [{"kinds_any": ["can_bus_off", "can_errors", "can_timeout"]}],
          "against": [],
          "check": "tug-test the CAN chain to that controller; see the CAN checks"}],
     "sources": [CTRE_CAN]},

    {"id": "remote_sensor", "title": "Controllers lost their remote sensors",
     "matches": ["device_fault:RemoteSensorDataInvalid", "device_fault:FusedSensorOutOfSync",
                 "device_fault:MissingRemoteSensor"],
     "what": "a controller's CANcoder (or other remote sensor) wasn't reporting or agreeing",
     "causes": [
         {"id": "shared_bus", "cause": "several controllers at once: the bus or the CANivore "
                                       "they share",
          "supports": [{"devices_gte": 3}, {"count_gte": 3}, {"recurring_gte": 3}],
          "against": [{"devices_lte": 1}],
          "check": "check the CANivore's USB and power and the CAN chain to the encoders; "
                   "the CANcoders' update rate against the bus load"},
         {"id": "one_encoder", "cause": "one encoder's wiring, ID or configuration",
          "supports": [{"devices_lte": 1}],
          "against": [{"devices_gte": 3}],
          "check": "check that CANcoder's connector, its CAN id, and the controller's remote "
                   "sensor id"}],
     "sources": [CTRE_CAN]},

    {"id": "controller_reset", "title": "A motor controller rebooted",
     "matches": ["device_reset", "device_fault:BootDuringEnable"],
     "what": "a controller restarted while the robot was running",
     "causes": [
         {"id": "its_power", "cause": "its power connection or breaker",
          "supports": [{"devices_lte": 2}],
          "against": [{"kind": "brownout"}, {"devices_gte": 4}],
          "check": "tug-test its power leads and Powerpole/ferrules; reseat its PDH breaker"},
         {"id": "robot_power", "cause": "the whole robot's supply dipped",
          "supports": [{"kind": "brownout"}, {"devices_gte": 4}],
          "against": [{"devices_lte": 1}],
          "check": "see the brownout checks"}],
     "sources": [CTRE_CAN, WPILIB_WIRING]},

    {"id": "missing_file", "title": "A file the code needs isn't on the robot",
     "matches": ["missing_file"],
     "what": "the code tried to open a deploy file (often a PathPlanner auto) that wasn't "
             "deployed or was renamed",
     "causes": [
         {"id": "not_deployed", "cause": "the file was renamed or not deployed",
          "supports": [{"kind": "missing_file"}], "against": [],
          "check": "check the name in the code against src/main/deploy, and redeploy; that "
                   "auto can't run until it's there"}],
     "sources": [WPILIB_DSERR]},

    {"id": "motor_stall", "title": "A motor pushed hard without turning",
     "matches": ["motor_stall"],
     "what": "high stator current with the rotor stopped, while enabled",
     "causes": [
         {"id": "jam", "cause": "a jam, a hard stop or a setpoint the mechanism can't reach",
          "supports": [{"duration_gte": 3}], "against": [],
          "check": "check the mechanism turns freely, its soft limits and setpoints, and its "
                   "stator current limit (a stall heats the motor fast)"}],
     "sources": [CTRE_CAN]},

    {"id": "rail_fault", "title": "A roboRIO power rail faulted",
     "matches": ["rail_fault"],
     "what": "the 3.3, 5 or 6 V rail's current-fault counter rose: too much current drawn "
             "from it",
     "causes": [
         {"id": "sensor_short", "cause": "a short or too many devices on that rail (sensor "
                                         "wiring, an LED strip on the 5 V)",
          "supports": [{"kind": "rail_fault"}], "against": [],
          "check": "inspect sensor wiring on the DIO/analog/MXP pins for a short; move "
                   "high-current loads off the roboRIO's rails"}],
     "sources": [WPILIB_BROWNOUT]},

    {"id": "joystick", "title": "A controller came unplugged at the DS",
     "matches": ["joystick_unplugged"],
     "what": "a joystick disconnected while enabled",
     "causes": [
         {"id": "usb", "cause": "its USB cable or hub at the Driver Station",
          "supports": [{"kind": "joystick_unplugged"}], "against": [],
          "check": "secure the USB cables and hub at the DS; check the DS's USB order"},
         ],
     "sources": [WPILIB_DSERR]},

    {"id": "joystick_missing", "title": "The code reads a controller that isn't plugged in",
     "matches": ["joystick_missing"],
     "what": "the code reads a button on a USB port the DS has no controller on: that "
             "control does nothing",
     "causes": [
         {"id": "wrong_port", "cause": "the code binds a controller on a port nothing is on "
                                       "(the DS's USB tab order)",
          "supports": [{"count_gte": 1}], "against": [{"recording_has": "joystick_unplugged"}],
          "check": "match the DS's USB tab to the ports the code uses (drag them into "
                   "order), or plug in the missing controller"}],
     "sources": [WPILIB_DSERR]},

    {"id": "code_loop", "title": "The robot code fell behind",
     "matches": ["loop_stall", "gc_pause", "code_error"],
     "what": "a loop cycle took ≥ 100 ms (motor safety's timeout), or the code reported an "
             "error",
     "causes": [
         {"id": "slow_code", "cause": "work in the loop: logging, dashboard publishing, PDH "
                                      "and CAN reads, vision",
          "supports": [{"none_of": ["gc_pause"]}, {"kind": "can_timeout"}],
          "against": [{"kind": "gc_pause"}],
          "check": "see the loop finding's slow parts; move PDH reads and logging off the "
                   "loop or slow them down"},
         {"id": "garbage_collection", "cause": "Java's garbage collector paused the code",
          "supports": [{"kind": "gc_pause"}, {"recording_has": "low_memory"}], "against": [],
          "check": "allocate less each loop (no new objects/strings per cycle)"},
         {"id": "console_printing", "cause": "the code printing to the console over and "
                                             "over (slow on a roboRIO)",
          "supports": [{"recording_has": "console_spam"}],
          "against": [],
          "check": "take the print out of the loop (print on a change, or log it with "
                   "AdvantageKit instead): the console spam finding names the line"}],
     "sources": [WPILIB_DSERR, WPILIB_GC]},

    {"id": "console_spam", "title": "The code printed one line over and over",
     "matches": ["console_spam"],
     "what": "a line printed hundreds of times a match; printing is slow on a roboRIO, "
             "costs loop time and floods the DS console the crew reads",
     "causes": [
         {"id": "print_in_loop", "cause": "a print in a method called every loop (a periodic "
                                          "or a state setter)",
          "supports": [{"count_gte": 1}], "against": [],
          "check": "find that text in the code and print only when the value changes, or "
                   "record it with AdvantageKit instead"}],
     "sources": [WPILIB_DSERR]},

    {"id": "low_memory", "title": "The roboRIO ran low on memory",
     "matches": ["low_memory"],
     "what": "the roboRIO reported little free memory (usually 150-220 MB on this robot); "
             "near zero, Java pauses for garbage collection and can end with an "
             "OutOfMemoryError",
     "causes": [
         {"id": "allocation", "cause": "the code keeps allocating or holding memory",
          "supports": [{"recording_has": "loop_stall"}, {"recording_has": "gc_pause"}],
          "against": [],
          "check": "look for objects or strings made every loop and lists that only grow; "
                   "take a heap dump (WPILib) if it keeps happening"}],
     "sources": [WPILIB_GC]},

    {"id": "radio_firmware", "title": "The radio's firmware is out of date",
     "matches": ["radio_firmware"],
     "what": "the Driver Station says the robot radio needs a firmware update; inspection "
             "checks it",
     "causes": [
         {"id": "update", "cause": "the radio hasn't been updated to this season's firmware",
          "supports": [{"count_gte": 1}], "against": [],
          "check": "update the radio with this season's radio firmware (WPILib: programming "
                   "your radio), or at the event's radio kiosk"}],
     "sources": [WPILIB_RADIO]},

    {"id": "thermal", "title": "A motor or controller got too hot",
     "matches": ["device_fault:DeviceTemp", "device_fault:ProcTemp"],
     "what": "a device protected itself from heat",
     "causes": [
         {"id": "binding", "cause": "binding, a stall or a hard duty cycle",
          "supports": [{"kind": "motor_stall"}], "against": [],
          "check": "check the mechanism turns freely, its duty, its current limit and its "
                   "cooling"}],
     "sources": [CTRE_CAN]},

    {"id": "estop", "title": "Emergency stop",
     "matches": ["estop"],
     "what": "the robot was E-stopped",
     "causes": [{"id": "pressed", "cause": "someone pressed E-stop (the team's or the field's)",
                 "supports": [{"kind": "estop"}], "against": [],
                 "check": "ask the drive team and the FTA"}],
     "sources": [FMS]},
]
BY_ID = {e["id"]: e for e in KB}


def entries_for(primary: str) -> list[dict]:
    """The KB entries an issue's primary kind (e.g. 'device_fault:BridgeBrownout')
    belongs to: exact first, then by its kind."""
    exact = [e for e in KB if primary in e["matches"]]
    if exact:
        return exact
    base = primary.split(":")[0]
    return [e for e in KB if base in e["matches"]]


def _test(t: dict, issue: dict, moment: dict) -> tuple[bool | None, str]:
    """(held, the fact it rests on) — None when the data can't say."""
    kinds = set(issue.get("kinds") or [])
    r = (moment or {}).get("readings") or {}
    readings = dict(r)
    if r.get("hardest_motors"):
        readings["hardest_motor_a"] = r["hardest_motors"][0]["stator_a_max"]
    if "battery_v" not in readings and "ds_battery_v" in readings:
        readings["battery_v"] = readings["ds_battery_v"]
    if "can_util_pct" not in readings and "ds_can_util_pct" in readings:
        readings["can_util_pct"] = readings["ds_can_util_pct"]
    own = issue.get("primary", "").split(":")[0]
    if t.get("kind") == own or t.get("kinds_any") == [own]:
        return None, "its own kind"
    if "none_of" in t:
        hit = sorted(kinds & set(t["none_of"]))
        return not hit, (f"none of {', '.join(t['none_of'][:4])}… happened with it" if not hit
                         else f"{', '.join(hit)} happened with it")
    if "kind" in t:
        return t["kind"] in kinds, f"{t['kind']} {'happened' if t['kind'] in kinds else 'did not happen'} with it"
    if "kinds_any" in t:
        hit = sorted((kinds - {own}) & set(t["kinds_any"]))
        return bool(hit), (f"{', '.join(hit)} happened with it" if hit
                           else f"none of {', '.join(t['kinds_any'])} happened with it")
    if "count_gte" in t:
        n = issue.get("count", 1)
        return n >= t["count_gte"], f"it happened {n} time(s)"
    if "devices_gte" in t or "devices_lte" in t:
        # The devices showing this problem (incidents' `buses`), not every
        # device that did anything in those seconds.
        own_devs = {d for ds in (issue.get("buses") or {}).values() for d in ds}
        n = len(own_devs) if issue.get("buses") is not None else len(issue.get("devices") or [])
        ok = n >= t["devices_gte"] if "devices_gte" in t else n <= t["devices_lte"]
        return ok, f"{n} device(s) involved"
    if "reading" in t:
        v = readings.get(t["reading"])
        if v is None:
            return None, f"{t['reading']} not recorded"
        ok = v < t["lt"] if "lt" in t else v > t["gt"]
        return ok, f"{t['reading']} {v:g}"
    if "detail_has" in t:
        import re as _re
        text = " ".join(str(e.get("detail") or "") for e in
                        ((issue.get("worst") or {}).get("events") or []))
        hit = _re.search(t["detail_has"], text, _re.I)
        return bool(hit), (f"the message says {hit.group(0)}" if hit
                           else "the message doesn't say why")
    if "recording_has" in t:
        k = t["recording_has"]
        if k == own:
            return None, "its own kind"
        hit = k in (issue.get("recording_kinds") or [])
        return hit, f"{k} {'happened' if hit else 'did not happen'} in this recording"
    if "recurring_gte" in t:
        b = issue.get("before") or {}
        if (b.get("of") or 0) < t["recurring_gte"]:
            return None, "too few earlier recordings to say"
        return b["in"] >= t["recurring_gte"], (
            f"it happened in {b['in']} of the last {b['of']} recordings before this one")
    if "phase" in t:
        return t["phase"] in (issue.get("phases") or []), f"phases {issue.get('phases')}"
    if "duration_gte" in t:
        d = issue.get("longest_s")
        if d is None:
            return None, "duration not known"
        return d >= t["duration_gte"], f"longest {d:g} s"
    return None, "unknown test"


def match(issue: dict, moment: dict | None, confirmed: dict[str, int] | None = None
          ) -> list[dict]:
    """
    The candidate causes for an issue, best supported first: each with the
    entry it's from, the facts for and against it (as code measured them),
    a score (for − against), and how often the crew confirmed it before.
    """
    confirmed = confirmed or {}
    out = []
    for e in entries_for(issue["primary"]):
        for c in e["causes"]:
            pro = [f for ok, f in (_test(t, issue, moment) for t in c["supports"]) if ok]
            con = [f for ok, f in (_test(t, issue, moment) for t in c["against"]) if ok]
            key = f"{e['id']}.{c['id']}"
            out.append({"id": key, "entry": e["id"], "title": e["title"], "cause": c["cause"],
                        "check": c["check"], "for": pro, "against": con,
                        "score": len(pro) - len(con), "confirmed": confirmed.get(key, 0),
                        "sources": e["sources"]})
    out.sort(key=lambda c: (-(c["score"] + min(c["confirmed"], 3)), c["id"]))
    return out


def describe(issue: dict) -> dict[str, Any]:
    e = (entries_for(issue["primary"]) or [None])[0]
    return {"title": e["title"] if e else issue["primary"].replace("_", " "),
            "what": e["what"] if e else ""}
