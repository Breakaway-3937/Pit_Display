# Breakaway 3937 — Brand System

Machine-readable extract of the Brand Playbook, v2.1 · 2026 season.
Same values as `breakaway.css` and the playbook page. Paste into a README,
a design doc, a Notion page, or an AI prompt when you need the system as text.

## Colour

### Brand

| Name | Hex | Token | Job |
|---|---|---|---|
| Breakaway Red | `#BA141A` | `--bw-red` | The accent. One per graphic. |
| Red hover | `#A21117` | `--bw-red-hover` | Hover state only. |
| Ember | `#8E1519` | `--bw-ember` | Red's shadow: pressed states, red-on-red rules, chart peaks, errors. |
| Graphite | `#59595B` | `--bw-graphite` | Brand grey. Secondary chart series, secondary fields. |
| Carbon | `#181416` | `--bw-carbon` | Body text and dark grounds. Our black. |
| White | `#FFFFFF` | `--bw-white` | The ground. Carries the layout. |

### Neutrals

| Token | Hex | Job |
|---|---|---|
| N50 | `#FAF9F8` | Page background |
| N100 | `#F3F1F0` | Panel / box fill |
| N200 | `#E5E2E1` | Hairlines, chart context bars, table rules |
| N300 | `#CFCBC9` | Card and input borders |
| N400 | `#A6A19E` | Disabled / muted |
| N500 | `#6A6462` | Secondary text |
| N600 | `#443F3D` | Tertiary ink |

### Full primary span

Non-red hues, for charts and data only — never as a second brand accent.
All are usable as fills on white; the "on white" column is the text contrast ratio.

| Name | Hex | Hue | On white | Use |
|---|---|---|---|---|
| Breakaway Red | `#BA141A` | red | 6.6:1 | The focus series |
| Ember | `#8E1519` | deep red | 9.1:1 | The single peak / record |
| Clay | `#C2571B` | orange | 4.6:1 | Categorical |
| Amber | `#E08A1E` | amber | 2.4:1 | Pending status, fills only |
| Ochre | `#C9A227` | yellow | 2.2:1 | Fills only, never text |
| **Field Green** | **`#17753F`** | **green** | **5.2:1** | **Our green: success, growth, outreach** |
| Pine | `#10552E` | deep green | 8.0:1 | Deep end of a green ramp, green-on-green rules |
| Sprout | `#7FB98F` | light green | 1.9:1 | Light end of a green ramp, fills only |
| Spruce | `#2E8B7F` | teal | 4.3:1 | Online status, categorical |
| Harbor | `#2B3A67` | blue | 10.4:1 | Categorical, the "other" series |
| Sky | `#3F6FB5` | light blue | 4.3:1 | Categorical |
| Plum | `#6B4E71` | violet | 6.5:1 | Categorical |

**Green ramp:** `#7FB98F` → `#17753F` → `#10552E`

Field Green is the only green in the system. It is a true green — no lime, no mint,
no neon. It reads as "good / passed / growing" next to red without competing with it,
which is why status dots use `#2E8B7F` for *online* but success messaging uses
`#17753F`. Do not tint the brand red toward green to make a "neutral" middle.

### Status

| State | Hex |
|---|---|
| Online | `#2E8B7F` |
| Pending | `#E08A1E` |
| Fault | `#BA141A` |
| Idle | `#A6A19E` |
| Success (messaging) | `#17753F` |

### Chart ramps

- **Categorical (≤6):** `#BA141A` `#2B3A67` `#E08A1E` `#2E8B7F` `#6B4E71` `#59595B`
- **Sequential:** `#FBE4E5` `#F3AEB1` `#E77B7F` `#D8474C` `#BA141A` `#8E1519`
- **Diverging:** `#2B3A67` `#6E7FA8` `#B9C2D8` `#EEEBEA` `#E79B9E` `#D8555A` `#BA141A`
- **Green (pass / growth):** `#7FB98F` `#17753F` `#10552E`

### Contrast — measured, not guessed

| Pair | Ratio | Verdict |
|---|---|---|
| Carbon on white | 15.3:1 | Body default |
| Red on white | 6.6:1 | AA at any size |
| White on red | 6.6:1 | Fine |
| Graphite on white | 7.1:1 | Fine |
| Field Green on white | 5.2:1 | AA at any size |
| Red on carbon | 2.8:1 | **Never** |

### The red budget — locked

Every graphic gets exactly one red thing: the score, the CTA, the eyebrow, *or* the
accent device. Everything else is carbon, grey, or white. On carbon and red grounds
red is replaced by white — the budget still exists, white is the loud colour.

## Type

Two faces. Chakra Petch is short, punchy, numeric. Roboto is sentences.

- `--bw-display: "Chakra Petch", system-ui, sans-serif` — weights 500 / 600 / 700
- `--bw-body: "Roboto", system-ui, sans-serif` — weights 400 / 500 / 700
- `--bw-mono: ui-monospace, "JetBrains Mono", monospace`

ARDestine is frozen inside the logo artwork only. Non-commercial licence — never
install it, never set live text in it.

| Token | Size / LH | Face | Use |
|---|---|---|---|
| Display XL | 56 / 1.0 | Chakra Petch 700 | Hero, social score |
| Display L | 40 / 1.05 | Chakra Petch 700 | Page and slide titles |
| H1 | 30 / 1.1 | Chakra Petch 700 | Section headings |
| H2 | 22 / 1.25 | Chakra Petch 600 | Subsections |
| Lead | 18 / 1.6 | Roboto 400 | Intro paragraph |
| Body | 16 / 1.6 | Roboto 400 | Everything else |
| Small | 14 / 1.5 | Roboto 400 | Captions, meta |
| Eyebrow | 12 / 1.4 | Chakra Petch 600, `.16em`, uppercase | Kickers, labels |
| Mono | 13 / 1.5 | Mono, tabular | Data, figures |

Minimums: slide body ≥ 24px at 1920×1080, print body ≥ 11pt, mobile hit targets ≥ 44px.

## Space & radius

Rounded is the shell: any box you draw has rounded corners, never sharp.

| Token | Value | Use |
|---|---|---|
| `--bw-r-pill` | 999px | Tags and chips |
| `--bw-r-btn` | 10px | Buttons and inputs |
| `--bw-r-media` | 12px | Images and media |
| `--bw-r-card` | 14px | Cards and callouts |
| `--bw-r-banner` | 18px | Banners and big panels |

One radius per shape; never mix a sharp box next to a rounded one.

Spacing scale, 4px base: `4 · 8 · 12 · 16 · 20 · 26 · 34 · 48 · 64`
(`--bw-s1` … `--bw-s9`). Hairline `1px #E5E2E1`; border `1.5px #CFCBC9`;
section rule `2px #181416`. Focus ring `0 0 0 3px rgba(186,20,26,.4)`.

## Components

Class names are `bw-block--modifier`, all prefixed `bw-`. Grounds are set on a
wrapper (`bw-on-light` / `bw-on-n50` / `bw-on-dark` / `bw-on-red`) and the
components inside reverse themselves.

| Component | Classes |
|---|---|
| Button | `bw-btn` + `--primary` / `--secondary` / `--ghost`, `--sm` / `--lg` |
| Chip | `bw-chip` + `--red` / `--solid` / `--alert` / `--level` |
| Level badge | `bw-chip bw-chip--level` + `--locked` / `--pick` / `--yours` |
| Card | `bw-card` + `--dark` / `--flat`; `bw-banner`, `bw-callout` |
| Media | `bw-media` (12px radius, clipped) |
| Form | `bw-field`, `bw-label`, `bw-input`, `bw-select`, `bw-textarea`, `bw-help`, `bw-error` |
| Table | `bw-table`, `td.bw-num`, `.bw-highlight` (one per table) |
| Status | `bw-dot` + `--online` / `--pending` / `--fault` / `--idle` |
| The Trace | `bw-trace` + `--slide` / `--doc` |
| Type | `bw-display-xl`, `bw-display-l`, `bw-h1`, `bw-h2`, `bw-lead`, `bw-body`, `bw-small`, `bw-eyebrow`, `bw-mono`, `bw-score` |
| Focal | `bw-focal` — one per screen |

The Trace markup:

```html
<svg class="bw-trace" viewBox="0 0 300 62" aria-hidden="true">
  <path d="M10,54 L52,14 L252,14"/>
  <circle cx="264" cy="14" r="9"/>
</svg>
```

Geometry is locked: 45° entry from the nearest corner, one mitered bend, flat run
≥ 1.5× the width of what it leads to, hollow circle terminal at the same stroke
weight. One per graphic, and only when there is a single focal element.

## Rules & checks

Rule levels — every section of the playbook carries these as badges:

- **Locked** — reproduce exactly. Escalate to a mentor rather than improvising.
- **Pick one** — choose any listed option; never combine two, never invent a sixth.
- **Your call** — design it. No approval needed.

Pre-publish check (all nine must clear):

1. Exactly one red thing on the graphic. (§03)
2. Body text is carbon — no red text on a dark ground. (§03)
3. Headline in Chakra Petch, sentences in Roboto. (§04)
4. Official logo file, unaltered, with full clear space. (§05)
5. One radius per element, nothing sharp mixed in. (§06)
6. One accent device at most — a Trace, landing on the focus. (§07)
7. One message: a score, or a date, or a thank-you. (§09)
8. Correct size for the graphic, text above the minimum. (§11)
9. No minor named or shown without consent. (§09)

Logo: artwork is not published in this extract on purpose.
**Check with Tao for the most up to date logos.** Never source a logo from a slide
deck, an old post, the website, or an image search. Vector for anything printed,
cut, or embroidered. Never recolour, stretch, outline, box, crop, or redraw it, and
never use it as a device.

Sizes: Instagram 1080×1350 / 1080×1080 / 1080×1920 (10% story margin);
slides 1920×1080; print 8.5×11 in at 300 dpi with a 0.25in safe margin;
banners CMYK, 0.125in bleed, logo ≥ 1in wide.
