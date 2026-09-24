# The Nexus event feed

What the pit knows about the event without anyone running back from the
field: which match is queuing, when ours is on deck, where our pit is, whether
we have passed inspection, and who is on our alliance. All of it comes from
[Nexus](https://frc.nexus) — the queuing tablet most events run — through its
public API, `https://frc.nexus/api/v1`, spec **v1.8.0**
([docs](https://frc.nexus/api/v1/docs)).

This file is the reference for the whole feature: its status, what every
endpoint and field means, how the app keeps the data fresh, and where a screen
should look to draw it. `app/nexus/api.py` is the same reference as code.

> **Attribution is a condition of the API's use.** Any surface that shows this
> data carries "Event data from frc.nexus" (`api.ATTRIBUTION`). The Event Feed
> panel does; a new board that shows a match must too.

---

## Status

| | |
|---|---|
| Relay | **Live** at `https://nexus.bh-stack.com` (deployed 2026-09-23) |
| Webhook | **Resolved.** Registered at frc.nexus/api for *live event status, all events* → `https://nexus.bh-stack.com/nexus/webhook`. Once for the season; no per-event re-registration |
| Pit machine | Needs only `secrets/nexus_relay_token`; setup in [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md#the-event-feed-nexus) |
| Planned for an audience screen | the alliance board, announcements, parts requests. **The pit map is not wanted**; it stays fetched for the Event Feed panel only |

**Credentials** live in `secrets/` in the data directory (`app/credentials.py`),
one file each, overridable as `PIT_SECRET_<NAME>`, and never in `nexus.json`,
the database or git:

| File | What | Needed |
|---|---|---|
| `nexus_relay_token` | `Authorization: Bearer …` to the relay (its `CLIENT_TOKEN`) | yes |
| `nexus_api_key` | `Nexus-Api-Key`, for polling frc.nexus directly if the relay is unreachable | optional |

The webhook token lives only in the relay's Worker secrets. They travel to a
pit machine in a setup file; see [`DEPLOYMENT.md`](DEPLOYMENT.md#b--install-on-a-new-pit-machine).

**From a terminal:**

    uv run tools/relay_check.py                         the relay, end to end
    uv run python tools/nexus_probe.py team <key> 3937 --relay
    uv run python tools/nexus_probe.py status <key> --raw     what Nexus actually sent
    PIT_NEXUS_FAKE=1 uv run main.py                     the spec's examples, stepping through a whole event

With `PIT_NEXUS_FAKE=1` the example teams are `100`–`3600`, so pick one as the
active team to see "our" match. `--self-check` parses every example payload
through the real models, so a field Nexus renames shows up there first.

---

## Where the data goes

```
frc.nexus ──webhooks──▶ relay (nexus.bh-stack.com) ──wss push──▶ EventStatus ─┐
          ◀──pull 30s── (while a pit is subscribed)                           │
          ◀──/api/v1 mirror── pits · map · inspection · teams · alliances     │  every 5 min
frc.nexus ◀──direct GET── only while the relay is unreachable ────────────────┤
                                                                              ▼
                            _NexusService  (app/nexus/service.py)
                                                          │
       status_changed · match_changed · now_queuing_changed · pits_changed · …
                                                          ▼
                                     Event Feed panel · (your board here)
```

| File | Role |
|---|---|
| `app/nexus/api.py` | Every endpoint, every schema as a typed model, `FakeClient`. No Qt |
| `nexus-relay/` | The relay — a Cloudflare Worker + Durable Object, TypeScript, its own README |
| `app/nexus/settings.py` | `nexus.json` — event key, cadences, relay address |
| `app/nexus/relay.py` | `RelayLink` — the WebSocket to the relay: heartbeat, watchdog, backoff |
| `app/nexus/service.py` | The `nexus` singleton: relay, fallback polling, freshness, "our match" |
| `app/nexus/alerts.py` | The `alerts` singleton: queue and inspection alerts → strips + banner |
| `app/widgets/queue_banner.py` | The banner along the bottom of both overhead screens |
| `app/widgets/next_match_overlay.py` | The Next Match board — a pinnable face on either overhead screen |
| `app/credentials.py` | The secret folder |
| `app/provision.py` | The pit setup file — make, apply, the drop-in at launch |
| `tools/pit_setup.py` | `make` / `show` / `apply` a setup file |
| `app/widgets/nexus_panel.py` | Control → Pit Systems → Event Feed |
| `app/widgets/network_panel.py` | Control → Pit Systems → Telemetry — the relay's health from both ends |
| `tools/nexus_probe.py` | The CLI; `--relay` goes through the relay |
| `tools/relay_check.py` | The relay end to end, ~35 checks, exit 0/1; `--local` against `wrangler dev` |
| `assets/nexus/examples.json` | The spec's example payloads, for the fake and the self-check |

Screens read the singleton and subscribe to its signals, like every other
cross-window fact in this app:

```python
from app.nexus import nexus

nexus.match_changed.connect(self._on_match)      # Match | None — ours
m = nexus.next_match()                           # or read it now
if m: print(m.label, m.status, m.alliance_of(nexus.our_team))
```

### The signals

All from `app.nexus.service._NexusService`. Payloads are the models below.

| Signal | Carries | Fires when |
|---|---|---|
| `state_changed(str)` | `off` / `idle` / `polling` / `live` / `error` | the feed's own state changes |
| `message_changed(str)` | one operator sentence | with every state change |
| `event_key_changed(str)` | the new key (`""` = off) | the operator changes the event |
| `busy_changed(bool)` | a fetch is in flight | |
| `status_changed(EventStatus \| None)` | the whole snapshot | a **newer** snapshot is adopted, by poll or push; `None` when the feed is turned off |
| `match_changed(Match \| None)` | our next match (or, once all are played, our current one) | its label, status **or times** differ from the last emit; also when the active team changes |
| `now_queuing_changed(str \| None)` | the queuing match's label | it changes |
| `announcements_changed(list[Announcement])` | all current announcements | the list changes |
| `parts_requests_changed(list[PartsRequest])` | all current requests | the list changes |
| `pits_changed(dict[str, str])` | team → pit address | each slow poll; also on team change |
| `pit_map_changed(PitMap \| None)` | the drawn map | each slow poll |
| `inspection_changed(dict[str, InspectionStatus])` | team → status | each slow poll; also on team change |
| `teams_changed(list[str])` | team numbers attending | each slow poll |
| `alliances_changed(Alliances \| None)` | the playoff board | each slow poll; also on team change |
| `events_changed(dict[str, EventSummary])` | every event Nexus lists | after *List events* |
| `relay_changed()` | — | the relay socket's state or the relay's counters move |
| `log(str)` | one line | anything the panel's log should show |

**Derived accessors** — read `config.active_team` at call time, so the team
selector switches every board without a refetch: `our_team`, `our_matches()`,
`next_match()`, `current_match()`, `now_queuing()`, `our_pit()`,
`our_inspection()`, `our_alliance()`, `age_s()` (seconds since Nexus built
the held snapshot), `event_name()`.

### Freshness — the rule everything obeys

**The newest `dataAsOfTime` wins, whichever way it arrived — and it is applied
twice.** The relay applies it to webhooks and its own pulls before storing
anything; the app applies it again in `_offer_status()` to whatever arrives,
pushed down the socket or polled. The spec is explicit that pushes repeat and
arrive out of order, and a poll can land between two pushes; this is the whole
defence. A team (match-status) webhook is folded into the held snapshot **on
the relay** — that one match replaced, the clock moved to the push's — so the
pit only ever receives whole snapshots.

A snapshot for a *different* event key than the configured one is ignored and
logged.

---

## The endpoints

All `GET`, under `https://frc.nexus/api/v1` with `Nexus-Api-Key`, or the relay's
mirror `https://nexus.bh-stack.com/api/v1` with the bearer token.
Errors are the same four everywhere and `Client._explain()` turns them into
the sentence the panel shows:

| Code | Means |
|---|---|
| 401 | No `Nexus-Api-Key` header |
| 403 | Key not recognised, or disabled for abuse (contact@frc.nexus) |
| 404 | On `/event/{key}` itself: the key does not exist. On a **sub-resource** (`/pits`, `/map`, `/inspection`, `/teams`, `/alliances`): the event has nothing of that kind — a demo event has no pit map, alliances do not exist until selection. The service holds the empty value and carries on; only the event's own 404 stops the other fetches (`/events`: no active events at all) |
| 500 | Their fault; try later |

| Endpoint | `Client` method | Returns | Cadence in the app |
|---|---|---|---|
| `/events` | `events()` | `{key: EventSummary}` | on *List events* |
| `/event/{key}` | `event_status(key)` | `EventStatus` | **pushed** by the relay; 30 s poll (`poll_interval_s`) only while it's unreachable |
| `/event/{key}/pits` | `pit_addresses(key)` | `{team: "A1"}` | 5 min (`slow_poll_interval_s`) |
| `/event/{key}/map` | `pit_map(key)` | `PitMap` | 5 min |
| `/event/{key}/inspection` | `inspection(key)` | `{team: InspectionStatus}` | 5 min |
| `/event/{key}/teams` | `teams(key)` | `["100", …]` | 5 min |
| `/event/{key}/alliances` | `alliances(key)` | `Alliances` | 5 min |

Nexus publishes no rate limit. The relay's pulls and the fallback poll are both
30 s; 10 s is the floor the settings file enforces.

### Two things the docs say only in prose

- **Live timing is only real at events that queue with Nexus.** At any other
  event the schedule is still returned but every estimate is meaningless.
  There is no flag; nothing in the payload distinguishes the two. If the
  `estimated*` times never move between polls, that is what is happening.
- **`/events` lists events *registered* for Nexus**, which does not mean the
  event uses any particular feature of it. Events vanish from the list once
  they end.

---

## The models — every field, what it means

Names below are the API's (`camelCase`) and the model's (`snake_case`).
**Every timestamp is Unix time in milliseconds**, kept as `int | None` in
the model; `api.when(ms)` turns one into a local `datetime`. **Every team
number is a string** — `"3937"` — because that is how Nexus keys its maps;
`api.team_str()` is the one conversion.

### `EventStatus` — `GET /event/{key}`, and the live-event webhook body

| API | model | meaning |
|---|---|---|
| `eventKey` | `event_key` | the event |
| `dataAsOfTime` | `data_as_of` | when Nexus built this snapshot. **The tie-breaker.** |
| `nowQueuing` | `now_queuing` | label of the latest match that is queuing; `None` between matches. e.g. `Qualification 24`, `Playoff 8`, `Final 1` |
| `matches` | `matches` | every scheduled match in **play order** — practice, then quals, then playoffs. Empty before the schedule is released. Includes scheduled replays (`replayOf`) |
| `announcements` | `announcements` | current announcements; empty if none |
| `partsRequests` | `parts_requests` | current parts requests; empty if none |

Derived: `matches_for(team)`, `upcoming_for(team)` (ours, not yet on field),
`next_for(team)`, `current_for(team)` (our last `On field` match — Nexus never
marks a match *finished*), `queuing_match()`, `by_status(s)`, `next_break()`.

### `Match`

| API | model | meaning |
|---|---|---|
| `label` | `label` | `Practice 1`, `Qualification 24`, `Qualification 24 Replay`, `Playoff 8`, `Final 1`. `Match.level` reads the first word |
| `status` | `status` | one of the four below |
| `redTeams` / `blueTeams` | `red_teams` / `blue_teams` | team numbers **in station order**. A `None` *entry* is an empty station (practice). A `None` *array* is an alliance not yet decided (playoffs). **Up to four** entries when a backup team is called in playoffs |
| `times` | `times` | see `MatchTimes` |
| `breakAfter` | `break_after` | a break that starts once this match is played, or `None`: `Break`, `Lunch`, `End of day`, `Alliance selection`, `Awards break` |
| `replayOf` | `replay_of` | the label this match replays, or `None` |

**`Match.status`** — `api.MatchState`. The usual walk is

    Queuing soon → Now queuing → On deck → On field

but the spec says **any transition is possible and some events skip
`Now queuing` entirely**. Never assume the previous state; `MatchState.rank()`
orders them for "has it reached X yet" questions. There is no "played" or
"finished" status — a match stays `On field` for the rest of the event, which
is why `current_for()` takes the *last* on-field match of ours.

What each one means at the pit:

| status | what the lead queuer has done | what the crew does |
|---|---|---|
| `Queuing soon` | nothing yet; times are estimates | keep working; `estimated_queue` is the clock |
| `Now queuing` | called the match to queue | robot on the cart, to the queue line |
| `On deck` | the match ahead is on the field | at the field, waiting |
| `On field` | on the field | — |

### `MatchTimes`

All nullable. **The `estimated*` fields collapse onto their `actual*` twin
once that stage is reached**, so a screen can always read the estimate as
"when", and the actual as "has it happened".

| API | model | meaning |
|---|---|---|
| `scheduledStartTime` | `scheduled_start` | the published schedule's start. **Not set for playoffs**, nor when no schedule exists |
| `estimatedQueueTime` | `estimated_queue` | when it will be `Now queuing`. Equals `actual_queue` once past `Queuing soon` |
| `estimatedOnDeckTime` | `estimated_on_deck` | when it will be `On deck`. Equals `actual_on_deck` once on deck or later |
| `estimatedOnFieldTime` | `estimated_on_field` | when it will be `On field`. Equals `actual_on_field` once on field |
| `estimatedStartTime` | `estimated_start` | when "3-2-1-Go" will happen |
| `actualQueueTime` | `actual_queue` | when it became `Now queuing`; `None` while `Queuing soon` |
| `actualOnDeckTime` | `actual_on_deck` | when it became `On deck`; `None` before that |
| `actualOnFieldTime` | `actual_on_field` | when it became `On field`; `None` before that |
| `actualStartTime` | `actual_start` | when the match really started. `None` until it has — **and always `None` at an event not using Nexus AutoQueue** |
| `actualCommitTime` | `actual_commit` | when scores were committed. Same AutoQueue caveat. `MatchTimes.played` is this |

The estimates are recomputed by Nexus on every change and move by seconds
between polls. That is why `match_changed` includes `times` in what it
compares — a start that moved four minutes is news.

### `Announcement`

| API | model | meaning |
|---|---|---|
| `id` | `id` | unique; use it to tell a new one from a repeat |
| `announcement` | `text` | the text |
| `postedTime` | `posted` | when posted |

### `PartsRequest`

| API | model | meaning |
|---|---|---|
| `id` | `id` | unique |
| `parts` | `parts` | what they need, free text |
| `requestedByTeam` | `requested_by` | who, team number |
| `postedTime` | `posted` | when |

### `MatchStatus` — the team-specific webhook body only

`event_key`, `data_as_of`, and one `match`: the match of ours whose status
just changed. Nexus sends it for every status change of any match containing
the team the webhook was registered for. The **relay** folds it into the held
`EventStatus` (`EventRoom.ingestMatch`); the pit never receives one on its own.

### `EventSummary` — `GET /events`

| API | model | meaning |
|---|---|---|
| (key) | `key` | the event key: matches frc.events for official events; offseasons may be frc.events-, TBA-derived, or arbitrary |
| `name` | `name` | |
| `start` / `end` | `start` / `end` | scheduled, ms. `is_live` is *now* between them |

### `PitAddress` — `GET /event/{key}/pits`

`{"3937": "C12"}`. An address is an arbitrary string, usually letter+number.
Empty `{}` when the event has not assigned pits.

### `InspectionStatus` — `GET /event/{key}/inspection`

| API | model | meaning |
|---|---|---|
| `inspected` | `inspected` | passed an initial, **complete** inspection |
| `status` | `status` | one of `api.InspectionState`, below. **`None` at demo events** |
| `queuePosition` | `queue_position` | place in the inspection queue, when queued. `None` at demo events |

Nexus says both `status` and `queuePosition` are cached upstream and **may be
a couple of minutes out of date** — which is why they are on the 5-minute
cadence, not the 30-second one.

| `status` | means |
|---|---|
| `not-started` | never been inspected |
| `queued` | in the inspection queue; `queue_position` says where |
| `in-progress` | an inspector is at the robot |
| `hold` | inspection paused — something to fix, come back |
| `complete` | passed |
| `reinspection` | passed once, now needs looking at again (a change, a weight query) |

### `PitMap` — `GET /event/{key}/map`

The pit area as drawn in Nexus. **Units: 10 to the foot.** Every element is a
`MapElement`: `x`, `y` are the element's **centre**, `width`, `height` its
size, `angle` its rotation about that centre in degrees (`None` → 0). The
map's own `size` becomes `PitMap.width` / `height`.

| section | keyed by | extra fields |
|---|---|---|
| `pits` | pit address (`A1`) | `team` — assigned team number, or `None` if empty |
| `areas` | area id | `label` — `Pit admin`, `Inspection`, `Spare parts`, `EMT`, `Radio config`, `Concessions`, `Machine shop`, `Restrooms`, … |
| `labels` | label id | `label` — `Field`, `Practice field`, `Main gym`, `Auxiliary pits`, … |
| `arrows` | arrow id | `type` — `single` (points up at 0°) or `double` (up and down); `color` — `red`, `blue` (default when null), `purple`, `gray` |
| `walls` | wall id | none — a barrier, or an area teams cannot enter |

`areas`, `labels`, `arrows`, `walls` can each be `null`; the model gives an
empty dict. `pit_of(team)` and `area_named("Inspection")` are the two lookups
a screen wants. Two rendered examples: [simple](https://guides.frc.nexus/assets/images/example-pit-maps/api-simple.png),
[angled](https://guides.frc.nexus/assets/images/example-pit-maps/api-complex.png).

### `Alliances` — `GET /event/{key}/alliances`

A list of alliances, seed order. Each is `[captain, first pick, second
pick]` and a fourth entry when a backup was called. **Any slot is `None`
while selection is in progress; a whole alliance can be `None` too.** The
model gives `Alliance(number, teams)` with `captain`, `picks`, `complete`,
and `Alliances.alliance_of(team)`, `selection_started`, `selection_complete`.

### `GET /event/{key}/teams`

`["100", "200", …]` — team numbers attending.

---

## The relay

A Cloudflare Worker with one Durable Object per event key, in `nexus-relay/`
([its README](nexus-relay/README.md) covers deploy, secrets, cost and checks).
It runs on Cloudflare's edge, with no tunnel and nothing on the pit laptop or
in the home lab.

    frc.nexus ──POST /nexus/webhook──▶ Worker ──▶ EventRoom "<event key>"  (every event, all season)
                                                  • newest snapshot in storage
                                                  • pulls Nexus every 30 s while a pit is subscribed
    pit display ◀──wss /api/v1/event/<key>/ws─────┘ • fans out to every pit socket
    pit display ──GET /api/v1/…──▶ Worker ──edge cache──▶ frc.nexus/api/v1/…

- **Every event arrives; the app chooses.** Nexus offers no webhook filter
  (only "live event status" and per-team "match status"), so the relay keeps
  every event's latest snapshot in its own room. Picking an event on the Event
  Feed panel is instant, even one never looked at before.
- **Every webhook POST is answered 200.** Nexus disables a hook that keeps
  failing, without telling anyone. A wrong token is ignored and counted as
  *refused*, and Telemetry says so in words.
- **The relay pulls on its own** while any pit is subscribed, so a quiet
  event or a broken registration costs latency, never data.
- **Team (match-status) webhooks are merged** into the held snapshot on the
  relay; the pit only ever receives whole snapshots.
- **The relay holds the Nexus API key** and mirrors Nexus's paths under
  `/api/v1/`, so `RelayClient` is `Client` with a different base URL and
  header.

**On the pit:** `RelayLink` (`app/nexus/relay.py`) holds the socket: a 30 s
ping answered by the runtime without waking the object, a 75 s watchdog for
the venue-wifi case where the OS never notices a dead link, and a 1 s → 30 s
backoff. After `fallback_after_s` (60 s) down, the live snapshot is polled
through `_tiered()`: the relay's HTTP mirror first (some venues break
WebSocket upgrades but allow HTTPS), then frc.nexus directly if there's a key.
Every path needs internet; the team carries a hotspot.

**Telemetry** (Control → Pit Systems) shows both ends: this machine's socket
(uptime, messages, reconnects, last error), the relay's counters for the event
(webhooks received / newer / stale / refused, pulls, subscribers), and which
route each piece of data came through.

---

## The Next Match board

**Control → Presentation A or B → Screen content → Next match** pins it.
On the shared chassis, like every other face — but **the whole plate is the
alliance colour**, red card or blue card, white type, so which alliance we
are on reads from the far side of the pit:

    NEXT MATCH
    Qualification 24  [RED 2]
    QUEUE IN
    12:33  at 17:08
    ●QUEUE 17:08 ── ○ON DECK 17:16 ── ○ON FIELD 17:23 ── ○START 17:28
    WITH 1234 5678          VS 100 200 300

The countdown ticks once a second to the **next** thing that happens to us,
chosen from the match's status: `Queuing soon` counts to the queue call,
`Now queuing` to on-deck, `On deck` to on-field, `On field` to the start.
A passed estimate reads `NOW`. The timeline underneath is all four
estimates with the next one lit and the passed ones faded. Once every match
of ours is played it shows the last one as `LAST MATCH · STARTED hh:mm`.
The ledger carries the current time (ticking), what is queuing right now,
our pit, the snapshot's age, and the attribution.

Every time on it is Nexus's *estimate* and moves with each snapshot; at an
event that does not queue with Nexus it will not move at all.

## Alerts — the strips and the overhead banner

`app/nexus/alerts.py` watches the feed and fires three things the pit sees
from across the room. Control → Event Feed → **Alerts** has a switch, a test
button for each, and Clear.

| alert | fires when | strips | banner on both overhead screens |
|---|---|---|---|
| **First queue** | our next match goes `Now queuing` | **sides** flash the alliance colour at 1.5 Hz for 2 s, steady for 1 s, then back to the resting look; the **centre run stays white** (on the W die) throughout | alliance colour: `FIRST QUEUE · Qualification 24 · RED 2`, for 10 s |
| **Second queue** | our next match goes `On deck` | same again | `SECOND QUEUE · …`, 10 s |
| **Inspection passed** | our inspection turns to passed | sides flash green for 5 s, centre white, then back | green: `INSPECTION PASSED`, 10 s |

First and second queue are the two calls a crew hears — *to the queue line*
and *to the field door* — and they are Nexus's `Now queuing` and `On deck`.
Some events skip `Now queuing`; a match arriving straight at `On deck` fires
the second call on its own. Both the strips and the banner are brief on
purpose — a pit's light and screens are ambient, and a colour that stays on
for ten minutes stops being read. The Event Feed panel keeps the standing
fact (next match, status, station) for anyone who missed the ten seconds.

The driver station in the banner is the team's position in `redTeams` /
`blueTeams` — `RED 2` is red alliance, station 2. The banner lies along the
bottom of every face (slides, checklist, judges artwork) because being called
outranks all of them.

Inspection alerts only on a **transition**: the first status the app reads
is the baseline, so a robot that passed yesterday does not celebrate on
every launch. "Passed" is `status == complete` when the event sends a
status — so clearing a re-inspection fires it again — and `inspected` when
it does not. **Demo events send only `inspected`** (measured 2026-09-15:
every team `{"inspected": true|false}`, no `status`, no `queuePosition`), and
`inspected` never un-sets once the initial pass is in, so a re-inspection
set up in the Nexus app is invisible on a demo event. Test the green by
flipping a team from not-inspected to inspected instead.

**The white centre needs controller firmware 2.2.** `SET_COLOR` gained an
optional fifth byte — the white die, per segment — because these strips'
only real white is that die; (255,255,255) on RGB is tinted and three times
the current. Older firmware ignores the byte and the centre run goes dark
for the duration instead — never anything unsafe. The panel says so when it
sees an old controller. Reflash `firmware/pit_leds`.

## What the pit can say — and where each fact comes from

Everything a board might want, with the accessor that answers it. This is the
list to build screens from.

| The pit wants to know | Read | From |
|---|---|---|
| Are we up soon? | `nexus.next_match()` → `.status`, `.times.estimated_queue / _on_deck / _start` | `EventStatus.matches` |
| Which alliance, with whom, against whom? | `m.alliance_of(team)`, `m.red_teams`, `m.blue_teams` | `Match` |
| What is queuing right now? | `nexus.now_queuing()` | `EventStatus.nowQueuing` |
| Is it a replay? | `m.replay_of` | `Match.replayOf` |
| When is lunch / end of day? | `nexus.status.next_break()` | `Match.breakAfter` |
| Did our last match get scored? | `nexus.current_match().times.played` (AutoQueue events only) | `MatchTimes.actualCommitTime` |
| Where is our pit? Where is inspection? | `nexus.our_pit()`, `nexus.pit_map.area_named("Inspection")` | `/pits`, `/map` |
| Are we inspected? Where in the queue? | `nexus.our_inspection()` → `.inspected`, `.status`, `.queue_position` | `/inspection` |
| Who picked us? Who did we pick? | `nexus.our_alliance()` → `.number`, `.captain`, `.picks` | `/alliances` |
| What is pit admin saying? | `nexus.status.announcements` | `EventStatus.announcements` |
| Does anyone need a part we have? | `nexus.status.parts_requests` | `EventStatus.partsRequests` |
| How stale is all this? | `nexus.age_s()` | `dataAsOfTime` |

The Next Match board and the alerts cover the first six rows. The alliance
board, announcements and parts requests are planned; the pit map is not.
