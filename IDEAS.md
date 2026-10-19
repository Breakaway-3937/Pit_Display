# Ideas

Features worth building, not scheduled. Each says what it uses that already
exists, so picking one up starts from the right files. Move an idea out when
it's built (or decided against, with why).

## Pit-front trivia game (parked 2026-10-18)

Visitors tap "Test your FIRST IQ" on the pit-front panel: five quick questions
from home's facts (Breakaway, Arkansas, the team's season history), score on
a "Top scores today" list on the overhead screens (first names, typed by the
visitor). Keeps strangers at the pit, and every question is about the team.

- **Uses:** home's `tba_fact` sentences and their Blue Alliance credit
  (`facts_overlay.py`, `app/attribution.py`), the touch router
  (`app/touch.py`), `wording.field()` so admins can edit questions, an
  overhead face for the scores (`app/overhead.py`, matched pair).
- **Open questions:** where questions come from (generated from facts, or
  authored by the team); names of minors on a public screen (brand check 9:
  consent), so maybe initials only.

## Alliance briefs (concept, waiting on the scouting system, 2026-10-18)

**Status: theory only. Nothing gets built until the scouts' system is stable
and Brayden brings it in.** Source is the scouts' own data, **not TBA**
(Brayden's call: the scouting system is the better source, and it's what the
team will want on these screens).

**What it is.** Before each of our matches, a brief per team in it: our two
partners and the three opponents. What each does well, what to watch, how
reliable they've been, and the scouts' notes, so the drive team walks to
queue knowing the match without anyone assembling it by hand.

**Who sees it, and where.** Strategy about other teams must not face the
pit aisle: opponents walk past our overhead screens. So the brief lives on
the **control screen** (and optionally the pit-front panel while the crew is
using it), never the audience screens. The overhead Next Match board keeps
showing what's public (the queue, the schedule, our record). Revisit only if
the team wants a "partners only" overhead view.

**Shape on screen** (control screen, one panel or a Next Match tab):
three partner cards (us + two) and three opponent cards, each a team number,
name, a line of tags from the scouts ("fast cycler", "plays defense"), three
to five headline metrics with a small bar against the event's range, a
reliability line (matches scouted, breakdowns or no-shows), and the newest
note. Alliance colour as the card's plate tint (the Next Match board's
`plate_tint()`), never red text; one red thing: a reliability flag, if any.
"Scouted in N matches" on every card so a thin sample reads as thin.

**The data contract to propose once the system is stable.** Season-agnostic
on purpose: the scouts pick the metrics each year, the display never
changes.

    per event, per team:
      team            "3937"            (a string, as everywhere here)
      event_key       "2027arli"
      matches_scouted 9
      updated_at      Unix ms
      metrics         [{key, label, unit, value, n, higher_is_better}]
      tags            ["fast cycler", "plays defense"]
      reliability     {breakdowns, no_shows, notes}
      notes           [{match_key, text, at}]   (no scout names: minors)

**How it would arrive, preferred first.**
1. Into the home SQL Server and down through the hub as a pulled-only,
   home-produced table, like `tba_match` (`tables.APPLIED`, DATABASE.md,
   a `home/REQUESTS.md` ask). Pits get it offline-safe at events through the
   sync they already do; home keeps history.
2. A file the scouting system exports, imported on a pit (as
   `pit-setup.json` is): the fallback if their system can't reach home.
3. Their API, polled by home (never by a pit: one credential, one place).

**Questions for the scouts when it's stable.** What it stores and exports;
how often data lands during an event, and over what network; who owns it
and who may see it (opponent notes); whether scout names are in it (minors:
keep them out of anything synced); and **what they'd want back**: our own
robot's numbers from its logs (cycle times, faults by match) could feed
their system in return.

**Later, on the same data.** Ask the Pit could take a scouting tool
("how did 1234 do in their last three matches?"), with the same
figure checks.

## Crew pit log (concept, 2026-10-18)

**What it is.** The crew's own record of what they did to the robot, written
in the moment: "replaced TalonFX 17", "re-zeroed the arm", "bumper bolt loose
after qm14", "swapped to battery 6". Timestamped and tagged to the match by
itself, so the robot's logs finally have the other half of the story: what
the humans changed between matches.

**Why it matters.** Today a fault that starts in qm15 can't be told apart
from the motor the crew swapped in after qm14. With the log, the analysis and
Ask the Pit can say "TalonFX 17's faults began after it was replaced at
2:10", and a mentor at home can read the day without being there.

**Writing an entry (fast, gloved, mid-repair).**
- On the control screen (and the pit-front panel when the crew holds it): a
  big "Log it" button, a text line, and one-tap chips for what happens every
  event: *Replaced*, *Adjusted*, *Inspected*, *Battery swap*, *Software
  deploy*, *Broken*, *Fixed*. Two taps and a device pick ("Replaced" →
  TalonFX 17, from the CAN names already in the database) is a full entry.
- Tagged by itself: the time, the match just played and the next one (from
  the Nexus schedule), the machine, who is optional (first name or initials;
  minors), and the log the robot was recording then, if one's imported.
- Editable for an hour by anyone on the control screen; deletable with the
  admin lock (the same rule as a session).

**Where it shows.**
- A day view on the control screen: entries interleaved with the matches
  played, boards published, faults latched and imports, so the day reads as
  one timeline. Filter by device.
- The Next Match board's "since last match" line on the control screen ("3
  entries since qm14: replaced TalonFX 17, ..."): crew-facing only, never the
  audience screens.
- Each robot log's detail (Robot Logs, the Analysis run) lists the entries
  made while it recorded and between it and the log before.

**How the models use it.**
- The analysis primer gets the entries between the previous log and this one
  ("crew changes since the last log"), next to the robot brief; a finding can
  cite an entry as context, never as a figure. `PROMPT_VERSION` bump.
- Ask the Pit gets a `pit_log` tool (entries by time, device or match) and the
  same entries in its primer for the focused log.
- The anomaly detector can treat a "Replaced" entry as a new baseline for that
  device: a new motor's different numbers are expected, not a flag.

**Storage and sync.** A synced table (`pit_log`: uid, at, text, kind,
devices as CAN identities, match_key, session uid, machine, author, edited
at), triggers like every synced table, pushed and pulled (every pit sees
every entry; home keeps them for the season). Contract in DATABASE.md;
home gets a request (R-next) to mirror it and expose it to its own analysis.

**Open questions.** Whether entries also come from a phone in the pit (the
pit LAN has no uplink, and the webcast server is read-only by design); a
how long entries stay editable; whether the
drive team wants a pre-match "what changed" read-aloud.
