# Breakaway 3937 — Brand System (agent reference)

Brand context for **FRC Team 3937 "Breakaway"** (Searcy, Arkansas). This file is written to be loaded by an AI coding/authoring agent so its output — web UI, charts, docs, slides, social copy — comes out on-brand without further instruction.

**How to use in Claude Code**
- Drop this file at your project root and either rename it to `CLAUDE.md`, or add `@BREAKAWAY_BRAND.md` to your existing `CLAUDE.md` to import it.
- Or paste the relevant section into a prompt when you want on-brand output.
- The **Tokens** and **Reusable snippets** sections are copy-paste ready.

**Design thesis:** *Precision under speed.* Aggressive, chamfered display type carrying competition energy; a disciplined, roomy system around it. One red used with intent; white carries the layout. Restraint is the point — when red or a device appears, it should mean something.

---

## 0. Quick reference (the 90%)

- **Colors:** Red `#C82027`, Carbon (near-black) `#181416`, White `#FFFFFF`. Ember `#8E1519` for deep accents.
- **Fonts:** **Chakra Petch** for display/headlines/numbers, **Roboto** for body/UI. (`ARDestine` = logo artwork only — see §3.)
- **Golden rules:**
  1. Red on white is the default, best-looking combo. White on red/carbon for reversed.
  2. **Never** put red text on carbon/black for body copy (contrast too low).
  3. One message / one focus per surface. Leave generous whitespace.
  4. Never alter the logo (recolor, stretch, rotate, effects, re-typeset).
  5. One brand *device* per surface (see §6). A device never replaces the logo.

---

## 1. Colors

| Role | Name | HEX | RGB | Notes |
|---|---|---|---|---|
| Primary | Breakaway Red | `#C82027` | 200, 32, 39 | The brand. Focus, CTAs, highlights. Print: **Pantone 186 C** / CMYK 0·84·80·22 |
| Accent | Ember | `#8E1519` | 142, 21, 25 | Deep red — pressed states, small accents. Use sparingly |
| Ink | Carbon | `#181416` | 24, 20, 22 | Body text on light; dark backgrounds. Not pure black |
| Ground | White | `#FFFFFF` | 255,255,255 | Surfaces; text on red/carbon |

**Neutral scale**

| Token | HEX | Use |
|---|---|---|
| N50 | `#FAF9F8` | app background |
| N100 | `#F3F1F0` | surface / fill |
| N200 | `#E5E2E1` | hairline / border |
| N300 | `#CFCBC9` | input border |
| N400 | `#A6A19E` | disabled / muted |
| N500 | `#6A6462` | secondary text |
| N600 | `#443F3D` | tertiary ink |

**Contrast (measured — obey these):**
- Carbon on White = 15.3:1 (AAA). Default for body text.
- Red on White = 5.69:1 (AA, **not** AAA). Fine for headlines, buttons, large text; use Carbon for long body copy.
- White on Red = 5.69:1 (AA). Fine.
- Red on Carbon = 3.12:1 — **large/graphic only, never body text.**
- N500 on White ≈ 5.1:1 (AA) — smallest acceptable for secondary text.

---

## 2. Data-visualization palettes

Rule: **red marks the focus series; all context series are neutral/gray.** Never rainbow a one-story chart. Hairline grids (N200), no top/right spines, no 3D/shadows/gradients, tabular figures, direct labels over legends.

- **Categorical (≤6 series):** `#C82027` `#2B3A67` `#E08A1E` `#2E8B7F` `#6B4E71` `#59595B`
- **Sequential (low→high):** `#FBE4E5` `#F3AEB1` `#E77B7F` `#D8474C` `#C82027` `#8E1519`
- **Diverging (below←neutral→above):** `#2B3A67` `#6E7FA8` `#B9C2D8` `#EEEBEA` `#E79B9E` `#D8555A` `#C82027` — built for variance (favorable = red, unfavorable = slate).

---

## 3. Typography

| Face | Role | Weights | License | Rule |
|---|---|---|---|---|
| **ARDestine** | Logotype only | — | Non-commercial / personal | **Frozen vector inside the logo files only. Never install or set live text.** Use Chakra Petch instead |
| **Chakra Petch** | Display / headline / numeric | 500, 600, 700 | SIL OFL 1.1 (free commercial) | Headlines, section titles, big numbers, scores, team #, eyebrows |
| **Roboto** | Body / UI / captions | 400, 500, 700 | Apache 2.0 (free commercial) | All body copy, labels, tables, UI text |
| Mono (optional) | Data / telemetry / code | 400, 500, 700 | — | `ui-monospace` / JetBrains Mono for tabular data, timestamps, code. Use tabular figures |

- Web import: `https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=Roboto:wght@400;500;700&display=swap`
- CSS stacks: display `"Chakra Petch", system-ui, sans-serif` · body `"Roboto", system-ui, sans-serif`
- **Rule of thumb:** full sentence → Roboto. Short punchy phrase or number → Chakra Petch. Never set body copy in the display face. Eyebrows = Chakra Petch 600, uppercase, ~0.2em tracking.

**Type scale (target sizes):** display-xl 44–64 / display-l 32–44 / h1 24–32 / h2 18–24 / h3 18 / body 15–16 / caption 13 / eyebrow 11–12 (tracked caps).

---

## 4. Logo

Canonical asset files (use as-is; never regenerate): `2026_logo.png` (primary horizontal lockup: roundel + 3937), `2026_logo_Stack.png` (stacked), `2026_Wordmark.png` (BREAKAWAY), roundel "b" badge (crop of the stack). Vector masters live in the team drive.

- **Color treatments:** red-on-white (preferred) · white-on-red · white-on-carbon. Those three only.
- **Clear space:** keep clear on all sides ≥ the height of the round "b" badge.
- **Min size:** ~120px (digital) / 1.0in (print) wide for the primary lockup; 24px floor for the roundel alone.
- **Avatar/favicon:** use the roundel "b" badge (white on red circle).
- **Never:** recolor, gradient, add glow/shadow/outline, stretch/skew/rotate/condense, re-typeset the wordmark, box it, or place on a busy photo. The logo is never chamfered or used as a device.

---

## 5. Voice & content

- **Tone:** proud not bragging; positive always (a tough match is "we learned a ton"); specific beats vague; thank sponsors/volunteers/mentors.
- **One message per post** — a score OR a date OR a thank-you, not all three. Score leads on match-result posts.
- **Handle:** `@breakaway3937`. **Hashtags (always):** `#Breakaway #FRC3937 #omgrobots #FIRSTrobotics` + event tags when available.
- **Safety (hard rule for generated copy):** never post/name minors without consent; never publish full names of minors, home addresses, or exact schedules. Default to "ask a mentor" when unsure.

---

## 6. Brand devices

Three shape devices extend the system. **Governance:** one device per surface (they don't stack), the logo is never a device, devices inherit only the brand palette, and The Pocket is scope-locked to engineering content.

### 6.1 The Cut (chamfer) — core brand container
- 45° hard cut on **two diagonally-opposite corners** (default top-right + bottom-left) → one consistent diagonal shear (implies motion), not a symmetric bevel.
- Radius 0 (hard point); a 2px radius is allowed only on large print / laser edges.
- **Size tiers:** small 7–9px (tags/buttons) · medium 12–16px (cards/callouts) · large 24–40px (heroes/banners, ≈2–3% of short side).
- Use on: containers, cards, buttons, tags, banners, image frames.
- Never: cut all four corners, use a non-45° angle, mix radius + cut on one element, or apply to the logo.
- CSS: see Reusable snippets.

### 6.2 The Pocket (soft triangle) — ENGINEERING SCOPE ONLY
- Origin: the robot's filleted lightening pockets. **Signals "this content is about the machine."**
- Geometry: equilateral triangle, **rounded vertices with ~30% fillet ratio (never sharp)**, point-up default; alternating up/down = truss pattern.
- Color: red solid for a focus shape; neutral outline for patterns/masks.
- **Use ONLY for:** robot reveals, CAD/design breakdowns, technical docs, subsystem callouts, build-season/shop content.
- **Never for:** general team/community posts, sponsor thank-yous, match hype, or anywhere The Cut should speak.
- Applications: marker, outline container, nested subsystem callout, low-opacity truss background, photo mask (thin red outline) for robot/CAD imagery.

### 6.3 The Break Line (leader → roundel) — motion signature
- A line enters at a justified angle, **breaks** to horizontal, and terminates in the split roundel (the mark = the destination). "A breakaway made visible."
- **Non-negotiable lock:** the horizontal (level) leg is **colinear with the roundel's split** — the line runs into and through the mark.
- **Angle:** 45° default (locks to The Cut's diagonal). 30–60° when matching a real vector in the frame (arm angle, game-piece trajectory, chart trend, photo edge). **Never arbitrary — always justified by something in the composition.** Ascending = momentum (default); descending = arrival (deliberate); mirror to enter from the right.
- **Proportions:** stroke ≈ 0.11 × roundel diameter (matches the ring weight); level segment ≥ 1.5 × diameter; clear space ≥ 0.5 × diameter; **one break only** (no zigzags).
- **Motion (video/reveal):** draw the approach→level (~1.1s, ease-out `cubic-bezier(.5,0,.2,1)`), then the roundel pops in (~0.4s) at ~95% of the draw. Respect `prefers-reduced-motion` (show resolved state, no animation).
- Use as: hero flourish, section divider, video lower-third, reveal. **Once per surface.** It's a signature, not a logo replacement.

---

## 7. UI conventions

- **Buttons:** primary = fill Red `#C82027`, text White, radius 8px, hover `#B01C22`, pressed Ember `#8E1519`. Secondary = white fill, Red text + 1.5px Red border, inverts on hover. Ghost = transparent, Carbon text.
- **Focus ring:** `0 0 0 3px rgba(200,32,39,.4)`, offset 2px — always visible for keyboard nav.
- **Tags/chips:** red-tint `background:rgba(200,32,39,.1); color:#8E1519` · neutral `#E5E2E1 / #443F3D` · solid `#181416 / #fff`.
- **Status dots:** online `#2E8B7F` · pending `#E08A1E` · fault `#C82027` · idle `#A6A19E`.
- **Data tables:** numeric columns in mono, tabular figures, right-aligned; red **bolds the highlight cell only**, never the whole column.
- **Surfaces/radii:** white or N50 backgrounds, N200 hairlines, 8px on controls, 10–14px on cards. Use The Cut (chamfer) for signature/stat cards, sparingly.

---

## 8. Social & print specs

**Instagram post sizes:** 1080×1080 (square) · 1080×1350 (portrait, best feed performance) · 1080×1920 (story; keep a 10% edge margin clear for platform UI).
**Post templates:** Match win (red bg, white text, score leads) · Event (carbon bg) · Sponsor thanks (white bg, red accents, restrained) · Custom (choose bg).

**Print:** build in **CMYK**; red = **Pantone 186 C** spot if budget. Bleed 0.125", safe margin 0.25". Logos as vector (`.ai`/`.pdf`/`.svg`) or 300dpi raster floor. Logo min ~1" wide (24px badge). Robot bumper: `3937` in Chakra Petch, white on carbon, ≥6" numerals per FRC rules.

---

## 9. Tokens (copy-paste)

### CSS custom properties
```css
:root {
  --bw-red:#C82027; --bw-red-hover:#B01C22; --bw-ember:#8E1519;
  --bw-carbon:#181416; --bw-white:#FFFFFF;
  --bw-n50:#FAF9F8; --bw-n100:#F3F1F0; --bw-n200:#E5E2E1;
  --bw-n300:#CFCBC9; --bw-n400:#A6A19E; --bw-n500:#6A6462; --bw-n600:#443F3D;
  /* data-viz categorical */
  --bw-d1:#C82027; --bw-d2:#2B3A67; --bw-d3:#E08A1E;
  --bw-d4:#2E8B7F; --bw-d5:#6B4E71; --bw-d6:#59595B;
  --bw-display:"Chakra Petch", system-ui, sans-serif;
  --bw-body:"Roboto", system-ui, sans-serif;
  --bw-mono:ui-monospace, "JetBrains Mono", monospace;
  /* signature chamfer (medium) */
  --bw-cut:polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px));
}
```

### Tailwind (extend `theme`)
```js
theme: {
  extend: {
    colors: {
      breakaway: {
        red:'#C82027', 'red-hover':'#B01C22', ember:'#8E1519',
        carbon:'#181416',
        n50:'#FAF9F8', n100:'#F3F1F0', n200:'#E5E2E1', n300:'#CFCBC9',
        n400:'#A6A19E', n500:'#6A6462', n600:'#443F3D',
      },
    },
    fontFamily: {
      display: ['"Chakra Petch"','system-ui','sans-serif'],
      body:    ['Roboto','system-ui','sans-serif'],
    },
  },
}
```

### Power BI theme (`Import theme → Browse`)
```json
{
  "name": "Breakaway 3937",
  "dataColors": ["#C82027","#2B3A67","#E08A1E","#2E8B7F","#6B4E71","#59595B"],
  "foreground": "#181416", "background": "#FFFFFF",
  "tableAccent": "#C82027",
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
    "font.family":"Roboto",
    "axes.prop_cycle":mpl.cycler(color=CATEGORICAL),
    "axes.edgecolor":"#E5E2E1", "axes.spines.top":False, "axes.spines.right":False,
    "axes.grid":True, "grid.color":"#E5E2E1", "grid.linewidth":0.8,
    "text.color":"#181416", "axes.labelcolor":"#6A6462",
    "xtick.color":"#6A6462", "ytick.color":"#6A6462",
    "figure.facecolor":"white", "axes.facecolor":"white",
})
# convention: red = focus series; graphite/neutral = context; Ember (#8E1519) = highlight bar
```

---

## 10. Reusable snippets (devices)

### The Cut (chamfer) — CSS
```css
/* size tiers: swap the 14px for 8px (small) or 28px (large) */
.bw-cut      { clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px)); }
.bw-cut-btn  { clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 9px 100%, 0 calc(100% - 9px)); }
```

### The Pocket (soft triangle) — SVG path generator
```js
// Rounded equilateral triangle. rot=0 => point up. soft = fillet fraction of edge (~0.30 = on-brand).
function pocketTrianglePath(cx, cy, R, rot = 0, soft = 0.30) {
  const P = [0,1,2].map(i => {
    const a = (rot + 120*i - 90) * Math.PI/180;
    return [cx + R*Math.cos(a), cy + R*Math.sin(a)];
  });
  const unit = (a,b) => { const dx=b[0]-a[0], dy=b[1]-a[1], L=Math.hypot(dx,dy); return [dx/L, dy/L, L]; };
  const A=[], B=[];
  for (let i=0;i<3;i++){
    const V=P[i], Pr=P[(i+2)%3], Nx=P[(i+1)%3];
    const [upx,upy,lp]=unit(V,Pr), [unx,uny,ln]=unit(V,Nx);
    const t = soft*Math.min(lp,ln);
    A.push([V[0]+upx*t, V[1]+upy*t]);
    B.push([V[0]+unx*t, V[1]+uny*t]);
  }
  const f = p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`;
  let d = `M${f(B[0])} `;
  for (let i=1;i<3;i++) d += `L${f(A[i])} Q${f(P[i])} ${f(B[i])} `;
  d += `L${f(A[0])} Q${f(P[0])} ${f(B[0])} Z`;
  return d; // fill #C82027 (focus) or stroke #181416 (outline)
}
```

### The Break Line — geometry
```js
// Emits polyline points + roundel center. Render the polyline with stroke ~0.11*D,
// then place the split roundel centered at [circleCx, y0] so its split == y0 (the level line).
function breakLine({ W, H, D=76, theta=45, dir='asc', mirror=false }) {
  const y0 = H*0.54;
  const circleCx = mirror ? 92 : W-92;
  const breakX   = mirror ? W*0.60 : W*0.40;        // elbow x
  const levelEnd = mirror ? circleCx + D/2 : circleCx - D/2;
  const Lapp = Math.min(96/Math.max(Math.sin(theta*Math.PI/180),1e-3), 210);
  const run  = Lapp*Math.cos(theta*Math.PI/180);
  const rise = Lapp*Math.sin(theta*Math.PI/180);
  const entryX = breakX + (mirror ? run : -run);
  const entryY = dir==='asc' ? y0+rise : y0-rise;
  return {
    points: [[entryX,entryY],[breakX,y0],[levelEnd,y0]], // approach -> break -> level
    roundel: { cx: circleCx, cy: y0, d: D },             // split sits at y0, colinear with level leg
    strokeWidth: +(0.11*D).toFixed(1),
  };
}
```

---

## 11. Directives by task (for the agent)

- **Web / React / HTML UI:** apply the CSS vars or Tailwind tokens; Chakra Petch for headings, Roboto for body; red for primary actions only; The Cut on signature cards, sparingly. Obey the contrast rules (body text = Carbon, not red).
- **Charts (Python/matplotlib):** apply the rcParams above; red = the one focus series, neutrals for context; hairline grids, no chartjunk, tabular figures, direct labels.
- **Power BI:** import the theme JSON.
- **Docs / slides:** white background, Carbon body, Chakra Petch headings, red accents/eyebrows; one idea per slide; logo small in a corner with clear space.
- **Social copy:** follow §5 voice + hashtags + safety rules; one message per post.
- **Logo/graphics:** never regenerate or alter the logo — reference the official files. Use devices per §6 governance (one per surface; Pocket = engineering only).

---

*Breakaway 3937 · Brand System v1.0 · Searcy, Arkansas. Pairs with the published `Breakaway_Brand_System.html`.*