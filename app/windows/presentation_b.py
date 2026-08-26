"""Presentation Screen B — process / outreach / sponsor slide rotation."""

from app.windows.presentation_base import PresentationScreen


class PresentationScreenB(PresentationScreen):

    SCREEN_ID = "presentation_b"

    # B asks "what is wrong, on which motor?" — the board you walk up to.
    BOARD_CONTENT = "robot_info"

    SLIDES = [
        (
            "Our Design Process",
            "Every mechanism starts with student-led research, prototyping, and iteration.",
        ),
        (
            "Community Outreach",
            "Beyond the field — workshops, demos, and inspiring the next generation.",
        ),
        (
            "Thank You, Sponsors",
            "None of this is possible without the support of our incredible partners.",
        ),
    ]
