# Design files

The app's real screens as layered SVGs. Code exports them, you change them in
Illustrator and save over the same file, and the code reads back exactly
what you changed. One tool does all of it: `uv run tools/design.py`.

```
design/
  screens/            the shared files: one SVG per screen (edit these)
    overhead-master.svg   the frame every overhead face shares
    overhead-<set>.svg    each matched set: Screen A | Screen B
    refs/               pictures: the frame under each face (linked), and how
                        the app draws each screen (regenerated, not committed)
  inbox/              anything else: mock-ups, IDML, PNGs, ideas
  out/                generated: diffs, shots (not committed)
```

## Once, on your Mac

1. **Install the brand fonts** so Illustrator has them: open each file in
   `assets/fonts/` (Chakra Petch, Roboto, JetBrains Mono) and click
   *Install* in Font Book. Without them, Illustrator swaps the type.
2. **Illustrator's SVG save settings.** Use *File → Save As → SVG* with
   *More Options*, set once:
   - SVG Profile: **SVG 1.1**
   - Fonts → Type: **SVG** (keeps text as text; never *Convert to outline*)
   - Images: **Link** (keeps the frame picture linked, not copied in)
   - Object IDs: **Layer Names** (keeps the layer names)
   - Decimal: **2**
   - **Responsive: off** (keeps the pixel size)

## The files

**The overhead screens are always a matched set** (`app/overhead.py`): each
file is one artboard with Screen A on the left and Screen B on the right,
the two screens as the pit shows them together.

| File | What |
|---|---|
| `overhead-master` | **the frame**: ground, plate, header band, footer ledger, the rail. Every overhead face sits in it, so it's here once |
| `overhead-slide-description` | **every Slide's Description**: eyebrow, the Trace, the bold headline, the catch line. One layout for all of them, so it's here once |
| `overhead-slide-figures` | a Detail: a few numbers side by side (our reach, Awards & Milestones, the robot's logs) |
| `overhead-slide-quality` | a Detail: the Quality Award leaderboard |
| `overhead-slide-bk_seasons` | a Detail: season by season |
| `overhead-slide-record` | a Detail: one of Breakaway's records |
| `overhead-slide-fact` | a Detail: a "Did you know?" fact |
| `overhead-slide-dataset` | a Detail: any other of home's datasets, as a table |
| `overhead-next_match` | Nexus queue · our schedule |
| `overhead-robot` | Diagnostics · Robot Info |
| `overhead-analysis` | the analysis board · Robot Info |
| `overhead-checklist` | a checklist each |
| `overhead-lunch`, `overhead-judges` | the modes that take over both screens |
| `control-<panel>` | the control screen with that panel open, full length |
| `project-board` | the pit-front portrait panel, full length (the CAD viewer is a dashed box: it's a web view) |

**The Slides** (`app/deck.py`) are everything the overhead screens tell
visitors, fifty or more of them: one screen the **Description**, the other
the **Detail**, swapping sides Slide to Slide. They're content, not files:
the Description is one layout (`overhead-slide-description`), and each kind
of Detail is one layout (`overhead-slide-<detail>`). In each of those files
the half that belongs to the other file is a picture (*Reference: this half
is edited in …*), so every layout's vectors exist exactly once. The words of
each Slide are edited in the app (Screen Wording → "Slides · …"), not here.

**Every face file holds only that face's own content.** Under it is a
picture of its frame (the *Reference* layer, linked from `refs/`). It's there
so you see the face in place; it isn't editable. **Change the frame in
`overhead-master.svg`**, and every face follows. A face that tints its whole
plate (the Next Match card's alliance colour) carries the tint itself.

**Layers.** Every file is a tree of named groups, the way the screen is
built: *Screen A → DiagnosticsOverlay → Stage → Text Bridge Brownout*,
*Shape #BA141A*, and on the control screen every panel, label and button by
name. Name any group you add: the name tells me what it is.

## The loop

```bash
uv run tools/design.py export all            # every screen (never over your edits)
# … edit design/screens/overhead-robot.svg in Illustrator, Save …
uv run tools/design.py diff overhead-robot    # what you changed, against the code
```

`diff` paints the screen from the code again and lists:

- **text** moved (by how much), resized, recoloured, re-fonted, reworded,
  added (where, which font, size, colour) or removed;
- **colours** you brought in or dropped;
- **where pixels changed**, as boxes on a picture (`design/out/<name>.diff.png`:
  the code's version | yours), named by screen ("Screen B, top left");
- **a brand check** of your file: a colour that's a token drifted
  (`#BB151A` is Breakaway Red), off-palette colours, fonts outside the
  three faces, red text on a dark ground, more red than the code's screen.

The report is also saved as `design/out/<name>.changes.md`. **That's the
change request:** hand it to me ("build what's in design/out/overhead-robot.changes.md")
or build it yourself. When the code matches your file, `diff` says *The
code matches this file*, and `export <target> --force` makes the code's
version the new starting point.

`export` never writes over a file you've changed (it keeps each export's
hash in `screens/.exports.json`); `--force` does, on purpose.

## Other commands

```bash
uv run tools/design.py list                        # every target
uv run tools/design.py watch overhead:robot         # a window that redraws on every code save
uv run tools/design.py shot control:music --compare design/inbox/music.png
                                                   # your mock-up | the app | the two overlaid
uv run tools/design.py lint design/inbox/            # brand check: SVG, IDML, CSS, Python
uv run tools/design.py export overhead:robot --theme light
```

Targets: `overhead:<master|set|slide:description|slide:<detail>|lunch|judges>`,
`control:<panel>`, `project:board`. Options: `--theme light`,
`--size WxH` (control and project only).

## What it can't carry

- **Letter-spacing.** Qt's SVG writer drops tracking, so the spaced capitals
  (eyebrows, the header's mono labels) look tight in the file. The app still
  tracks them; leave them, or say what you want in a layer name.
- **Pictures stay pictures:** sponsor logos, judges artwork, and the frame
  reference. Logos come from the official files only (see the brand rules);
  never trace or redraw one here.
- **Shapes are matched by look, not one by one.** A moved or reshaped box
  shows up in *Where pixels changed* and as colours, not as "rect 3 grew
  10 px". Text is matched exactly.
- **The data is test data** (a test robot log, a sample event, team 3937),
  the same every export, never a pit's real data.
- `.ai` and `.indd` can't be read: save as SVG (Illustrator) or IDML
  (InDesign) into `inbox/`, which `lint` checks.
