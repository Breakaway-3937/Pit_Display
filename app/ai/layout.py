"""
A board laid out by code, no model: the findings as cards, worst first.

The designer model words a board (a headline, card labels, which charts);
nothing it does decides what the crew learns, since every figure, colour and
card already comes from the findings. So when it's not worth waiting for, or
it fails its checks twice, code lays the board out instead:

* **early**, right after the CSA step: its diagnoses go on screen a minute or
  so into a run, not five (the analyst and the designer take the rest), and
  the full board replaces it under the same uid;
* **as the fallback** when the designer's board is rejected twice: sound
  findings used to be thrown away with it.

Every string is cut from a finding's own claim (whole words only, so no figure
is cut in half), so `checks.check_board` holds by construction; the pipeline
still runs it. Charts are the first of `checks.chart_options()`, one per
finding, at most two. No Qt.
"""

from __future__ import annotations

import re

from app.ai import checks

DESIGNER = "code"


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, at a word boundary, no trailing punctuation."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text.rstrip(" .;:,")
    head = text[:limit + 1].rsplit(" ", 1)[0] if " " in text[:limit + 1] else text[:limit]
    return head[:limit].rstrip(" .;:,(—-")


def _topic(claim: str, limit: int) -> str:
    """What a finding is about: its claim up to the first ':' '(' ',' or ' — '."""
    head = re.split(r"\s\(|:\s|,\s|\s—\s|\s-\s", " ".join(str(claim or "").split()), maxsplit=1)[0]
    return _cut(head or claim, limit)


def _value(v) -> str | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if float(v).is_integer() and abs(v) < 1e11:
        return str(int(v))
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return text if len(text) <= 12 else None


def draft(insights: dict, *, name: str = "", match_key: str = "",
          options: list[dict] | None = None) -> dict:
    """A designer draft (`schema.designer_schema()`) from the findings alone."""
    order = checks.SEVERITY_ORDER
    findings = sorted(insights.get("findings") or [],
                      key=lambda f: order.get(f.get("severity"), 9))
    problems = [f for f in findings if f.get("severity") in ("fault", "warn")]
    if problems:
        worst = problems[0]
        headline = {"title": _topic(worst.get("claim"), 40),
                    "sentence": _cut(worst.get("claim"), 200)}
    else:
        headline = {"title": "All clear",
                    "sentence": _cut(findings[0].get("claim"), 200) if findings
                    else "Nothing in this log needs the crew"}
    vitals, faults = [], []
    for f in findings:
        metric = f.get("metric") or {}
        card = {"label": _topic(f.get("claim"), 28), "finding": f.get("id"),
                "value": _value(metric.get("value")) or "—",
                "detail": _cut(f.get("claim"), 80)}
        if metric.get("unit"):
            card["unit"] = str(metric["unit"])[:8]
        if isinstance(f.get("series"), dict):
            card["series"] = dict(f["series"])
        region = faults if f.get("severity") == "fault" else vitals
        region.append(card)
    vitals, faults = vitals[:8], faults[:6]
    if not vitals and faults:
        vitals = [faults.pop(0)]            # the contract needs a vitals list with a card
    charts, drawn = [], set()
    for opt in options or []:
        fid = opt.get("finding")
        if fid in drawn or opt.get("kind") not in ("line", "bars", "sessions"):
            continue
        f = next((x for x in findings if x.get("id") == fid), None)
        if f is None or f.get("severity") not in ("fault", "warn"):
            continue
        drawn.add(fid)
        metric = f.get("metric") or {}
        chart = {"kind": opt["kind"], "title": _cut(metric.get("name") or _topic(
                     f.get("claim"), 48), 48),
                 "finding": fid, "source": dict(opt["source"]),
                 "why": "laid out by code: the first chart its finding offers"}
        if metric.get("unit"):
            chart["unit"] = str(metric["unit"])[:8]
        charts.append(chart)
        if len(charts) == 2:
            break
    title = " · ".join(x for x in (match_key or name, headline["title"].lower()) if x)
    out = {"title": _cut(title, 60), "headline": headline, "vitals": vitals,
           "footer": {"left": "Laid out by code from the checked findings"}}
    if faults:
        out["faults"] = faults
    if charts:
        out["charts"] = charts
    return out
