"""
Team settings that live in JSON, and team files that live on disk.

**Settings documents** (`tbl = "setting"`): the team-owned keys of a settings
JSON, never the whole file. `nexus.json` carries the event and how to follow
it; its `last_poll` / `last_result` are this machine's own status. The
updater's `channel` stays local on purpose (one machine takes a beta before
the rest), and `webcast.json` is this pit's cabling, so it isn't here at all.

**Files** (`tbl = "file"`): judges slides and the CAD model with its
subsystem map, as `{root}/{name}` → the blob's SHA-256. Only the data tree is
scanned: a machine still using the CAD model that shipped in the bundle has
nothing in `assets/cad/` and pushes nothing. A file changed in the last
`SETTLE_S` seconds is skipped (it may still be copying in).

Change detection is by hash against `sync_doc`, which records what this
machine last pushed or applied, so an applied change is never echoed back.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from app import paths

SETTING = "setting"
FILE = "file"
SETTLE_S = 10

# uid -> (module path, team-owned keys)
SETTING_DOCS: dict[str, tuple[str, tuple[str, ...]]] = {
    "nexus": ("app.nexus.settings",
              ("event_key", "auto_poll", "poll_interval_s", "slow_poll_interval_s",
               "relay_enabled", "relay_url", "fallback_after_s")),
    "update": ("app.update.settings", ("auto_check", "check_interval_hours")),
    "transfer": ("app.db.sync.transfer", ("start", "end", "enforce")),
    # Which of home's datasets the adults have cleared for the screens.
    "datasets": ("app.dataset_settings", ("enabled",)),
    # Admin-edited screen wording (app/wording.py): one edit, every pit.
    "wording": ("app.wording", ("texts",)),
    # The crew's robot brief for the analyst (app/ai/brief.py): one per team.
    "brief": ("app.ai.brief", ("text",)),
}

FILE_ROOTS: dict[str, tuple[str, ...]] = {
    "judges_slides": ("assets", "judges_slides"),
    "cad": ("assets", "cad"),
    # Songs other machines sent. This machine's own songs stay wherever it
    # scanned them; they're named from `tracks` (`music_files()`), not here.
    "music": ("music",),
}
MUSIC = "music"
_SHA_PREFIX = 16


def _module(name: str):
    import importlib
    return importlib.import_module(name)


def hash_json(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def setting_data(uid: str) -> dict | None:
    spec = SETTING_DOCS.get(uid)
    if spec is None:
        return None
    values = _module(spec[0]).load()
    return {k: values.get(k) for k in spec[1]}


def local_settings() -> dict[str, tuple[dict, str]]:
    out = {}
    for uid in SETTING_DOCS:
        data = setting_data(uid)
        if data is not None:
            out[uid] = (data, hash_json(data))
    return out


def apply_setting(uid: str, data: dict) -> str | None:
    """Write the team keys. Returns the new hash, or None for an unknown doc."""
    spec = SETTING_DOCS.get(uid)
    if spec is None:
        return None
    _module(spec[0]).save(**{k: data[k] for k in spec[1] if k in data})
    return hash_json(setting_data(uid))


# ── files ────────────────────────────────────────────────────────────────────

def root(name: str) -> Path:
    return paths.data_dir(*FILE_ROOTS[name])


def split(uid: str) -> tuple[str, str] | None:
    """`judges_slides/slide 1.png` → (root, name), refusing anything path-like."""
    head, _, name = uid.partition("/")
    if head not in FILE_ROOTS or not name or name != Path(name).name \
            or name.startswith(".") or name in ("..", "."):
        return None
    return head, name


class FileHasher:
    """Hashes each file once per (size, mtime): the CAD model is ~300 MB."""

    def __init__(self):
        self._cache: dict[str, tuple[int, int, str]] = {}

    def sha(self, path: Path) -> str:
        from app.db.sync.client import sha256_file
        st = path.stat()
        key = str(path)
        hit = self._cache.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        digest = sha256_file(path)
        self._cache[key] = (st.st_size, st.st_mtime_ns, digest)
        return digest

    def local_files(self, conn=None) -> dict[str, tuple[Path, str, int]]:
        """uid → (path, sha, bytes) for every settled team file on this machine.
        With `conn`, every scanned song too (`music_files()`)."""
        out = {}
        now = time.time()
        if conn is not None:
            out.update(music_files(conn, self))
        for name in FILE_ROOTS:
            if name == MUSIC:
                continue                  # songs come from the library, above
            folder = root(name)
            for p in sorted(folder.iterdir()) if folder.is_dir() else []:
                if not p.is_file() or p.name.startswith(".") or p.name.endswith(".part"):
                    continue
                st = p.stat()
                if now - st.st_mtime < SETTLE_S:
                    continue
                out[f"{name}/{p.name}"] = (p, self.sha(p), st.st_size)
        return out


def music_name(sha: str, filename: str) -> str:
    """A song's name in the team library: `<sha prefix>_<file name>`, so two
    `01 Track.mp3`s never collide and a received copy keeps its name."""
    prefix = sha[:_SHA_PREFIX]
    return filename if filename.startswith(prefix + "_") else f"{prefix}_{filename}"


def music_files(conn, hasher: "FileHasher") -> dict[str, tuple[Path, str, int]]:
    """uid → (path, sha, bytes) for every scanned song that's on disk and not
    deleted for the team. The sha is remembered in `tracks` per (size, mtime)."""
    out = {}
    now = time.time()
    rows = conn.execute(
        "SELECT id, path, sha, sha_size, sha_mtime_ns FROM tracks "
        "WHERE missing = 0 AND team_deleted = 0").fetchall()
    for tid, p, sha, size, mtime in rows:
        path = Path(p)
        try:
            st = path.stat()
        except OSError:
            continue
        if now - st.st_mtime < SETTLE_S:
            continue
        if not sha or size != st.st_size or mtime != st.st_mtime_ns:
            sha = hasher.sha(path)
            conn.execute("UPDATE tracks SET sha = ?, sha_size = ?, sha_mtime_ns = ? "
                         "WHERE id = ?", (sha, st.st_size, st.st_mtime_ns, tid))
        uid = f"{MUSIC}/{music_name(sha, path.name)}"
        if split(uid) is not None:
            out[uid] = (path, sha, st.st_size)
    return out


def music_source(conn, uid: str) -> Path | None:
    """Where this machine holds the song `uid`: the team folder, or the file
    it scanned from its own library."""
    parts = split(uid)
    if parts is None or parts[0] != MUSIC:
        return None
    here = root(MUSIC) / parts[1]
    if here.is_file():
        return here
    row = conn.execute("SELECT path FROM tracks WHERE sha LIKE ? AND missing = 0 LIMIT 1",
                       (parts[1][:_SHA_PREFIX] + "%",)).fetchone()
    return Path(row[0]) if row else None


def remove_file(uid: str) -> None:
    parts = split(uid)
    if parts is None:
        return
    try:
        (root(parts[0]) / parts[1]).unlink(missing_ok=True)
    except OSError:
        pass


def file_path(uid: str) -> Path | None:
    parts = split(uid)
    return None if parts is None else root(parts[0]) / parts[1]

