# Breakaway 3937 — Brand System (agent reference)

Brand context for **FRC Team 3937 "Breakaway"** (Searcy, Arkansas). Written to be loaded by an AI coding/authoring agent so its output — web UI, charts, docs, slides, social copy — comes out on-brand without further instruction.

**How to use in Claude Code**
- Drop this file at your project root and either rename it to `CLAUDE.md`, or add `@BREAKAWAY_BRAND.md` to your existing `CLAUDE.md` to import it.
- Or paste the relevant section into a prompt when you want on-brand output.
- The **Tokens** and **Reusable snippets** sections are copy-paste ready.

**Design thesis:** *Precision under speed.* Confident, disciplined, and roomy. One red used with intent; white carries the layout. Restraint is the point — when red or an accent device appears, it should mean something.

---

## 0. Quick reference (the 90%)

- **Colors:** Red `#C82027`, Carbon (near-black) `#181416`, White `#FFFFFF`. Ember `#8E1519` for deep accents.
- **Fonts:** **Chakra Petch** for display/headlines/numbers, **Roboto** for body/UI. (`ARDestine` = logo artwork only — see §3.)
- **Devices:** **Rounded** (the container for everything) · **Bracket** (frames the one focal element) · **Pocket** (engineering-only triangle) · **Trace** (leading line to the headline).
- **Golden rules:**
  1. Red on white is the default. White on red/carbon for reversed. Never red body text on carbon.
  2. **One focal red per surface** (the "red budget" — see §5). If two things are red, neither wins.
  3. One message / one focus per surface. Generous whitespace.
  4. Never alter the logo (recolor, stretch, rotate, effects, re-typeset).
  5. One accent device per surface (Bracket, Pocket, or Trace) — never stacked. Rounded is the shell underneath.

---

## 1. Colors

| Role | Name | HEX | RGB | Notes |
|---|---|---|---|---|
| Primary | Breakaway Red | `#C82027` | 200, 32, 39 | The brand. Focus, CTAs, highlights. Print: **Pantone 186 C** / CMYK 0·84·80·22 |
| Accent | Ember | `#8E1519` | 142, 21, 25 | Deep red — pressed states, tint-chip text. Use sparingly |
| Ink | Carbon | `#181416` | 24, 20, 22 | Body text on light; dark backgrounds. Not pure black |
| Ground | White | `#FFFFFF` | 255,255,255 | Surfaces; text on red/carbon |

**Neutral scale**

| Token | HEX | Use |
|---|---|---|
| N50 | `#FAF9F8` | app background |
| N100 | `#F3F1F0` | surface / fill |
| N200 | `#E5E2E1` | hairline / border |
| N300 | `#CFCBC9` | card border / input border |
| N400 | `#A6A19E` | disabled / muted |
| N500 | `#6A6462` | secondary text |
| N600 | `#443F3D` | tertiary ink |

**Contrast (measured — obey these):**
- Carbon on White = 15.3:1 (AAA). Default for body text.
- Red on White = 5.69:1 (AA). Headlines, buttons, large text; use Carbon for long body copy.
- White on Red = 5.69:1 (AA). Fine.
- Red on Carbon = 3.12:1 — **large/graphic only, never body text.**
- N500 on White ≈ 5.1:1 (AA) — smallest acceptable for secondary text.

**Data-viz palettes.** Red marks the focus series; context series are neutral/gray. Hairline grids, no top/right spines, no 3D/shadows, tabular figures, direct labels.
- Categorical (≤6): `#C82027` `#2B3A67` `#E08A1E` `#2E8B7F` `#6B4E71` `#59595B`
- Sequential (low→high): `#FBE4E5` `#F3AEB1` `#E77B7F` `#D8474C` `#C82027` `#8E1519`
- Diverging (below←neutral→above): `#2B3A67` `#6E7FA8` `#B9C2D8` `#EEEBEA` `#E79B9E` `#D8555A` `#C82027`

---

## 2. Typography scale

Two faces, two jobs: **Chakra Petch** for anything short/punchy/numeric, **Roboto** for sentences. Never set body copy in the display face.

| Token | Size / line-height | Font · weight | Tracking | Use | Print |
|---|---|---|---|---|---|
| Display XL | 56 / 1.0 | Chakra Petch 700 | −0.01em | Hero, IG score* | 30pt+ |
| Display L | 40 / 1.05 | Chakra Petch 700 | −0.01em | Page / slide titles | 26pt |
| H1 | 30 / 1.1 | Chakra Petch 700 | 0 | Section titles | 22pt |
| H2 | 22 / 1.25 | Chakra Petch 600 | 0 | Subsections | 16pt |
| H3 | 17 / 1.3 | Chakra Petch 600 | 0 | Minor headings | 13pt |
| Lead | 18 / 1.6 | Roboto 400 | 0 | Intro paragraph | 13pt |
| Body | 16 / 1.6 | Roboto 400 | 0 | Default text | 11pt |
| Small | 14 / 1.5 | Roboto 400 | 0 | Captions, meta | 9–10pt |
| Eyebrow | 12 / 1.4 | Chakra Petch 600 | +0.16em, UPPER | Kickers, labels | 9pt |
| Mono | 13 / 1.5 | ui-monospace / JetBrains Mono | 0 (tabular) | Data, scores, timestamps | 10pt |

\*On a 1080px social artboard, scale display up: score 120–160px, headline 48–64px, kicker 28–32px.

- Web import: `https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=Roboto:wght@400;500;700&display=swap`
- Stacks: display `"Chakra Petch", system-ui, sans-serif` · body `"Roboto", system-ui, sans-serif`

---

## 3. Fonts (licensing)

| Face | Role | Weights | License | Rule |
|---|---|---|---|---|
| **ARDestine** | Logotype only | — | Non-commercial / personal | **Frozen vector inside the logo files only. Never install or set live text.** Use Chakra Petch instead |
| **Chakra Petch** | Display / headline / numeric | 500, 600, 700 | SIL OFL 1.1 (free commercial) | Per the scale in §2 |
| **Roboto** | Body / UI / captions | 400, 500, 700 | Apache 2.0 (free commercial) | Per the scale in §2 |
| Mono (optional) | Data / code | 400, 500, 700 | — | `ui-monospace` / JetBrains Mono, tabular figures |

---

## 4. Logo

Canonical files (use as-is; never regenerate): `2026_logo.png` (horizontal lockup), `2026_logo_Stack.png` (stacked), `2026_Wordmark.png`, roundel "b" badge. Vector masters live in the team drive.

- **Treatments:** red-on-white (preferred) · white-on-red · white-on-carbon. Those three only.
- **Clear space:** ≥ the height of the round "b" badge on all sides.
- **Min size:** ~120px / 1.0in wide (lockup); 24px floor for the roundel alone.
- **Avatar/favicon:** the roundel "b" badge, white on a red circle.
- **Never:** recolor, gradient, glow/shadow/outline, stretch/skew/rotate, re-typeset, box, or place on a busy photo. The logo is never a device and is never restyled by one.

---

## 5. The device system

Four graphical devices. **Rounded is the shell on every surface; Bracket, Pocket, and Trace are accents — one accent per surface max, never stacked, each in its lane.** Devices use the red/carbon/white/neutral palette only. The logo is never a device.

| Device | Role | Frequency |
|---|---|---|
| **Rounded** | Container for everything (cards, buttons, tags, banners, inputs, media) | Everywhere — the default shell |
| **Bracket** | Corner ticks framing the ONE focal element | ≤1 per surface |
| **Pocket** | Soft triangle — signals robot/machine content | Engineering only |
| **Trace** | Leading line pointing the eye to the headline / connecting two things | ≤1 per surface |

### The "red budget" (color discipline)
**One focal red per surface.** Red goes on the single most important element — the score, the CTA, the eyebrow, *or* the accent device — and everything else is carbon / neutral / white. If two things are red, neither wins.

### Which device when

| Situation | Device |
|---|---|
| Any container | **Rounded** |
| Lock the eye onto a region or photo | **Bracket** (frame it) |
| Lead the eye to a headline/score, or connect two things | **Trace** |
| Content is about the robot / machine / CAD | **Pocket** |
| Sponsor / community / hype | **Rounded only** — no engineering accents |

### 5.1 Rounded — the container
The default shell. Filled (red or carbon, no border) or white with a 1.5px N300 hairline.

| Element | Radius |
|---|---|
| Tags / chips / pills | full pill (radius = ½ height) |
| Buttons / inputs | 10px |
| Cards / callouts | 14px |
| Banners / hero panels | 16–20px |
| Images / media | 12px |

**Never** mix two radii on one element, or mix a sharp corner with a rounded one.

### 5.2 Bracket — focus accent
Four **open** L-ticks at the corners of one element; they never close into a box.
- Tick length ≈ 12–16% of the framed area's short side (min 12px, max ~28px); stroke 3–4px, scaling with size.
- Red on light, white on dark; sits on or just inside the corners.
- **Use for the single focal thing:** hero stat, key photo/CAD render, featured callout, section header.
- **Never:** bracket everything, bracket tiny inline tags (it frames a *region*, not every chip), or combine with a heavy border. One bracketed element per surface.
- **Bracket vs Trace:** Trace *leads to* a headline with a line; Bracket *frames* a region with ticks. One per element, never both on the same thing.

### 5.3 Pocket — engineering scope
Soft filleted triangle (~30% edge fillet, never sharp), point-up default; alternating up/down = truss pattern.
- **Use ONLY for:** robot reveals, CAD/design breakdowns, technical docs, subsystem callouts, build-season/shop content.
- **Never for:** general/community posts, sponsor thanks, match hype.
- Applications: solid focus marker, outline container, nested subsystem callout, low-opacity truss background, photo mask (thin red outline) for robot/CAD imagery.

### 5.4 Trace — leading line
A line enters on the diagonal, bends once to flat, and lands in a rectangular pad — pointing the eye at the focal element. **It is a leading line: the diagonal entry is what makes the eye travel.**
- **Geometry:** 45° entry from the nearest corner/edge → exactly one mitered bend to horizontal → rectangular pad terminal (or runs off-frame). No second bend, no 90° bracket, never round.
- **Stroke:** hero/social 6–8px · slide 5–6px · doc heading accent 2–3px.
- **Pad:** rectangle, width ≈ 2.5× stroke, height ≈ 1.5× stroke, radius 2px.
- **Flat run:** ≥ 1.5× the width of the element it leads to; the pad sits just past the headline.
- **Placement:** flat leg sits ~0.5× cap-height below the headline baseline; enters from the bottom corner.
- **Color:** red on light, white on dark; neutral gray for faint background fields.
- **Uses:** lead the eye to a title/score (Word title, PowerPoint title slide, Instagram score), connect two components, section divider, chart annotation (arrives at a pad).
- **Never:** pure 90° brackets/frames, multiple bends/zigzags, round terminals, use as the logo, or float it with nothing to lead to.
- **Teen recipe (same everywhere):** (1) line from a corner at 45°, (2) bend once to flat, (3) end in a small rectangle under the headline.

---

## 6. Voice & content

- **Tone:** proud not bragging; positive always; specific beats vague; thank sponsors/volunteers/mentors.
- **One message per post** — a score OR a date OR a thank-you. Score leads on match-result posts.
- **Handle:** `@breakaway3937`. **Hashtags (always):** `#Breakaway #FRC3937 #omgrobots #FIRSTrobotics` + event tags.
- **Safety (hard rule for generated copy):** never post/name minors without consent; never publish full names of minors, home addresses, or exact schedules. Default to "ask a mentor" when unsure.

---

## 7. UI conventions

- **Buttons (Rounded, 10px):** primary = fill Red, text White, hover `#B01C22`, pressed Ember `#8E1519`. Secondary = white fill, Red text + 1.5px Red border. Ghost = transparent, Carbon text.
- **Focus ring:** `0 0 0 3px rgba(200,32,39,.4)`, offset 2px.
- **Tags/chips (full pill):** red-tint `bg rgba(200,32,39,.1) / text #8E1519` · neutral `#E5E2E1 / #443F3D` · solid `#181416 / #fff`.
- **Status dots:** online `#2E8B7F` · pending `#E08A1E` · fault `#C82027` · idle `#A6A19E`.
- **Data tables:** numeric columns in mono, tabular figures, right-aligned; red **bolds the highlight cell only**.
- **Surfaces:** white or N50 backgrounds, N200 hairlines. Radii per §5.1. Bracket the one focal card/stat if needed.

---

## 8. Social & print

**Instagram sizes:** 1080×1080 (square) · 1080×1350 (portrait, best feed) · 1080×1920 (story; 10% edge margin clear). **Templates:** match win (red, score leads) · event (carbon) · sponsor thanks (white, red accents) · custom. Use Trace to lead the eye to the score/headline; Bracket to frame a hero photo.

**Print:** CMYK; red = **Pantone 186 C** spot if budget. Bleed 0.125", safe margin 0.25". Vector or 300dpi floor. Logo min ~1" wide. Robot bumper: `3937` Chakra Petch, white on carbon, ≥6" numerals per FRC.

---

## 9. Tokens (copy-paste)

### CSS custom properties
```css
:root {
  --bw-red:#C82027; --bw-red-hover:#B01C22; --bw-ember:#8E1519;
  --bw-carbon:#181416; --bw-white:#FFFFFF;
  --bw-n50:#FAF9F8; --bw-n100:#F3F1F0; --bw-n200:#E5E2E1;
  --bw-n300:#CFCBC9; --bw-n400:#A6A19E; --bw-n500:#6A6462; --bw-n600:#443F3D;
  --bw-d1:#C82027; --bw-d2:#2B3A67; --bw-d3:#E08A1E;
  --bw-d4:#2E8B7F; --bw-d5:#6B4E71; --bw-d6:#59595B;
  --bw-display:"Chakra Petch", system-ui, sans-serif;
  --bw-body:"Roboto", system-ui, sans-serif;
  --bw-mono:ui-monospace, "JetBrains Mono", monospace;
  /* Rounded radius scale */
  --bw-r-pill:999px; --bw-r-btn:10px; --bw-r-card:14px; --bw-r-banner:18px; --bw-r-media:12px;
}
```

### Tailwind (extend `theme`)
```js
theme: {
  extend: {
    colors: {
      breakaway: {
        red:'#C82027', 'red-hover':'#B01C22', ember:'#8E1519', carbon:'#181416',
        n50:'#FAF9F8', n100:'#F3F1F0', n200:'#E5E2E1', n300:'#CFCBC9',
        n400:'#A6A19E', n500:'#6A6462', n600:'#443F3D',
      },
    },
    fontFamily: {
      display: ['"Chakra Petch"','system-ui','sans-serif'],
      body:    ['Roboto','system-ui','sans-serif'],
    },
    borderRadius: { pill:'999px', btn:'10px', card:'14px', banner:'18px', media:'12px' },
  },
}
```

### Power BI theme
```json
{
  "name": "Breakaway 3937",
  "dataColors": ["#C82027","#2B3A67","#E08A1E","#2E8B7F","#6B4E71","#59595B"],
  "foreground": "#181416", "background": "#FFFFFF", "tableAccent": "#C82027",
  "good": "#2E8B7F", "neutral": "#A6A19E", "bad": "#C82027",
  "maximum": "#8E1519", "center": "#E5E2E1", "minimum": "#FBE4E5"
}
```

### matplotlib
```python
import matplotlib as mpl
CATEGORICAL = ["#C82027","#2B3A67","#E08A1E","#2E8B7F","#6B4E71","#59595B"]
SEQUENTIAL  = ["#FBE4E5","#F3AEB1","#E77B7F","#D8474C","#C82027","#8E1519"]
DIVERGING   = ["#2B3A67","#6E7FA8","#B9C2D8","#EEEBEA","#E79B9E","#D8555A","#C82027"]
mpl.rcParams.update({
    "font.family":"Roboto", "axes.prop_cycle":mpl.cycler(color=CATEGORICAL),
    "axes.edgecolor":"#E5E2E1", "axes.spines.top":False, "axes.spines.right":False,
    "axes.grid":True, "grid.color":"#E5E2E1", "grid.linewidth":0.8,
    "text.color":"#181416", "axes.labelcolor":"#6A6462",
    "xtick.color":"#6A6462", "ytick.color":"#6A6462",
    "figure.facecolor":"white", "axes.facecolor":"white",
})  # red = focus series; Ember (#8E1519) = the single highlight
```

---

## 10. Reusable snippets (devices)

### Rounded — CSS
```css
.bw-card   { border-radius:14px; background:#fff; border:1.5px solid #CFCBC9; }
.bw-btn    { border-radius:10px; background:#C82027; color:#fff; }
.bw-tag    { border-radius:999px; }        /* full pill */
.bw-banner { border-radius:18px; background:#181416; color:#fff; }
```

### Bracket — SVG (four open corner ticks around x,y,w,h)
```js
// tick = 12-16% of short side, clamped 12-28px; stroke 3-4px; red on light / white on dark.
function bracket(x, y, w, h, color = "#C82027", stroke = 3.5) {
  const t = Math.max(12, Math.min(28, 0.14 * Math.min(w, h)));
  const L = (ax,ay,bx,by,cx,cy) => `M${ax},${ay} L${bx},${by} L${cx},${cy}`;
  const d = [
    L(x, y+t, x, y, x+t, y),                 // top-left
    L(x+w-t, y, x+w, y, x+w, y+t),           // top-right
    L(x, y+h-t, x, y+h, x+t, y+h),           // bottom-left
    L(x+w-t, y+h, x+w, y+h, x+w, y+h-t),     // bottom-right
  ].join(" ");
  return `<path d="${d}" fill="none" stroke="${color}" stroke-width="${stroke}"/>`;
}
```

### Pocket — SVG path generator (rounded equilateral triangle)
```js
// rot=0 => point up. soft = fillet fraction of edge (~0.30 on-brand). Fill #C82027 or stroke #181416.
function pocketTrianglePath(cx, cy, R, rot = 0, soft = 0.30) {
  const P = [0,1,2].map(i => { const a=(rot+120*i-90)*Math.PI/180; return [cx+R*Math.cos(a), cy+R*Math.sin(a)]; });
  const unit = (a,b) => { const dx=b[0]-a[0], dy=b[1]-a[1], L=Math.hypot(dx,dy); return [dx/L,dy/L,L]; };
  const A=[], B=[];
  for (let i=0;i<3;i++){
    const V=P[i], Pr=P[(i+2)%3], Nx=P[(i+1)%3];
    const [upx,upy,lp]=unit(V,Pr), [unx,uny,ln]=unit(V,Nx);
    const t=soft*Math.min(lp,ln);
    A.push([V[0]+upx*t, V[1]+upy*t]); B.push([V[0]+unx*t, V[1]+uny*t]);
  }
  const f = p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`;
  let d = `M${f(B[0])} `;
  for (let i=1;i<3;i++) d += `L${f(A[i])} Q${f(P[i])} ${f(B[i])} `;
  d += `L${f(A[0])} Q${f(P[0])} ${f(B[0])} Z`;
  return d;
}
```

### Trace — leading line geometry
```js
// 45 entry from a bottom corner -> one mitered bend to flat -> rectangular pad under the headline.
// dir 'left'  => enters bottom-left, pad on the right (default).
function trace({ x, y, flatLen = 220, drop = 54, stroke = 6, color = "#C82027", dir = "left" }) {
  const s = dir === "left" ? 1 : -1;
  const bendX = x, bendY = y;                    // where the diagonal meets the flat
  const startX = x - s*drop, startY = y + drop;  // 45 entry point (below the bend)
  const padX = x + s*flatLen;
  const pts = `${startX},${startY} ${bendX},${bendY} ${padX},${bendY}`;
  const pw = 2.5*stroke, ph = 1.5*stroke;
  const px = dir === "left" ? padX : padX - pw;
  return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="${stroke}"
      stroke-linecap="round" stroke-linejoin="miter"/>
    <rect x="${px}" y="${bendY - ph/2}" width="${pw}" height="${ph}" rx="2" fill="${color}"/>`;
}
```

---

## 11. Directives by task (for the agent)

- **Web / React / HTML UI:** apply CSS vars or Tailwind tokens; Rounded radius scale on every container; Chakra Petch headings, Roboto body; one focal red per view (red budget); Bracket the single hero stat/photo if it needs emphasis. Body text = Carbon, never red.
- **Charts (Python/matplotlib):** apply the rcParams; red = the one focus series, neutrals for context; hairline grids, no chartjunk, tabular figures, direct labels.
- **Power BI:** import the theme JSON.
- **Docs / slides:** white background, Carbon body, Chakra Petch headings per the type scale, red accents/eyebrows; one idea per slide; Trace leads the eye into the title; logo small in a corner with clear space.
- **Social copy:** §6 voice + hashtags + safety; one message per post; Trace to the score, Bracket to frame a hero photo.
- **Logo/graphics:** never regenerate or alter the logo. One accent device per surface; Pocket = engineering only.

---

*Breakaway 3937 · Brand System v2.0 · Searcy, Arkansas. Pairs with the published `Breakaway_Brand_System.html`.*