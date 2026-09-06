"""Presentation Screen A — welcome / team-intro slide rotation."""

from app.slides import Slide
from app.windows.presentation_base import PresentationScreen


class PresentationScreenA(PresentationScreen):

    SCREEN_ID = "presentation_a"

    # A asks "is anything wrong?" — the glanceable board. B carries the detail.
    BOARD_CONTENT = "diagnostics"

    # The eyebrow names the slide's subject, not the team: the team is already
    # in the header band of every surface, and repeating it there spends the
    # one line of small type that could have told a visitor what they are
    # looking at.
    SLIDES = [
        Slide(
            eyebrow="Welcome",
            title="Welcome to Breakaway",
            body="Stop by and meet Team 3937 — we'd love to tell you about our season.",
        ),
        Slide(
            eyebrow="The robot",
            title="Our Robot This Year",
            body="Designed and built from the ground up by our student members.",
        ),
        Slide(
            eyebrow="Recognition",
            title="Awards & Milestones",
            body="Celebrating the accomplishments that define our team's journey.",
        ),
        Slide(
            eyebrow="Come talk to us",
            title="Come Ask Us Anything",
            body="Questions about FRC, engineering, or our robot? We've got answers.",
        ),
    ]
