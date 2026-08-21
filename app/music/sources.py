"""
Music sources.

`LocalSource` is the product and the permanent fallback. `SpotifySource` is a
deliberate stub: the groundwork is here and the seam is real, but it is not
wired up, because depending on it would make the pit's music worse.

Why Spotify is not the base (checked Aug 2026):
  - Player endpoints do still work (play/pause/skip/seek/volume/queue), but
    every one of them needs internet, and this app exists for venues where
    that is exactly what you do not have.
  - Dev Mode caps an app at five users and requires the app owner to hold an
    active Premium subscription or the whole thing stops working. Extended
    quota needs a registered business with 250k monthly actives.
  - Audio Analysis / Audio Features were withdrawn for new apps in Nov 2024,
    so there is no beat data to drive lighting from either.
  - There is no PCM access, so the in-app equaliser can never apply to it.

If Spotify is ever needed, the honest fallback is to run the desktop client on
the pit machine alongside this app.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.music import library
from app.music.library import Track


@runtime_checkable
class MusicSource(Protocol):
    """A place tracks come from."""

    key: str
    label: str

    def available(self) -> bool: ...
    def tracks(self, search: str = "") -> list[Track]: ...
    def resolve(self, track: Track) -> str | None: ...


@dataclass
class LocalSource:
    """Files on disk, indexed in SQLite. Always available."""

    key: str = "local"
    label: str = "Local Library"

    def available(self) -> bool:
        return True

    def tracks(self, search: str = "") -> list[Track]:
        return library.all_tracks(search=search)

    def resolve(self, track: Track) -> str | None:
        """A playable path, or None if the file has gone missing since the scan."""
        return track.path if track.exists else None


@dataclass
class SpotifySource:
    """
    Not implemented. Present so the seam is visible and the UI can show why.

    Building this means: OAuth PKCE with a loopback redirect, token refresh,
    device selection via /me/player/devices, and transport calls against
    /me/player/*. All of it is additive — nothing above it should ever assume
    this source exists.
    """

    key: str = "spotify"
    label: str = "Spotify"
    reason: str = (
        "Not enabled. Needs internet, a Premium account on the app owner, and "
        "cannot be equalised. Run the desktop client alongside the app instead."
    )

    def available(self) -> bool:
        return False

    def tracks(self, search: str = "") -> list[Track]:
        return []

    def resolve(self, track: Track) -> str | None:
        return None
