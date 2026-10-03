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


# A column's title for each category home sends (home/REQUESTS.md: facts are
# Breakaway's or Arkansas's, "3937" first). Anything else reads as the league.
TITLES = {"3937": "Breakaway", "arkansas": "Across Arkansas"}


def title(category: str) -> str:
    return TITLES.get(category, "Across the league")


@dataclass(frozen=True)
class Fact:
    key: str
    category: str          # '3937' | 'arkansas'
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
    found = [Fact(r["uid"], r["category"] or "", r["text"].strip()) for r in rows]
    if found:
        return found
    # Home sends only its standard datasets now (home/REQUESTS.md R10): the
    # same sentences arrive as the `fun_facts` dataset, already in order.
    from app import datasets
    facts = [Fact(k, c, t) for k, c, t in datasets.fun_facts()]
    return sorted(facts, key=lambda f: 0 if f.category == "3937" else 1)
