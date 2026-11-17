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

## The researcher: a second agent that fills the CSA's knowledge gaps (sketch, 2026-11-05)

**Status: design sketch, feasibility checked, nothing built.** Idea from a
friend of Brayden's: the CSA (qwen3:8b over `app/ai/csa.py`) knows only what's
written in its knowledge base; a second, agentic model takes the gaps it hits,
searches documentation and other teams' threads, and hands back what it found,
back and forth, so the knowledge grows from sources instead of only from the
crew's confirmations.

**What's a gap** (all already recorded per run in `analysis_run.stats.incidents`):
a diagnosis that came out `unexplained`; one at low confidence; a kind with no
entry (`csa.match` → no candidates: "no entry in the CSA knowledge covers this
kind yet"); a `hunch` (the model reaching past the candidates); an exact error
string the console printed that no entry names (e.g. `CAN frame not received/
too-stale … Status Signal Fault_RemoteSensorDataInvalid`). Code writes each
gap as a question: the kind, the exact message, the measured facts, the
candidates and what they scored.

**Where it can search, keyless (probed 2026-11-05):**
- WPILib docs: `https://docs.wpilib.org/_/api/v3/search/?q=project:frc-docs+<terms>`
  (Read the Docs search: titles, paths and the matching passages; "watchdog
  not fed" 624 hits).
- CTRE Phoenix 6 docs: `https://v6.docs.ctr-electronics.com/_/api/v3/search/?q=project:phoenixpro-documentation+<terms>`
  ("remote sensor": 25 hits, Remote Sensors first).
- Chief Delphi (Discourse): `https://www.chiefdelphi.com/search.json?q=<exact error>`
  then `/t/<id>.json` for the thread's posts (RemoteSensorDataInvalid found a
  team posting the same error).
- Reddit is out: `r/FRC/search.json` answers 403 without OAuth.
  An allowlist of these hosts only; nothing else is fetched.

**The loop** (code drives it; each model does one bounded job):
1. **Ask** (code): the gap → 2-4 searches (the exact message first, then the
   kind's terms), across the allowlist.
2. **Read** (code): fetch the top results, split into passages, keep the ones
   that share the error text or the kind's terms (no model needed to rank).
3. **Draft** (researcher model): from those passages only, propose a cause
   (`cause`, `check`, tests from the existing vocabulary: `kind`, `reading`,
   `devices_gte`, `recording_has`…) and **quote** the sentence it stands on,
   with its URL.
4. **Check** (code, as `curate.check_changes` does for lessons): the quote
   must appear verbatim in the fetched page; the URL on the allowlist; every
   figure in the cause or check in the quote; tests only from the vocabulary;
   not a duplicate of an entry's cause. Refused drafts go back once with why.
5. **Back to the CSA** (the back-and-forth): the next diagnosis of that kind
   gets the drafted cause as an extra candidate marked `researched`, scored
   on the same facts; if it fits, the CSA can pick it (never above medium
   until the crew confirms). The CSA's answer (fits / doesn't / still unclear)
   goes back to the researcher as the next question's context.
6. **Kept by people**: a drafted entry shows in Control → Analysis as
   "Researched: <cause> (source)". An admin keeps it (it becomes a synced
   knowledge doc, as the playbook is) or drops it; "What was it really?"
   confirmations count for it as for any cause.

**Which model.** "A much smaller agentic model" saves less than it seems:
the job is reading technical passages and quoting them exactly, which is
where 1-2B models invent. Options, best first:
- **the same qwen3:8b in a second role**: no extra download or memory (the
  16 GB pit runs one model at a time), and the curator already does this;
- **Qwen3-4B (Instruct 2507)** for speed, measured with `tools/ai_eval.py`
  before trusting it;
- not two models loaded at once on a pit machine (6.5 GB each).

**When it runs.** It needs the internet and the engine idle: home, or a pit
machine overnight / between events (as the curator runs when idle), never
during a match queue. Cached: a page fetched once is kept (`research_page`),
so the same gap doesn't fetch again.

**Uses:** `diagnose.py` (gaps, candidates, the `hunch`), `csa.py` (the test
vocabulary, `sources`), `curate.py` (the shape: propose → code checks →
people keep), `app/net.py` (HTTPS, `truststore`), `tools.py` (read-only), the
synced setting docs (`app/db/sync/docs.py`). New: `app/ai/research.py`, one
table (`research_page`: url, fetched_at, text) and one doc (`csa_researched`).

**Open questions:** whether home runs it (always online, a bigger model) and
pushes the drafts as a synced doc, which would spare the pit machines
entirely; how many drafts a crew will actually review; CTRE's and Chief
Delphi's terms for automated searching (keep it to a few queries a gap).
