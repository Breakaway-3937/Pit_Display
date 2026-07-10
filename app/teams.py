"""
Team registry. Add new teams by inserting into TEAMS.
primary_color / secondary_color drive branding across all screens.
"""

from dataclasses import dataclass, field


@dataclass
class Team:
    number: int
    name: str
    primary_color: str    # hex — used for accents, headers, highlights
    secondary_color: str  # hex — used for secondary text/elements
    location: str = ""
    logo_path: str = ""   # relative to assets/logos/

    @property
    def display_name(self) -> str:
        return f"{self.number} — {self.name}" if self.name else str(self.number)


# ── Registry ──────────────────────────────────────────────────────────────────
# Add more teams here. The control screen will pick them up automatically.

TEAMS: dict[int, Team] = {
    16: Team(
        number=16,
        name="",
        primary_color="#1565C0",   # blue
        secondary_color="#ffffff",
        location="",
    ),
    3937: Team(
        number=3937,
        name="Breakaway",
        primary_color="#C82027",   # Breakaway Red
        secondary_color="#ffffff",
        location="Searcy, Arkansas",
    ),
}


def get_team(number: int) -> "Team | None":
    return TEAMS.get(number)


def all_teams() -> list[Team]:
    return sorted(TEAMS.values(), key=lambda t: t.number)


def register_team(team: Team) -> None:
    TEAMS[team.number] = team
