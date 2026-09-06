# Pit Display — Visual Design Brief

**For:** a design-focused AI (or human designer) tasked with redesigning the entire look and feel.
**From:** the engineering side of FRC Team 3937 "Breakaway".
**Date:** 2026-09-04 · Season 2026

You are being asked to design the visual system for a four-screen live installation
that runs in a robotics competition pit. Everything below is real: real screens,
real distances, real content, real constraints. Nothing here is hypothetical.

**The ambition, stated plainly:** this should feel like a Disney park attraction's
interpretive display, not a hobby project. Forward-facing, futuristic, and
genuinely beautiful — while staying strictly inside the Breakaway brand system.
Those two goals are not in tension, and §9 explains how to hold both.

---

## 1. What this thing is

An FRC (FIRST Robotics Competition) team's **pit** is a 10×10 ft booth on a
convention-center floor, next to sixty other pits, under arena lighting, for
three days. It is simultaneously:

- a **workshop** — the robot gets repaired here between matches, on a six-minute clock
- a **showroom** — visitors, families, and other teams walk past constantly
- an **interview room** — award judges arrive unannounced and get ~10 minutes

The Pit Display is a Python/PyQt6 desktop app driving four screens that serve all
three of those jobs at once. One operator runs it from a control panel. Everything
else is either audience-facing or crew-facing.

### The physical installation

| Screen | Hardware | Orientation | Who looks at it | From how far |
|---|---|---|---|---|
| **Presentation A** | ~55" TV, overhead | Landscape 1920×1080 | Visitors + crew | 6–15 ft, glancing |
| **Presentation B** | ~55" TV, overhead | Landscape 1920×1080 | Visitors + crew | 6–15 ft, glancing |
| **Project** | ~32" touch monitor, mounted at standing height | **Portrait 1080×1920** | Visitors, hands-on | 1–3 ft, touching |
| **Control** | Laptop or small touch panel on the workbench | Landscape, variable | The operator only | 1–2 ft |

Two of these panels take **simultaneous touch** (Control and Project) — that is
already solved in code and does not constrain your design, but it tells you both
are genuinely touched, not pointed at with a mouse.

### Ambient conditions that must drive the design

- **Arena lighting is bright and unflattering.** Low-contrast, thin-stroke, or
  pastel designs disappear. Dark backgrounds with luminous content survive best.
- **Nobody stops walking.** An audience slide has ~4 seconds of attention. One
  idea per slide, readable in one fixation.
- **The pit is loud and cluttered.** The screens are the calm thing in the frame.
- **It runs 10+ hours a day, unattended.** Nothing may strobe, flash, or induce
  fatigue. Motion must be slow and purposeful.
- **The crew reads diagnostic screens under time pressure**, mid-repair, sometimes
  with a wrench in one hand. Those screens are not decorative.

---

## 2. The mode system (read this before designing anything)

There is a **global display mode** (set by the operator, affects both presentation
screens at once), and a **per-screen content setting** (affects one screen only).

### Global modes — three

| Mode | What happens |
|---|---|
| `standard` | Both presentation screens run their own content (see below). The default, ~95% of the time. |
| `judges` | Both presentation screens take over with a judge-facing slide deck (operator-advanced) or the 3D CAD viewer. This is the "we are being evaluated right now" state. |
| `lunch` | Both presentation screens show a full-bleed "We'll be right back" holding card. |

### Per-screen content — four faces of `standard`

Each presentation screen independently shows one of:

| Content | What it is |
|---|---|
| `rotation` *(default)* | Auto-advancing slide cycle, 45 seconds per slide, wrapping forever |
| `checklist` | The pit's pre-match checklist, ticked from the control panel |
| `diagnostics` | Live robot health board — "is anything wrong?" |
| `robot_info` | Live robot detail board — "what is wrong, on which motor?" |

The useful pit arrangement is one overhead screen pinned to `checklist` or
`diagnostics` while the other keeps rotating for visitors. **Design for the case
where the two screens are showing different things and are visible in the same
glance.** They should read as one system, not two apps.

### Themes

Every screen independently supports **dark** and **light**. Dark is the default
and the one that matters — light exists for bright-window venues and is currently
a straight token swap. **You may treat dark as the primary design and light as a
faithful derivative, but light must not look like an afterthought.**

### Multi-team

The app supports more than one team. The active team's `primary_color` drives
every accent, live. Today:

- **3937 Breakaway** — `#C82027` red
- **16** — `#1565C0` blue

**Consequence: nothing in your design may assume the accent is red.** Every accent
treatment must survive being swapped to blue at runtime with no other change.
Gradients, glows, and tints derived from the accent are fine — hard-coded red
values in a place that should follow the team are a bug.

---

## 3. The brand system (non-negotiable)

The full spec is in `breakaway_branding.md` and is the authority. Summary:

### Color

| Role | Hex |
|---|---|
| Breakaway Red (the accent) | `#BA141A` |
| Ember (red's shadow) | `#8E1519` |
| Graphite | `#59595B` |
| Carbon (our black) | `#181416` |
| White | `#FFFFFF` |

Neutrals: `#FAF9F8` `#F3F1F0` `#E5E2E1` `#CFCBC9` `#A6A19E` `#6A6462` `#443F3D` (N50→N600)

Status: online `#2E8B7F` · pending `#E08A1E` · fault `#BA141A` · idle `#A6A19E` · success `#17753F`

Extended hues for **data and charts only, never as a second brand accent**:
Clay `#C2571B` · Amber `#E08A1E` · Ochre `#C9A227` · Field Green `#17753F` ·
Pine `#10552E` · Spruce `#2E8B7F` · Harbor `#2B3A67` · Sky `#3F6FB5` · Plum `#6B4E71`

> **⚠ Known discrepancy — please resolve as part of the redesign.**
> `breakaway_branding.md` (v2.1) says the brand red is `#BA141A`.
> `app/brand.py` currently ships `#C82027`, and `app/teams.py` carries `#C82027`
> as team 3937's `primary_color`. These are different reds. Pick one, say which,
> and note it in your deliverable — the code will follow you.

### The red budget — locked

**One red thing per surface.** The score, *or* the CTA, *or* the eyebrow, *or* the
accent device. Everything else is carbon, grey, or white. On carbon and red
grounds, red is replaced by white — the budget still exists, white becomes the
loud color.

This is the single hardest rule to hold in a redesign that wants to feel
"futuristic", because the reflex is to add red glows everywhere. Don't. The
restraint *is* the sophistication.

Two existing decisions that show the rule working, and which must survive:

- **A ticked checklist item is green (`#2E8B7F`), not the accent.** At ten feet a
  red tick reads "fault", not "finished".
- **On the diagnostics boards, red means a latched fault and nothing else.** When
  the robot is clean there is zero red on the board. That's where those screens
  spend their entire red budget, and it's why red appearing is meaningful.

### Type

- **Chakra Petch** — display, headlines, numbers, eyebrows. Weights 500/600/700.
- **Roboto** — body copy, sentences. Weights 400/500/700.
- **JetBrains Mono** — data, figures, timestamps, CAN ids. Tabular.

Scale (px / line-height): Display XL 56/1.0 · Display L 40/1.05 · H1 30/1.1 ·
H2 22/1.25 · Lead 18/1.6 · Body 16/1.6 · Small 14/1.5 · Eyebrow 12/1.4
(Chakra 600, +0.16em, uppercase) · Mono 13/1.5

**Minimum for slide body text at 1920×1080 is 24px.** In practice the audience
screens run far larger than the scale above — the scale is a *ratio* system, and
overhead screens scale it up. Current audience headline is 46px and is arguably
too small; you are invited to go much bigger.

ARDestine is frozen inside logo artwork only. Never set live text in it.

### Shape

Rounded is the shell — **every box has rounded corners, never sharp.**

| Token | Radius |
|---|---|
| Pill (tags, chips) | 999px |
| Button / input | 10px |
| Media / image | 12px |
| Card / callout | 14px |
| Banner / big panel | 18px |

**One radius per shape. Never a sharp box next to a rounded one.**

Spacing scale, 4px base: `4 · 8 · 12 · 16 · 20 · 26 · 34 · 48 · 64`
Hairline 1px `#E5E2E1` · border 1.5px `#CFCBC9` · section rule 2px `#181416`
Focus ring `0 0 0 3px rgba(186,20,26,.4)`

### The four brand devices

Rounded is the shell that's always present. The other three are **accents — one
per surface, never stacked**:

- **Bracket** — four *open* corner L-ticks framing one focal region. Never closes
  into a box. Tick length 12–16% of the short side, clamped 12–28px.
- **Pocket** — a soft filleted triangle marking engineering/robot/CAD content.
  Engineering surfaces only.
- **Trace** — the leading line. A 45° diagonal entry from the nearest corner, one
  mitered bend to flat, flat run ≥1.5× the width of what it leads to, ending in a
  hollow circle terminal at the same stroke weight. Leads the eye to a single
  focal element. Geometry is locked:
  ```
  viewBox 0 0 300 62 · path M10,54 L52,14 L252,14 · circle cx=264 cy=14 r=9
  ```

All four already exist as painted Qt widgets in `app/widgets/brand_widgets.py`.
If your redesign introduces a fifth device, say so explicitly and justify it
against the "one accent per surface" rule — do not smuggle it in.

### Pre-publish check (all nine must clear, per the playbook)

1. Exactly one red thing on the graphic
2. Body text is carbon — no red text on a dark ground
3. Headline in Chakra Petch, sentences in Roboto
4. Official logo file, unaltered, full clear space
5. One radius per element, nothing sharp mixed in
6. One accent device at most, landing on the focus
7. One message per surface
8. Correct size, text above the minimum
9. No minor named or shown without consent

---

## 4. Screen-by-screen inventory

This is what exists today, what each surface is *for*, and what you are being
asked to design. Current implementations are described so you know what you're
replacing — **do not treat them as constraints on the design**, only on the
technology (§7).

---

### 4.1 Presentation A & B — the audience rotation

**Source:** `app/windows/presentation_base.py`, `app/widgets/slide_panel.py`

**Job:** hold a walking visitor's attention for four seconds and leave them with
one idea. This is the most-seen surface in the entire app.

**Structure of one slide today:**
- Eyebrow: `BREAKAWAY 3937 · 01` — Chakra caps, tracked, team accent
- Headline: 46px Chakra 700, centered, word-wrapped
- A **Trace** device centered beneath the headline (220–320px wide, 6px stroke)
- Body: 18px Roboto, centered, max 720px wide
- Footer: dot indicator — inactive slides are 10px circles in N400, the active
  slide is a 30px rounded pill in the team accent

**Content, verbatim from the code.** Screen A carries welcome/robot/awards; Screen
B carries process/outreach/sponsors:

> **Welcome to Breakaway** — Stop by and meet Team 3937 — we'd love to tell you about our season.
> **Our Robot This Year** — Designed and built from the ground up by our student members.
> **Awards & Milestones** — Celebrating the accomplishments that define our team's journey.
> **Come Ask Us Anything** — Questions about FRC, engineering, or our robot? We've got answers.
> **Our Design Process** — Every mechanism starts with student-led research, prototyping, and iteration.
> **Community Outreach** — Beyond the field — workshops, demos, and inspiring the next generation.
> **Thank You, Sponsors** — None of this is possible without the support of our incredible partners.

**Plus dynamically generated "fun fact" slides** built from real robot telemetry
after a log import — same (title, body) shape, appended to the rotation, tagged
internally as `FROM LOG`. Their content rule: *every number is real*, and the
jokes are at our own expense. Example shape: a title like "We Drove 14.2 Miles"
with a one-sentence body. Design them as first-class slides, but consider whether
a data-derived slide should look visually distinct from an authored one.

**Design asks:**
- The slide is currently a centered stack with a lot of empty space. It is
  correct and boring. **Make it feel designed** — this is the single biggest win
  available in the whole app.
- Consider slide *archetypes* rather than one template: a statement slide, a
  number/stat slide, a sponsor/logo slide, a data-fact slide. The code can
  support multiple layouts; today it doesn't have them because nobody designed
  them.
- The transition between slides is currently an instant `QStackedWidget` swap.
  A designed transition is available and wanted (see §7 for what's possible).
- The dot indicator is functional and unremarkable. It's also the only thing on
  screen that communicates "there's more coming" — consider whether it should
  do more (progress through the 45s? position in a named cycle?).
- Screen A and Screen B are visible simultaneously. Should they be visually
  identical templates, or should they be distinguishable at a glance?

---

### 4.2 Lunch overlay — the holding card

**Source:** `app/widgets/lunch_overlay.py`

**Job:** the pit is unattended. Say so warmly, and keep the brand on screen.

**Current design**, painted entirely in `paintEvent` (bypasses Qt layout so the
type can genuinely fill a 55" panel — a real constraint that produced this
approach, see §7):

- Logo (`assets/logos/2026 Wordmark.png`) at 22% of screen height, top 6%
- Eyebrow: team name + number, Chakra caps, tracked +30%, in team accent
- A rounded pill divider bar in the team accent, at 34% height
- Headline: **"We'll Be Right Back"** — auto-fit Chakra 700, at 40% height
- Sub: *"Our team is on a lunch break and will return shortly. / Thank you for
  stopping by our pit!"* — Roboto, muted, at 64% height

Everything is positioned by fraction-of-height constants, so it is resolution
independent by construction.

**Design asks:** This is a full-bleed, zero-interaction, long-dwell surface — the
one place in the app where you can be genuinely cinematic. It's currently a
centered text stack. It could be the most beautiful screen in the installation.
Slow ambient motion is acceptable here in a way it isn't elsewhere.

---

### 4.3 Judges overlay — the evaluation deck

**Source:** `app/widgets/judges_overlay.py`

**Job:** during a judge visit, both overhead screens become a presentation deck
the operator advances by hand from the control panel.

**Current design:** a header bar plus a full-area image label. Slides are
**PNG/JPG files dropped into `assets/judges_slides/`**, numbered (`01_intro.png`,
`02_robot.png`), sorted alphabetically. The app scales them to fit and does not
author them.

**Design asks — this is two separate deliverables:**

1. **The chrome around the image.** Header, letterbox treatment, slide position
   indicator, and the empty state (currently plain text reading *"No slides
   loaded. Drop images into assets/judges_slides/ then click Reload on the
   control screen."*). The chrome must not compete with the slide inside it.
2. **A slide template the team designs their actual deck in.** These slides are
   made in whatever tool the students use and exported as images. Giving them a
   real 1920×1080 template — title slide, content slide, image slide, data slide
   — is high value and currently missing entirely. This is a straight
   graphic-design deliverable with no code implications.

There is also a **judges CAD mode**: the same screens can switch to the 3D robot
viewer with the operator focusing individual subsystems from the control panel.
See §4.7.

---

### 4.4 Checklist overlay — the crew's pre-match list

**Source:** `app/widgets/checklist_overlay.py` (400 lines)

**Job:** the pre-match checklist, on an overhead screen, ticked from the control
panel. **Nobody ticks it on the display** — the overhead screens are out of reach
above the workbench.

**Current design:**
- Header with the team accent, item count, and a progress bar in the accent
- Rows: a painted tick box (empty outline → filled with a check), item text
- Done rows go **green (`#2E8B7F`)**, not accent-red — see §3
- Everything is sized from the live widget height, including an explicit fixed
  height per row, so it's legible on both a 24" cart monitor and a 55" panel
- Empty state points at the control panel rather than showing an empty box to a
  pit full of visitors
- **Ships with zero items by design.** The team writes their own; the app must
  never invent checklist content.

**Design asks:** progress is the emotional core of this screen — a crew glancing
up wants "how close are we?" in one fixation. The current progress bar is a
literal bar. Consider what a genuinely well-designed completion state looks like
at ten feet. Also: a long list eventually runs off the bottom (the stated fix is
"fewer items", not smaller type) — is there a better answer visually?

---

### 4.5 Diagnostics board (A) & Robot Info board (B) — the crew's live data

**Source:** `app/widgets/diagnostics_overlay.py` (542 lines),
`app/widgets/robot_info_overlay.py` (458 lines), data from `app/robot/diagnostics.py`

**Job:** *"fun facts talk to visitors; these talk to the crew."* Every tile is
something you'd act on in the six minutes before the next match. These are built
from real telemetry imported off the robot (millions of samples, rolled up).

**The split is by question, not by column count:**

| | asks | shows |
|---|---|---|
| **A — Diagnostics** | *is anything wrong right now?* | A headline taking the worst status on the board, a grid of vitals tiles, a subsystem status strip |
| **B — Robot Info** | *what is wrong, on which motor, and what log is this?* | The fault list by name, a per-motor table with CAN ids and names, and log provenance |

**Concrete content:**
- **Vitals tiles** — battery sag (e.g. `12.39 V`), peak current, max temperature,
  each with a caption and a status dot. Painted status dots, green/amber/red/grey.
- **Subsystem strip** — mechanism name, amps, temperature per subsystem.
- **Fault rows** (B) — what latched, how many motors, which ones.
- **Motor table** (B) — the operator's typed names (`Front-Left Drive`), falling
  back to `TalonFX 11` where nobody has named it, plus CAN id and figures.

**Hard layout facts the design must respect:**
- Minimum vitals tile width **250px**; maximum **4 columns** — past four across
  it stops being glanceable and becomes a spreadsheet.
- Minimum subsystem column **300px**; minimum motor column **420px** (below that
  the name elides, and the name is the part that matters).
- Row counts are **banded by panel height**, not measured: ≥900px → 6 faults / 8
  motors / show CAN ids; ≥760 → 5/6/yes; ≥620 → 5/6/no; below → 4/4/no.
  (An earlier version measured settled geometry and silently hid rows there was
  room for. Bands are deliberate.)
- Lists are sorted worst-first upstream, so what a cap drops is always healthy.
- **Numbers use the mono face**, tabular.

**Design asks:** these two are the most information-dense surfaces in the app and
currently the most utilitarian. They're also where "futuristic" is most
*earned* — this is live machine telemetry on an overhead panel. Make them look
like an instrument, not a table. But: red still means fault and only fault, and
a clean robot must still show a board with no red on it.

---

### 4.6 Project screen — the interactive Impact board

**Source:** `app/widgets/interactive_board.py` (846 lines)

**Job:** the visitor-operated touch kiosk. **Portrait, 1080×1920, 32", at standing
height.** This is the only screen a stranger will physically touch.

**Current structure:**
- Horizontal tab bar across the top
- Single scrolling column beneath
- Persistent sponsors strip pinned at the bottom
- Every program/award card is tappable and opens a full detail view with a
  `‹ Back` control
- Type scales with screen width so it stays legible from across a pit

**Tabs and real content** (this is genuine, from the team's FIRST Impact
documents — treat it as real copy to design around, not lorem ipsum):

- **About** — who Breakaway is
- **Legislation** — *"The Bill — Act 472"*: the team pioneered and passed Act 472,
  Arkansas's first state funding for competitive robotics teams, signed by the
  Governor in April 2025 after 24+ months of legislative work — Capitol pit
  tours, a Competitive Robotics Capitol Day with 6 FRC and 4 VEX teams, testimony
  before Senate and House Education Committees. Also *"Museum of Discovery"*: a
  10+ year partnership grown from 1 to 3 annual events, 3,500+ people reached,
  $10K+ raised.
- **Outreach** — Girls in STEM Camp and other community programs
- Plus awards and sponsors sections

Each card has a `name`, a short `blurb`, and a longer `detail` shown on tap.

**Design asks:**
- **Portrait is the whole design problem here** and the current layout doesn't
  really exploit it — it's a landscape design rotated. A 1080×1920 canvas at
  standing height is closer to a museum interpretive panel than a web page.
- Touch targets: **44px minimum**, and realistically much larger at this size.
- The Act 472 story is genuinely remarkable and is currently presented as a card
  in a list. It probably deserves a hero treatment.
- Card → detail → back is the only navigation. Make the transition feel intentional.
- The sponsors strip is permanently visible. Design it as a considered element,
  not a footer.
- **No hover states.** A touchscreen sends no leave event; anything that paints
  on hover stays painted forever. Design for press/release only.

---

### 4.7 CAD viewer — the 3D robot

**Source:** `assets/cad_viewer/index.html` (324 lines) + `viewer.js` (712 lines),
Three.js, served over a local HTTP server on port 8765, hosted in a `QWebEngineView`

**This is the one surface that is real HTML/CSS/JS**, so you can design it with
full web fidelity — CSS animations, filters, blend modes, custom shaders, all
available. It appears in two places:

1. **Project screen** — interactive. Free orbit, pinch-zoom, two-finger pan,
   tap a subsystem to isolate it.
2. **Presentation screens in judges mode** — the operator drives subsystem focus
   from the control panel and the audience screens follow.

**Existing UI elements** (ids you'd be restyling):
- `#subsystem-label` — a `SUBSYSTEM` tag over the focused subsystem's name
- `#facts-panel` / `#facts-title` — a `KEY FACTS` panel
- `#sub-bar` — the subsystem selector bar
- `#status-overlay` — empty state: *"No Model Loaded — Upload a .glb file using
  the Control Screen to get started."* (currently a `⬡` glyph)
- `#libs-error` — library load failure state (currently a `⚠` glyph)

Subsystems are defined in `assets/cad/subsystems.json`, each with a
`display_name` and its own `accent_color` — so this screen has a *secondary*
accent system beyond the team color. When a subsystem is focused, the LED strips
in the physical pit echo that subsystem's accent color.

**Design asks:** this is the most inherently futuristic surface in the app and
the one with the fewest technical limits. It's also the one that most risks
looking like generic sci-fi HUD. Make it look like *Breakaway's* CAD viewer.
The empty and error states currently use emoji-adjacent glyphs and should be
designed properly.

---

### 4.8 Control screen — the operator panel

**Source:** `app/windows/control_screen.py` (1212 lines), plus
`led_panel.py`, `music_panel.py`, `robot_panel.py`, `checklist_panel.py`,
`cad_upload_panel.py`, `admin_bar.py`

**Job:** one operator drives the entire installation from here. Dense, functional,
touched. **This screen is never seen by visitors.** It should be beautiful, but
beauty here means clarity under time pressure, not spectacle.

**Current layout:**

```
┌──────────────────────────────────────────────────────────────────┐
│ [3937] BREAKAWAY   [Standard][Judges][Lunch]        Team ▾       │ 76px top bar
├──────────────────────────────────────────────────────────────────┤
│ (admin bar — 46px strip, appears inline when unlocked)           │
├────────────────┬─────────────────────────────────────────────────┤
│ SCREENS        │                                                 │
│  Presentation A│  PRESENTATION A                                 │
│  Presentation B│  Configure settings for the Presentation A…     │
│  Project    [●]│  ───────────────────────────────────────────    │
│  Control  AlwaysOn│ Appearance      Dark/Light        [toggle]   │
│                │  Standard Content  [Rotation ▾]                 │
│ PIT SYSTEMS    │  Checklist editor…                              │
│  LED Strips [●]│  Standard Slides   (list, click to jump)        │
│  Music         │  Judges Slides     (thumbnail strip)            │
│  Robot Logs    │  Judges CAD        (subsystem buttons)          │
│  220px sidebar │                    scrolling settings column    │
└────────────────┴─────────────────────────────────────────────────┘
```

**Elements worth knowing about:**

- **Top-bar brand chip** — a 58×44 rounded frame filled with the team accent,
  showing the team number. It is *also the admin door*: clicking it opens the
  admin bar. It carries no visual affordance for that today.
- **Mode buttons** — three, 124×42, ghost when inactive / accent-filled when
  active.
- **Screen cards** — 56px sidebar rows. Active state = surface fill, white text,
  3px accent left border. Each has a power toggle that shows/hides that window.
  The Control row shows a green "Always On" instead.
- **Admin bar** — a **46px** inline strip between the top bar and the body, not a
  modal and not a separate screen, deliberately: the operator watches the gated
  controls appear as they unlock. It grows to 85px only while the change-password
  form is open. **It must stay a thin strip** — it appears over the operator's
  actual work. Gated controls are *hidden*, not disabled, so nobody goes looking
  for a password.
- **Standard slide picker** — a list of every slide in that screen's rotation
  with a two-digit mono number, title, and body preview. Entries carry tags:
  `FROM LOG` (amber-green, generated from telemetry) or `LIVE BOARD` (pending
  amber, the diagnostics board). The live slide is highlighted; clicking jumps.
- **Judges thumbnail strip** — 96×54 thumbnails in a horizontal scroller, active
  one bordered in the team accent.
- **LED panel** — brightness, speed, named looks, color, follow-team, save.
  The on/off kill switch is never gated; everything else is.
- **Music panel** — transport, queue, local library browser, volume, duck, and a
  **10-band graphic equalizer** with presets. The EQ is entirely admin-gated.
  A ten-band EQ is a real design object and currently isn't treated as one.
- **Robot log panel** — import button, progress/results, and the **CAN-id name
  table** where the crew types `Front-Left Drive` against `TalonFX 11`. Import
  results report both stored and skipped row counts.

**Design asks:**
- Sidebar + settings column is a sound information architecture. The visual
  execution is generic dark-app.
- **The operator is often standing and sometimes touching.** Density is right,
  but hit targets and spacing should acknowledge fingers.
- The mode buttons are the highest-stakes control in the app — switching to
  Judges mode mid-visit is a live action. Should they feel more consequential?
- The EQ, the LED controls, and the CAN-id table are three genuinely different
  kinds of interface currently wearing the same clothes.
- The admin bar's appearance/disappearance is an important moment. It's currently
  a `setVisible()`.

---

## 5. Content inventory summary

Everything the design system has to hold:

| Content type | Where | Notes |
|---|---|---|
| Authored slide (title + body) | Presentation A/B | 7 today, editable in code |
| Generated fact slide (title + body) | Presentation A/B | From telemetry, count varies |
| Full-bleed holding card | Lunch | 1 |
| Imported image slide | Judges | PNG/JPG, unbounded count |
| Checklist (header, progress, rows) | Checklist | Team-authored, starts empty |
| Vitals tiles + status strip | Diagnostics | Live |
| Fault list + motor table | Robot Info | Live, mono figures |
| Tabbed cards + detail views | Project (portrait) | Real Impact copy |
| Sponsors strip | Project | Persistent |
| 3D model + subsystem HUD | CAD (web) | Per-subsystem accents |
| Dense operator controls | Control | Sidebar + settings column |
| 10-band EQ | Control → Music | Admin-gated |
| Data table (CAN ids ↔ names) | Control → Robot Logs | Editable |

---

## 6. Motion

There is essentially **no motion design today** and this is the largest single
gap between where the app is and "Disney level".

Where motion is wanted:
- **Slide transitions** (Presentation A/B, every 45 seconds, all day)
- **Mode changes** — standard → judges is a significant state change and
  currently snaps
- **Checklist ticks** — an item completing is a small moment of satisfaction
- **Status changes on the diagnostics boards** — a fault appearing should be
  noticed without being alarming
- **Card → detail on the project screen** (touch, so it should feel physical)
- **Admin bar reveal** on the control screen
- **Ambient motion on the lunch card** — the only long-dwell zero-interaction surface

Where motion is **forbidden**:
- Anything that repeats faster than ~2 seconds anywhere in a 10-hour day
- Anything that flashes, strobes, or pulses at attention-grabbing rates
- Motion on the diagnostics boards that competes with an actual fault appearing
- Motion behind text a visitor is trying to read in four seconds

Please specify durations and easing curves explicitly. The physical LED strips in
the pit already animate on their own hardware loop and echo mode and subsystem
changes — screen motion and light motion should feel like one system.

---

## 7. Technical constraints (please design within these)

This app is **PyQt6**, not a browser. Three different rendering paths exist and
they have very different capabilities. **Tell us which path each part of your
design targets.**

### Path A — Qt Style Sheets (QSS)

A CSS *subset*. Used for the app-wide theme (`app/theme.py`) and per-widget
styling.

**Available:** `background-color`, `color`, `border`, `border-radius`, `padding`,
`margin`, `font-family`, `font-size` (px), `font-weight`, `letter-spacing`,
`qlineargradient(...)` and `qradialgradient(...)`, `:hover`, `:pressed`,
`:checked`, `:disabled`, `::item`, `::section`, `::handle`, object-name and
property selectors, border-image for 9-slice.

**NOT available:** `box-shadow`, `backdrop-filter`, `filter`, `blur`, `opacity`
on arbitrary widgets, CSS transitions, CSS animations, `transform`, flexbox/grid
(Qt layouts do this instead), pseudo-elements like `::before` / `::after`, web
fonts.

**Practical consequences:**
- **Glassmorphism / frosted blur is not directly available in QSS.** It can be
  faked with a `QGraphicsBlurEffect` on a snapshot, which is expensive and
  fragile. If your design depends on blur, flag it — we'd rather know than
  discover it.
- **Drop shadows** need `QGraphicsDropShadowEffect` per widget (works, but costs
  a compositing pass) or must be baked into a painted background.
- **Glows** are best done as painted radial gradients, not shadows.

### Path B — Custom `QPainter` painting

Anything can be drawn by hand in a widget's `paintEvent`. This is how the brand
devices, the status dots, the tick boxes, the slide dots, and the entire lunch
overlay already work. **Effectively unlimited** — arbitrary paths, gradients,
strokes, clipping, compositing modes, antialiasing.

The lunch overlay is painted rather than laid out for a specific reason worth
knowing: **Qt's label size-hint system capped the rendered font regardless of the
pixel size set**, so filling a 55" panel with type required bypassing the layout
engine. Any design that needs type to genuinely fill a screen will take this path.

If your design is ambitious, **assume it will be hand-painted.** That is the
normal answer here, not the exotic one.

### Path C — Web (the CAD viewer only)

`assets/cad_viewer/` is real HTML/CSS/JS in a Chromium view. Full modern CSS,
CSS animations, filters, blend modes, WebGL/Three.js. Design this one like a web
page.

### Animation

`QPropertyAnimation` / `QVariantAnimation` / `QGraphicsOpacityEffect` drive any
numeric property over time, with the standard easing curves (`InOutCubic`,
`OutBack`, `OutExpo`, etc.). Opacity crossfades, slide-ins, scale, and painted-
value animation all work. Specify easing by name and duration in ms.

### Layout traps that shape what's buildable

Documented from real bugs, so you know why some things are the way they are:

- `addWidget(w, alignment=…)` lays a widget out at its `sizeHint()`, ignoring
  `heightForWidth` — so **a word-wrapped label gets a one-line height and long
  body text clips.** Centering is done with stretch rows instead.
- A custom painted widget with no `sizeHint()` gets **zero width and silently
  never paints**.
- Long unwrapped labels force their whole panel wider than the window.

None of these constrain your design. They constrain how it gets built, and
they're the reason some current layouts look conservative.

### Fonts

Chakra Petch, Roboto, and JetBrains Mono are bundled in `assets/fonts/` and
loaded at startup by globbing `*.ttf`. **Adding a face is as easy as dropping a
TTF in** — if your design wants a fourth face, that's a real option, but weigh it
against the brand's two-face discipline. Mono must be specified as a *stack*
(JetBrains Mono → Menlo → Consolas → DejaVu Sans Mono → Courier New) because
naming a family Qt can't resolve costs ~40ms on every font construction.

### Runtime environment

- The pit machine is **Windows**; development is on macOS. Test assumptions on
  Windows.
- No internet in the venue. Everything must be local — **no CDN fonts, no
  remote assets, no web-hosted anything.**
- The app runs for 10+ hours. Avoid per-frame work that accumulates.

---

## 8. Assets that exist

- `assets/logos/2026 Wordmark.png` — the only logo file currently in the repo.
  **Check with Tao for current logo artwork before designing around it.** Never
  source a logo from a slide deck, an old post, the website, or an image search.
  Never recolor, stretch, outline, box, crop, or redraw it, and never use it as a
  device.
- `assets/fonts/*.ttf` — Chakra Petch, Roboto, JetBrains Mono
- `assets/judges_slides/` — operator-supplied images
- `assets/cad/` — `.glb` robot models + `subsystems.json`
- `assets/cad_viewer/` — the Three.js viewer

Sizes elsewhere in the brand system, for reference: Instagram 1080×1350 /
1080×1080 / 1080×1920 (10% story margin); slides 1920×1080; print 8.5×11in at
300dpi with 0.25in safe margin; banners CMYK with 0.125in bleed and logo ≥1in.

---

## 9. The ambition — what "Disney level" means here

The reference point is a Disney park's interpretive and queue-line media: content
that is unmistakably branded, technically ambitious, and *calm*. It never shouts.
It rewards a long look but works in a glance. It feels expensive because of
restraint and craft, not because of effects.

**Concretely, for this project:**

- **Futuristic ≠ sci-fi HUD.** No neon-cyan grids, no glitch text, no scanlines,
  no hexagon meshes, no faux-terminal chrome. The brand is Carbon, one red,
  Chakra Petch, and rounded geometry. Future comes from precision, depth,
  material, and motion — not from decoration.
- **The brand thesis is "precision under speed."** Confident, disciplined, roomy.
  Every design decision should be defensible against that sentence.
- **Restraint is the differentiator.** The red budget is the clearest expression
  of this. A design that spends its red once and holds everything else in carbon
  and white will read as far more premium than one that glows everywhere.
- **Depth over ornament.** Layering, considered elevation, real material logic,
  and light — not borders and badges.
- **Type is the primary design element.** Two faces, a strict scale, and enormous
  sizes on the overhead panels. Most of the beauty available here is typographic.
- **Data is content.** The telemetry boards, the EQ, the CAN table, the fun-fact
  slides — treat live data as something to design *with*, not to tabulate.
- **It has to survive a convention center.** Under arena light, at fifteen feet,
  next to sixty other pits, for three days.

---

## 10. What we need back

In priority order. Deliver what you can; partial is useful.

1. **A stated resolution of the red discrepancy** (`#BA141A` vs `#C82027`), and a
   final token table we can paste into `app/brand.py` — including any tokens you
   add (elevation, glow, gradient stops, motion values).
2. **The audience slide system** — slide archetypes, full-bleed comps at
   1920×1080, dark and light, with the transition specified.
3. **The lunch card** — one comp, 1920×1080.
4. **The two diagnostics boards** — comps at 1920×1080, showing both a clean
   robot (no red) and a faulted robot (red present, single focal).
5. **The checklist overlay** — comps at 1920×1080: empty, partial, complete.
6. **The project screen** — comps at **1080×1920 portrait**: tab view, card list,
   card detail, sponsors strip.
7. **The control screen** — top bar, sidebar, settings column, admin bar (both
   46px and 85px states), and one comp each of the LED panel, the 10-band EQ, and
   the CAN-id table.
8. **The CAD viewer HUD** — as web CSS, including the empty and error states.
9. **A judges slide template** — 1920×1080, title / content / image / data
   variants, as a file the students can author in.
10. **A motion spec** — every transition listed in §6 with duration, easing, and
    what moves.

**Please annotate each deliverable with its render path (§7):** QSS, custom
`QPainter`, or web. If something needs a capability Qt doesn't have, say so
plainly and propose the nearest thing that does — we would much rather redesign
one element than discover it at build time.

---

## 11. Hard rules — do not violate

1. **One red thing per surface.** No exceptions, no "but it's a glow".
2. **Red on the diagnostics boards means a latched fault and nothing else.**
3. **A completed checklist item is green, not the accent.**
4. **Nothing may assume the accent is red** — it swaps to blue at runtime.
5. **Every box is rounded.** One radius per shape. Never a sharp corner next to a
   rounded one.
6. **Chakra Petch for display and numbers, Roboto for sentences.** ARDestine
   never as live text.
7. **One accent device per surface** — Bracket, Pocket, or Trace, never stacked.
   Rounded is the shell underneath and doesn't count.
8. **No red text on a dark ground** (2.8:1 — the brand explicitly forbids it).
9. **The logo is never a device**, never recolored, never redrawn.
10. **No hover-dependent states on touch surfaces** (Project, Control).
11. **No internet-dependent assets.** Everything local.
12. **Nothing strobes, flashes, or pulses fast.** This runs for ten hours in a
    room full of people.
13. **Slide body text ≥24px at 1920×1080**; touch targets ≥44px.
14. **Do not invent checklist content.** The list ships empty on purpose.
15. **Every number on a fun-fact slide is real.** Nothing invented or rounded for
    effect — a visitor who asks "is that true?" gets a yes.

---

## 12. Reference files in this repo

| File | What's in it |
|---|---|
| `breakaway_branding.md` | The brand system, authoritative |
| `CLAUDE.md` | Full architecture, every subsystem, every trap |
| `app/brand.py` | Current tokens as code |
| `app/theme.py` | The QSS template, light + dark |
| `app/widgets/brand_widgets.py` | Bracket, Pocket, Trace, Rounded as Qt widgets |
| `DATABASE.md` | Storage schema (context for what data exists) |
| `MEDIA_GUIDE.md` · `CAD_GUIDE.md` · `ADMIN_GUIDE.md` | Operator instructions |
