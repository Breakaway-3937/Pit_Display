"""
Home's fun facts (`tba_fact`, home/REQUESTS.md R5) for the overhead screens.

Read-only. Breakaway's facts first, then the league's, each by home's `sort`.
Every surface that shows these carries "Powered by The Blue Alliance"
(`app/attribution.py`): the facts are TBA data.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.attribution import TBA_TEXT

CREDIT = TBA_TEXT


@dataclass(frozen=True)
class Fact:
    key: str
    category: str          # '3937' | 'league'
    text: str


def facts() -> list[Fact]:
    """Every fact home sent, ours first. [] before any arrive (or on error)."""
    try:
        from app.db import db
        rows = db.fetchall(
            """SELECT uid, category, text FROM tba_fact
               WHERE text IS NOT NULL AND trim(text) <> ''
               ORDER BY CASE category WHEN '3937' THEN 0 ELSE 1 END,
                        COALESCE(sort, 9999), uid""")
    except Exception:
        return []
    return [Fact(r["uid"], r["category"] or "", r["text"].strip()) for r in rows]
