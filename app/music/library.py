"""
Local music library — folder scan, tag read, SQLite index.

The library is the product: it works with no network, no account, and no
subscription that can lapse the morning of an event. Scanning is explicit
(a button), never automatic on a timer, so a big folder never stalls the UI
at a bad moment.
"""

from dataclasses import dataclass
from pathlib import Path

from app.db import db

AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma"}


@dataclass(frozen=True)
class Track:
    id: int
    path: str
    title: str
    artist: str
    album: str
    duration: float

    @property
    def exists(self) -> bool:
        return Path(self.path).is_file()

    def label(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title

    def duration_text(self) -> str:
        if self.duration <= 0:
            return "--:--"
        total = int(self.duration)
        return f"{total // 60}:{total % 60:02d}"


def _row_to_track(row) -> Track:
    return Track(
        id=row["id"], path=row["path"], title=row["title"], artist=row["artist"],
        album=row["album"], duration=row["duration"],
    )


def _read_tags(path: Path) -> dict:
    """Best-effort metadata. A file that will not parse still gets indexed."""
    info = {"title": path.stem, "artist": "", "album": "", "duration": 0.0}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(str(path), easy=True)
        if audio is None:
            return info
        tags = audio.tags or {}

        def first(key: str) -> str:
            value = tags.get(key)
            if isinstance(value, (list, tuple)):
                return str(value[0]) if value else ""
            return str(value) if value else ""

        info["title"] = first("title") or path.stem
        info["artist"] = first("artist")
        info["album"] = first("album")
        if audio.info is not None and getattr(audio.info, "length", None):
            info["duration"] = float(audio.info.length)
    except Exception:
        pass
    return info


def scan(folder: Path) -> tuple[int, int]:
    """
    Index every audio file under `folder`. Returns (found, added).

    Re-scanning is safe and cheap: existing rows are matched by path and left
    alone, so playlists that reference them survive. Files that have gone away
    are flagged `missing` rather than deleted, for the same reason.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return (0, 0)

    seen: set[str] = set()
    found = added = 0

    with db.transaction():
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            found += 1
            key = str(path.resolve())
            seen.add(key)

            row = db.fetchone("SELECT id FROM tracks WHERE path = ?", (key,))
            if row is not None:
                db.execute("UPDATE tracks SET missing = 0 WHERE id = ?", (row["id"],))
                continue

            tags = _read_tags(path)
            db.execute(
                """INSERT INTO tracks (path, title, artist, album, duration)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, tags["title"], tags["artist"], tags["album"],
                 tags["duration"]),
            )
            added += 1

        for row in db.fetchall("SELECT id, path FROM tracks WHERE missing = 0"):
            if row["path"] not in seen and not Path(row["path"]).is_file():
                db.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (row["id"],))

    return (found, added)


def all_tracks(search: str = "") -> list[Track]:
    sql = "SELECT * FROM tracks WHERE missing = 0"
    params: list = []
    if search.strip():
        sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
        like = f"%{search.strip()}%"
        params += [like, like, like]
    sql += " ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE"
    return [_row_to_track(r) for r in db.fetchall(sql, tuple(params))]
