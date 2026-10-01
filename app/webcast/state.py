"""
What a published screen currently *is*, as JSON. No widgets, no pixels.

This is the whole reason the webcast is cheap. The first build rasterised each
screen to JPEG several times a second and pushed the pixels; a 1280x720 frame
cost ~15 ms of full-chassis repaint plus encode, so two screens ran at a
quarter of a core forever and arrived soft. **The picture is almost always the
same picture** — a slide holds for 45 seconds — so the expensive thing was
being redone to say nothing had changed.

Sending state instead means the server does arithmetic and the browser does
the drawing, on the Pi's own GPU, at its own native resolution. A slide change
is one ~1 KB message. The dwell rail is not sent at all: the payload carries
the dwell's **deadline**, and the page animates it locally with
`requestAnimationFrame`, so a rail that moves 60 times a second costs the pit
machine nothing.

The shape follows Cheesy Arena: every message is `{"type": ..., "data": ...}`,
a client gets the full current state on connect and deltas afterwards, and the
screen a page wants is named in its URL.

**Everything here is read from the same singletons the Qt widgets read**, so a
monitor and a browser cannot disagree — `config` for the team, mode, theme and
per-screen face; `rotation` for the dwell; the screen class itself for the
list of slides. Nothing is duplicated except the drawing.
"""

from __future__ import annotations

import time
from typing import Any

from app import brand
from app.config import SCREEN_LABELS, config
from app.rotation import IDLE_MS, rotation

# Which window class backs each published screen. Imported lazily inside the
# functions: this module is imported by the socket server, and pulling a
# QMainWindow subclass in at import time would drag the whole widget tree into
# a process that may never build one.
_SCREEN_CLASSES = {
    "presentation_a": ("app.windows.presentation_a", "PresentationScreenA"),
    "presentation_b": ("app.windows.presentation_b", "PresentationScreenB"),
}


def _screen_class(screen_id: str):
    import importlib
    module_name, class_name = _SCREEN_CLASSES[screen_id]
    return getattr(importlib.import_module(module_name), class_name)


def now_ms() -> int:
    """Server clock, in ms. The page uses it to correct for its own offset."""
    return int(time.time() * 1000)


def palette(theme: str) -> dict[str, str]:
    """
    The plate's ink roles, as the chassis defines them.

    **Deliberately not `brand.DARK` / `brand.LIGHT`.** The plate is a lit
    surface and its inks are brighter than a control's — `chassis.py` says so
    and picks different values. Sending the control palette would give the web
    page a body ink one step too dark on every surface, which reads as "the
    web one looks a bit off" and is impossible to pin down later.
    """
    dark = theme != "light"
    return {
        "theme": "dark" if dark else "light",
        "ground": brand.CARBON_BG if dark else brand.N50,
        "plate": "#1A1719" if dark else brand.WHITE,
        "ink": brand.WHITE if dark else brand.CARBON,
        "body": brand.N300 if dark else brand.N600,
        "muted": brand.N400 if dark else brand.N500,
        "faint": brand.N500 if dark else brand.N400,
        "rule": brand.CARBON_LINE if dark else brand.N200,
        "red": brand.RED,
    }


def _slide(slide) -> dict[str, Any]:
    return {
        "kind": slide.kind,
        "eyebrow": slide.eyebrow,
        "title": slide.title,
        "body": slide.body,
        "figure": slide.figure,
        "unit": slide.unit,
        "items": list(slide.items),
        "from_log": bool(slide.is_from_log),
    }


def _rotation_face(screen_id: str) -> dict[str, Any]:
    """The slide rotation: which stop, out of how many, and the dwell clock."""
    cls = _screen_class(screen_id)
    entries = cls.rotation_entries()
    count = max(1, len(entries))
    index = int(config.get(screen_id, "slide_index", 0) or 0) % count
    entry = entries[index]

    # A board in the rotation is a Slide carrying the board's label; the page
    # draws the board face for it rather than a slide.
    board_label = cls.BOARD_LABELS.get(cls.BOARD_CONTENT)
    is_board = board_label is not None and entry is board_label

    face: dict[str, Any] = {
        "index": index,
        "count": count,
        "board": cls.BOARD_CONTENT if is_board else "",
    }
    if not is_board:
        face["slide"] = _slide(entry)
    return face


def _dwell() -> dict[str, Any]:
    """
    The dwell as a clock, not a position.

    Sending `progress` would mean sending it many times a second to keep a
    rail smooth — which is the mistake the whole rewrite exists to undo. The
    deadline is sent once per slide and the page animates against it, so the
    rail is as smooth as the browser's refresh and costs the pit machine one
    message every 45 seconds.
    """
    running = rotation.progress() > 0.0 or config.mode == "standard"
    remaining = int(IDLE_MS * (1.0 - rotation.progress()))
    return {
        "running": bool(running),
        "duration_ms": int(IDLE_MS),
        "deadline_ms": now_ms() + remaining,
    }


def _checklist_face(screen_id: str) -> dict[str, Any]:
    from app.checklist import checklist
    list_id = config.get(screen_id, "checklist_id")
    if list_id is None:
        list_id = checklist.default_list_id()
    if list_id is None:
        return {"name": "", "items": [], "done": 0, "total": 0}
    board = checklist.get_list(list_id)
    items = checklist.items(list_id)
    done, total = checklist.progress(list_id)
    return {
        "name": board.name if board else "",
        "items": [{"text": i.text, "done": bool(i.done)} for i in items],
        "done": int(done),
        "total": int(total),
    }


def _lunch_face() -> dict[str, Any]:
    """The holding card's words, read from the widget so they cannot drift."""
    from app.widgets import lunch_overlay
    return {
        "eyebrow": "Back shortly",
        "headline": lunch_overlay.HEADLINE,
        "body": lunch_overlay.SUBLINE,
    }


def _judges_face() -> dict[str, Any]:
    """
    The judges deck, as URLs the page fetches for itself.

    The artwork is the team's own finished graphic — full-bleed, never boxed —
    so the page loads the image file rather than being handed a picture of it.
    That is also the one place the old pixel path was actively worse: a
    photograph of a photograph, re-encoded every frame.
    """
    from app.judges_slides import judges_slides
    names = [p.name for p in judges_slides.paths]
    index = judges_slides.index
    return {
        "images": [f"/judges/{n}" for n in names],
        "index": index if 0 <= index < len(names) else 0,
    }


def face_for(screen_id: str) -> str:
    """Which face this screen is showing, resolved the way the window does."""
    if config.mode == "lunch":
        return "lunch"
    if config.mode == "judges":
        return "judges"
    content = str(config.get(screen_id, "content", "rotation") or "rotation")
    return content if content in (
        "rotation", "next_match", "checklist", "diagnostics", "robot_info", "analysis"
    ) else "rotation"


def screen_state(screen_id: str, on: bool = True) -> dict[str, Any]:
    """
    Everything a page needs to draw `screen_id` right now.

    **`on` is the sidebar power switch, and it is not derivable from here.**
    Power is not a `config` key — it lives with the window the control screen
    builds — so it has to be handed in. Getting this wrong is what made a
    networked screen ignore its own switch: the page kept the last state it
    was sent and carried on rotating a screen the operator had turned off.
    """
    team = config.active_team
    theme = config.screen_theme(screen_id)
    face = face_for(screen_id) if on else "off"

    state: dict[str, Any] = {
        "on": bool(on),
        "screen": screen_id,
        "label": SCREEN_LABELS.get(screen_id, screen_id),
        "mode": config.mode,
        "face": face,
        "palette": palette(theme),
        "team": {
            "number": team.number,
            "name": team.name,
            "primary": team.primary_color,
        },
        # Screen A rails left and B rails right; the ledger names which.
        "side": "left" if screen_id.endswith("_a") else "right",
        "ledger_right": ("ROTATION A" if screen_id.endswith("_a")
                         else "ROTATION B"),
        "server_now_ms": now_ms(),
        "dwell": _dwell(),
    }

    if not on:
        # Nothing else is worth building or sending: the page draws its off
        # state from this alone, and a screen that is off has no face.
        return state

    try:
        if face == "rotation":
            state["rotation"] = _rotation_face(screen_id)
        elif face == "checklist":
            state["checklist"] = _checklist_face(screen_id)
        elif face == "lunch":
            state["lunch"] = _lunch_face()
        elif face == "judges":
            state["judges"] = _judges_face()
        elif face in ("diagnostics", "robot_info"):
            state["board"] = board_state(face)
        elif face == "analysis":
            state["board"] = analysis_board_state()
        elif face == "next_match":
            state["next_match"] = next_match_state()
    except Exception as e:                    # never let one face kill a page
        state["error"] = f"{type(e).__name__}: {e}"
    return state


def board_state(which: str) -> dict[str, Any]:
    """
    The diagnostics / robot-info boards.

    Every figure comes from `diagnostics.dashboard()`, which is the only place
    allowed to compute one — the widgets do not, and neither does this. The
    board's own rule holds on the web too: **red means a latched fault and
    nothing else**, so `status` travels per row and the page colours from it
    rather than inventing a threshold in JavaScript.
    """
    from app.robot import diagnostics as diag
    board = diag.dashboard()
    if board.empty:
        return {"which": which, "empty": True}
    return {
        "which": which,
        "empty": False,
        "worst": board.worst,
        "source": board.source_name,
        "sources": list(board.sources),
        "match_key": board.match_key,
        "vitals": [{"label": r.label, "value": r.value, "unit": r.unit,
                    "status": r.status, "detail": r.detail,
                    "shape": list(r.shape)} for r in board.vitals],
        "subsystems": [{"name": s.name, "status": s.status, "note": s.note,
                        "stator_a": s.stator_a, "temp_f": s.temp_f}
                       for s in board.subsystems],
        "motors": [{"label": m.label, "can_id": m.can_id,
                    "device_type": m.device_type, "temp_c": m.temp_c,
                    "stator_a": m.stator_a, "supply_v": m.supply_v,
                    "rps": m.rps, "faults": list(m.faults),
                    "status": m.status} for m in board.motors],
        "faults": [{"label": f.label, "value": f.value, "detail": f.detail,
                    "status": f.status} for f in board.faults],
    }


def analysis_board_state() -> dict[str, Any]:
    """
    The newest analysis board, in `board_state()`'s shape plus its own
    headline and charts. Every figure is the stored board's, which passed the
    pipeline's checks (app/ai/checks.py); the page computes nothing.
    """
    from app.ai import boards
    b = boards.latest()
    if b is None:
        return {"which": "analysis", "empty": True}
    d = b.dashboard
    charts = []
    for c in b.charts[:2]:
        data = c.get("data") or {}
        charts.append({"kind": c.get("kind"), "title": c.get("title", ""),
                       "unit": c.get("unit") or "",
                       "points": [v for _t, v in data.get("points") or []],
                       "bars": [{"label": x["label"], "value": x["value"]}
                                for x in (data.get("bars") or [])[:8]]})
    return {
        "which": "analysis",
        "empty": False,
        "worst": b.headline.get("status") or d.worst,
        "source": b.spec.get("title", ""),
        "headline": {"title": b.headline.get("title", ""),
                     "sentence": b.headline.get("sentence", "")},
        "vitals": [{"label": r.label, "value": r.value, "unit": r.unit,
                    "status": r.status, "detail": r.detail,
                    "shape": list(r.shape)} for r in d.vitals],
        "subsystems": [{"name": s.name, "status": s.status, "note": s.note,
                        "stator_a": s.stator_a, "temp_f": s.temp_f}
                       for s in d.subsystems],
        "faults": [{"label": f.label, "value": f.value, "detail": f.detail,
                    "status": f.status} for f in d.faults],
        "charts": charts,
    }


def next_match_state() -> dict[str, Any]:
    """
    Our next match, as the Next Match board reads it.

    Times are sent as the API's own Unix milliseconds and the **countdown is
    the page's job** — same reasoning as the dwell rail. A server ticking a
    clock down for every viewer is the thing this rewrite deleted.
    """
    from app.nexus import nexus
    matches = nexus.our_matches()
    if not matches:
        return {"empty": True}
    match = matches[0]
    return {
        "empty": False,
        "label": getattr(match, "label", ""),
        "status": getattr(match, "status", ""),
        "alliance": getattr(match, "alliance", "") or "",
        "red": list(getattr(match, "red_teams", []) or []),
        "blue": list(getattr(match, "blue_teams", []) or []),
        "times": {
            "queue": getattr(match, "estimated_queue_time", None),
            "deck": getattr(match, "estimated_on_deck_time", None),
            "field": getattr(match, "estimated_on_field_time", None),
            "start": getattr(match, "estimated_start_time", None),
        },
        "server_now_ms": now_ms(),
    }
