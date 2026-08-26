"""Presentation Screen A — welcome / team-intro slide rotation."""

from app.windows.presentation_base import PresentationScreen


class PresentationScreenA(PresentationScreen):

    SCREEN_ID = "presentation_a"

    # A asks "is anything wrong?" — the glanceable board. B carries the detail.
    BOARD_CONTENT = "diagnostics"

    SLIDES = [
        (
            "Welcome to Breakaway",
            "Stop by and meet Team 3937 — we'd love to tell you about our season.",
        ),
        (
            "Our Robot This Year",
            "Designed and built from the ground up by our student members.",
        ),
        (
            "Awards & Milestones",
            "Celebrating the accomplishments that define our team's journey.",
        ),
        (
            "Come Ask Us Anything",
            "Questions about FRC, engineering, or our robot? We've got answers.",
        ),
    ]
