"""Presentation Screen B — process / outreach / sponsor slide rotation."""

from app.windows.presentation_base import PresentationScreen


class PresentationScreenB(PresentationScreen):

    SCREEN_ID = "presentation_b"

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
