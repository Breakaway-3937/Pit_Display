"""
What this build *is* — the one fact an install needs about itself.

A machine that cannot name its own version cannot be updated: there is nothing
to compare a release against. Everything else in `app/update/` is built on this
string.

**It is stamped at build time, not maintained by hand.** `tools/stamp_version.py`
rewrites this file from the git tag in CI, so the version on the pit machine is
the tag that produced it and can never drift from it. A checkout keeps the
sentinel below, and `is_release()` is False — which is how the updater knows it
is running from source and must not touch anything.

Ordering is semver-shaped and deliberately small: `1.4.2`, `1.4.2-beta.3`, and
build metadata after `+` that is compared as nothing at all. A prerelease sorts
*below* the release it leads to, which is what makes `stable` and `beta` two
channels rather than two names for the same feed.
"""

from __future__ import annotations

# ── Stamped by tools/stamp_version.py — do not edit by hand ──────────────────
# Nothing may follow these four values on their own lines: the stamper rewrites
# each line whole, so a trailing comment is a comment it would eat.
# CHANNEL is "stable" | "beta" | "dev"; BUILT is ISO-8601 UTC.
VERSION = "0.0.0+dev"
CHANNEL = "dev"
COMMIT = ""
BUILT = ""
# ─────────────────────────────────────────────────────────────────────────────


def is_release() -> bool:
    """False from a checkout or any build nobody stamped."""
    return VERSION != "0.0.0+dev" and CHANNEL in ("stable", "beta")


def describe() -> str:
    parts = [VERSION]
    if COMMIT:
        parts.append(COMMIT[:7])
    if BUILT:
        parts.append(BUILT)
    return "  ".join(parts)


def parse(version: str) -> tuple[tuple[int, ...], tuple]:
    """
    `"v1.4.2-beta.3+abc"` → `((1, 4, 2), (("beta",), (3,)))`, sortable.

    Build metadata is dropped: two builds of the same version are the same
    version, which is exactly what semver says and what a rebuilt tag means.
    """
    v = version.strip().lstrip("vV").split("+", 1)[0]
    release, _, pre = v.partition("-")

    numbers: list[int] = []
    for part in release.split("."):
        try:
            numbers.append(int(part))
        except ValueError:
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)

    # An *absent* prerelease must sort above a present one, so it gets a
    # marker that compares higher than any identifier tuple.
    if not pre:
        return tuple(numbers), (1,)

    ids: list[tuple] = []
    for part in pre.split("."):
        # Numeric identifiers compare numerically and below alphanumerics.
        if part.isdigit():
            ids.append((0, int(part), ""))
        else:
            ids.append((1, 0, part))
    return tuple(numbers), (0, tuple(ids))


def is_newer(candidate: str, current: str = "") -> bool:
    """Is `candidate` a version this install should move to?"""
    return parse(candidate) > parse(current or VERSION)
