"""
The sync hub's HTTP API, as plain blocking calls. Run off the GUI thread.

Every call carries the bearer token and this machine's identity
(`X-Pit-Machine`, `X-Pit-Name`, `X-Pit-Version`). HTTPS goes through
`net.ssl_context()` like every other call in the app (CLAUDE.md, "Python HTTPS
goes through app/net.py").

Files are content-addressed: a blob's name *is* its SHA-256, so an upload that
was interrupted is simply started again, one that already exists is skipped,
and a download is checked against its name before anything uses it. Files
over `SINGLE_PUT_MAX` go up in `PART_SIZE` parts (Workers refuse a request body
over 100 MB).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from app import net

TIMEOUT_S = 30
BLOB_TIMEOUT_S = 300
SINGLE_PUT_MAX = 64 * 1024 * 1024
PART_SIZE = 32 * 1024 * 1024
CHUNK = 1024 * 1024


class SyncError(Exception):
    """An operator-readable failure. `status` is the HTTP code, if there was one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


class HubClient:

    def __init__(self, url: str, token: str, machine_id: str, machine_name: str,
                 version: str = ""):
        self.url = url.rstrip("/")
        self.host = urllib.parse.urlparse(self.url).netloc
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-Pit-Machine": machine_id,
            "X-Pit-Name": machine_name,
            "X-Pit-Version": version,
            "User-Agent": "breakaway-pit-display",
        }

    # ── plumbing ──────────────────────────────────────────────────────────

    def _open(self, method: str, path: str, *, body: bytes | Any = None,
              headers: dict[str, str] | None = None, timeout: float = TIMEOUT_S):
        req = urllib.request.Request(self.url + path, data=body, method=method,
                                     headers={**self._headers, **(headers or {})})
        # Plain http is allowed only for `wrangler dev` on this machine.
        context = net.ssl_context() if self.url.startswith("https://") else None
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=context)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
            except Exception:
                pass
            if e.code == 401:
                raise SyncError("The sync hub refused this machine's token. "
                                "Check secrets/sync_token.", 401) from None
            raise SyncError(f"Sync hub answered HTTP {e.code}"
                            + (f": {detail}" if detail else ""), e.code) from None
        except urllib.error.URLError as e:
            raise SyncError(net.describe_url_error(e, self.host, "the sync hub")) from None
        except (TimeoutError, OSError) as e:
            raise SyncError(f"Could not reach the sync hub: {e}") from None

    def _json(self, method: str, path: str, payload: Any = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        with self._open(method, path, body=body, headers=headers) as r:
            return json.loads(r.read().decode("utf-8"))

    # ── rows ──────────────────────────────────────────────────────────────

    def push(self, changes: list[dict], force: bool = False) -> dict:
        return self._json("POST", "/v1/push", {"changes": changes, "force": force})

    def changes(self, since: int, limit: int = 500) -> dict:
        return self._json("GET", f"/v1/changes?since={int(since)}&limit={int(limit)}")

    def status(self) -> dict:
        return self._json("GET", "/v1/status")

    def blobs(self, limit: int = 1000) -> list[dict]:
        """Every blob the hub holds, oldest first: sha, kind, name, origin (the
        machine that uploaded it). The hub serves at most 1000 per call."""
        return list(self._json("GET", f"/v1/blobs?limit={int(limit)}").get("blobs", []))

    # ── blobs ─────────────────────────────────────────────────────────────

    def has_blob(self, sha: str) -> bool:
        try:
            with self._open("HEAD", f"/v1/blob/{sha}"):
                return True
        except SyncError as e:
            if e.status == 404:
                return False
            raise

    def put_blob(self, path: Path, sha: str, kind: str, name: str,
                 progress: Callable[[int, int], None] | None = None) -> None:
        """Upload `path` as blob `sha`. Skipped if the hub already has it."""
        if self.has_blob(sha):
            return
        size = os.path.getsize(path)
        meta = {"X-Blob-Kind": kind, "X-Blob-Name": name[:300]}
        if size <= SINGLE_PUT_MAX:
            with open(path, "rb") as f:
                data = f.read()
            with self._open("PUT", f"/v1/blob/{sha}", body=data,
                            headers={**meta, "Content-Type": "application/octet-stream",
                                     "Content-Length": str(size)},
                            timeout=BLOB_TIMEOUT_S):
                pass
            if progress:
                progress(size, size)
            return

        start = self._json_with("POST", f"/v1/blob/{sha}/mpu", meta)
        if start.get("existed"):
            return
        upload_id = start["uploadId"]
        parts: list[dict] = []
        sent = 0
        try:
            with open(path, "rb") as f:
                n = 0
                while True:
                    block = f.read(PART_SIZE)
                    if not block:
                        break
                    n += 1
                    with self._open("PUT", f"/v1/blob/{sha}/mpu/{upload_id}/{n}",
                                    body=block,
                                    headers={"Content-Type": "application/octet-stream",
                                             "Content-Length": str(len(block))},
                                    timeout=BLOB_TIMEOUT_S) as r:
                        parts.append(json.loads(r.read().decode("utf-8")))
                    sent += len(block)
                    if progress:
                        progress(sent, size)
            self._json_with("POST", f"/v1/blob/{sha}/mpu/{upload_id}/complete", meta,
                            {"parts": parts, "bytes": size})
        except Exception:
            try:
                with self._open("DELETE", f"/v1/blob/{sha}/mpu/{upload_id}"):
                    pass
            except SyncError:
                pass
            raise

    def _json_with(self, method: str, path: str, headers: dict[str, str],
                   payload: Any = None) -> Any:
        body = json.dumps(payload or {}).encode("utf-8")
        with self._open(method, path, body=body,
                        headers={**headers, "Content-Type": "application/json"}) as r:
            return json.loads(r.read().decode("utf-8"))

    def get_blob(self, sha: str, dest: Path) -> Path:
        """Download blob `sha` to `dest`, verified. Writes beside it, then renames."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        h = hashlib.sha256()
        with self._open("GET", f"/v1/blob/{sha}", timeout=BLOB_TIMEOUT_S) as r, \
                open(part, "wb") as f:
            for block in iter(lambda: r.read(CHUNK), b""):
                h.update(block)
                f.write(block)
        if h.hexdigest() != sha:
            part.unlink(missing_ok=True)
            raise SyncError(f"Downloaded file {sha[:12]}… failed its checksum; "
                            "it will be fetched again next cycle.")
        os.replace(part, dest)
        return dest
