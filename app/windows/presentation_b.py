"""Presentation Screen B — process / outreach / sponsor slide rotation."""

from app.slides import Slide, ROSTER
from app.windows.presentation_base import PresentationScreen


class PresentationScreenB(PresentationScreen):

    SCREEN_ID = "presentation_b"

    # B asks "what is wrong, on which motor?" — the board you walk up to.
    BOARD_CONTENT = "robot_info"

    SLIDES = [
        Slide(
            eyebrow="Our process",
            title="Our Design Process",
            body="Every mechanism starts with student-led research, prototyping, "
                 "and iteration.",
        ),
        Slide(
            eyebrow="Community",
            title="Community Outreach",
            body="Beyond the field — workshops, demos, and inspiring the next "
                 "generation.",
        ),
        # The sponsors slide is a Roster, not a Statement: a sponsor mark needs
        # a light ground and its own colour, so the stage becomes a grid of
        # white media plates and the surface spends **no** red at all. The
        # cells are placeholders until artwork is supplied — see OPERATOR_GUIDE.md.
        Slide(
            kind=ROSTER,
            eyebrow="Our partners",
            title="Thank You, Sponsors",
            body="None of this is possible without the support of our incredible "
                 "partners.",
        ),
    ]
