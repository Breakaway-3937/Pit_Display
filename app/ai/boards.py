"""
Published analysis boards, as the screens read them.

A board (`home/contracts/board.schema.json`) mirrors `diagnostics.Dashboard`
field for field, on purpose: `to_dashboard()` turns one into the other, and the
Analysis face paints it with the diagnostics board's own painter. Nothing here
computes a figure; the board's were checked before it was stored.

The newest board wins, whoever made it (home or any pit): the crew wants the
latest read of the robot, and every board names its models and session.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.db import db
from app.robot import diagnostics as dg


@dataclass
class Board:
    uid: str
    spec: dict
    created_at: str
    dashboard: dg.Dashboard = field(default_factory=dg.Dashboard)

    @property
    def headline(self) -> dict:
        return self.spec.get("headline") or {}

    @property
    def charts(self) -> list[dict]:
        return [c for c in self.spec.get("charts") or [] if c.get("data")]


def _reading(r: dict) -> dg.Reading:
    return dg.Reading(label=str(r.get("label", "")), value=str(r.get("value", "")),
                      unit=str(r.get("unit") or ""), status=r.get("status") or dg.IDLE,
                      detail=str(r.get("detail") or ""),
                      shape=tuple(float(x) for x in r.get("shape") or [] if x is not None))


def to_dashboard(spec: dict) -> dg.Dashboard:
    """The board as the diagnostics painter's own model."""
    return dg.Dashboard(
        # Not None: the painter reads None as "no log", and this board has one.
        session_id=0,
        source_name=str(spec.get("title", "")),
        vitals=[_reading(r) for r in spec.get("vitals") or []],
        subsystems=[dg.Subsystem(name=str(s.get("name", "")), stator_a=s.get("stator_a"),
                                 supply_a=s.get("supply_a"), pdh_a=s.get("pdh_a"),
                                 temp_f=s.get("temp_f"), status=s.get("status") or dg.IDLE,
                                 note=str(s.get("note") or ""))
                    for s in spec.get("subsystems") or []],
        faults=[_reading(r) for r in spec.get("faults") or []],
    )


def latest() -> Board | None:
    row = db.fetchone("SELECT uid, spec, created_at FROM analysis_board "
                      "ORDER BY created_at DESC, rowid DESC LIMIT 1")
    if row is None:
        return None
    try:
        spec = json.loads(row["spec"])
    except (TypeError, ValueError):
        return None
    if not isinstance(spec, dict):
        return None
    return Board(row["uid"], spec, row["created_at"], to_dashboard(spec))
