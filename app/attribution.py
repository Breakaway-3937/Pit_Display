"""
The credits this app owes its data sources. Conditions of use, not courtesy.

**Nexus** (frc.nexus API docs, v1.8.0): "Include a link back to frc.nexus in
any projects in which you utilize Nexus data."

**The Blue Alliance** (TBA Developer Guidelines, thebluealliance.com/apidocs):
"Please note that your project is 'Powered by The Blue Alliance' with a link
back to thebluealliance.com." Their branding rule: never "The Blue
Alliance", "TBA" or the lamp logo in our app's name or brand identity, so
the credit is **text only**, never their logo, and we say we aren't an
official TBA app. They also ask that links out for a team or event go to the
matching TBA page (`tba_team_url()`).

Where they show (rule, in CLAUDE.md): the **Data sources** section of Control
→ Software Updates carries both, as links; **every surface that shows a
source's data carries its credit**: the Event Feed panel and the Next Match
board (native and on the pit network) for Nexus; anything showing TBA rows
(`tba_*` tables, `match_context`) for TBA. On an overhead screen nothing is
clickable, so the credit is the visible domain. Our own brand still applies:
plain type in the surface's ink, never red, never a badge.
"""

from __future__ import annotations

NEXUS_TEXT = "Event data from frc.nexus"
NEXUS_URL = "https://frc.nexus"

TBA_TEXT = "Powered by The Blue Alliance"
TBA_URL = "https://www.thebluealliance.com"


def tba_team_url(team_number: str | int) -> str:
    """The page TBA asks a link out about a team to go to."""
    return f"{TBA_URL}/team/{team_number}"


def tba_event_url(event_key: str) -> str:
    return f"{TBA_URL}/event/{event_key}"


def link(text: str, url: str) -> str:
    """Rich text for a `LinkLabel`: the link in the label's own ink."""
    return f'<a href="{url}">{text}</a>'
