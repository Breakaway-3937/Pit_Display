"""
The release feed — GitHub Releases on a **private** repository.

Private is the constraint that shapes this file. On a public repo an update is
a plain URL and there is nothing to authenticate; on a private one every byte
comes through the API with a token, and two things follow that are easy to get
wrong and fail in confusing ways:

* **A release asset is fetched by id, not by its download URL.**
  `browser_download_url` is a web-session URL and answers 404 to a token. The
  API endpoint `/releases/assets/{id}` with `Accept: application/octet-stream`
  is the one that works.
* **The Authorization header must be dropped on the redirect.** That endpoint
  302s to object storage, which signs its own URL and rejects any request that
  also carries a bearer token — "only one auth mechanism allowed", as a 400 on
  a download that looked fine a moment earlier. `_Redirect` strips it whenever
  the host changes.

**The token is a file, not a setting.** `update_token` in the data directory,
or `$PIT_UPDATE_TOKEN`. A fine-grained PAT with `Contents: read` on this one
repository and nothing else — it can fetch releases and can do nothing whatever
if it leaks off the pit machine. No token means updates are simply off, which
is the correct state for a machine nobody has set up yet.

Every release carries a `manifest.json` asset naming the per-platform zip and
its SHA-256. **A release without one is refused rather than trusted**: the hash
is the only thing standing between a 400 MB download and an install folder, and
"the asset was named right" is not a check.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app import paths

OWNER = os.environ.get("PIT_UPDATE_OWNER", "Breakaway-3937")
REPO = os.environ.get("PIT_UPDATE_REPO", "Pit_Display")
API = os.environ.get("PIT_UPDATE_API", "https://api.github.com")

TOKEN_FILE = "update_token"
MANIFEST_ASSET = "manifest.json"

_TIMEOUT = 30
_CHUNK = 1 << 20          # 1 MiB — a 400 MB download in ~400 progress ticks


class UpdateError(Exception):
    """Anything that stops an update, phrased for an operator in a pit."""


# ── Which platform's zip this machine wants ──────────────────────────────────

def platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# ── The token ────────────────────────────────────────────────────────────────

def token_path() -> Path:
    return paths.data_root() / TOKEN_FILE


def token() -> str:
    """The PAT, or `""`. Environment first, so a test can override the file."""
    env = os.environ.get("PIT_UPDATE_TOKEN", "").strip()
    if env:
        return env
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def configured() -> bool:
    return bool(token())


def set_token(value: str) -> None:
    """Write or clear the token file."""
    p = token_path()
    value = value.strip()
    if not value:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)        # no-op on Windows; correct everywhere else
    except OSError:
        pass


# ── HTTP ─────────────────────────────────────────────────────────────────────

class _Redirect(urllib.request.HTTPRedirectHandler):
    """
    Follow the redirect, but not with the bearer token.

    GitHub hands a release asset off to signed object storage. That URL carries
    its own credentials in the query string and rejects a request that *also*
    has an `Authorization` header, so keeping ours turns a working download
    into a 400 nobody can read.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if (urllib.parse.urlparse(newurl).netloc
                != urllib.parse.urlparse(req.full_url).netloc):
            for name in list(new.headers):
                if name.lower() == "authorization":
                    del new.headers[name]
            new.unredirected_hdrs.pop("Authorization", None)
        return new


# Verified the way the OS verifies — see app/net.py for why this matters on
# a pit machine and not on a home one.
from app import net  # noqa: E402

_opener = urllib.request.build_opener(
    _Redirect, urllib.request.HTTPSHandler(context=net.ssl_context()))


def _request(url: str, accept: str):
    tok = token()
    if not tok:
        raise UpdateError(
            "No update token on this machine. The repository is private, so "
            "the app needs a GitHub token with read access to it — see "
            "DEPLOYMENT.md, “Setting up updates”.")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "breakaway-pit-display",
    })
    return req


def _open(url: str, accept: str):
    try:
        return _opener.open(_request(url, accept), timeout=_TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise UpdateError(
                f"GitHub refused the update token ({e.code}). It may have "
                "expired, or it may not have Contents: read on "
                f"{OWNER}/{REPO}.") from e
        if e.code == 404:
            raise UpdateError(
                f"{OWNER}/{REPO} not found with this token — check the "
                "repository name and that the token can see it.") from e
        raise UpdateError(f"GitHub returned {e.code} {e.reason}.") from e
    except urllib.error.URLError as e:
        host = urllib.parse.urlparse(url).netloc or "github.com"
        raise UpdateError(net.describe_url_error(e, host, "GitHub")) from e
    except OSError as e:
        raise UpdateError(f"Could not reach GitHub: {e}") from e


def _api_json(path: str):
    with _open(f"{API}{path}", "application/vnd.github+json") as r:
        return json.loads(r.read().decode("utf-8"))


# ── A release ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    channel: str
    notes: str
    published: str
    asset_id: int
    asset_name: str
    size: int
    sha256: str

    @property
    def size_mb(self) -> float:
        return self.size / (1024 * 1024)


def _asset_id(release: dict, name: str) -> int | None:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return int(asset["id"])
    return None


def _fetch_manifest(asset_id: int) -> dict:
    with _open(f"{API}/repos/{OWNER}/{REPO}/releases/assets/{asset_id}",
               "application/octet-stream") as r:
        return json.loads(r.read().decode("utf-8"))


def latest(channel: str = "stable") -> Release | None:
    """
    The newest release this channel can see, or None if there are none.

    `stable` skips prereleases; `beta` takes them too, which is the whole of
    the difference between the two channels. Drafts are invisible to both —
    a draft is a release somebody has not finished writing.
    """
    releases = _api_json(f"/repos/{OWNER}/{REPO}/releases?per_page=30")
    if not isinstance(releases, list):
        raise UpdateError("GitHub returned something that is not a release list.")

    # Highest version first, never the API's order. GitHub lists releases by
    # the tagged commit's date and then by tag name *as text*, so on one day
    # "beta.9" sorts above "beta.10" and "beta.11": every pit stopped at
    # beta.9 (2026-10-03). The manifest's version is the authority below;
    # the tag only orders the candidates.
    from app.version import parse
    releases = sorted((r for r in releases if isinstance(r, dict)),
                      key=lambda r: parse(str(r.get("tag_name", ""))), reverse=True)

    for entry in releases:                 # highest version first
        if entry.get("draft"):
            continue
        if channel != "beta" and entry.get("prerelease"):
            continue

        manifest_id = _asset_id(entry, MANIFEST_ASSET)
        if manifest_id is None:
            # A hand-made release with no manifest cannot be verified, so it
            # is not an update. Skip it rather than fail the whole check —
            # the next one down may be a real build.
            continue

        manifest = _fetch_manifest(manifest_id)
        platform = manifest.get("platforms", {}).get(platform_key())
        if not platform:
            continue                       # no build for this OS in that release

        asset_id = _asset_id(entry, platform.get("asset", ""))
        if asset_id is None:
            continue

        return Release(
            version=str(manifest.get("version", entry.get("tag_name", "")).lstrip("vV")),
            tag=str(entry.get("tag_name", "")),
            channel="beta" if entry.get("prerelease") else "stable",
            notes=(entry.get("body") or "").strip(),
            published=str(entry.get("published_at", "")),
            asset_id=asset_id,
            asset_name=str(platform["asset"]),
            size=int(platform.get("size", 0)),
            sha256=str(platform.get("sha256", "")).lower(),
        )
    return None


def download(release: Release, dest: Path,
             progress: Callable[[str, float], None] | None = None) -> Path:
    """
    Stream the zip to `dest`, hashing as it goes. Returns `dest`.

    The hash is computed from the bytes as they land rather than by re-reading
    the file afterwards — a 400 MB re-read on a pit laptop's disk is a minute
    of nothing happening, and the bytes are already in hand.
    """
    if not release.sha256:
        raise UpdateError("That release has no checksum in its manifest, so it "
                          "cannot be verified. Refusing to install it.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    got = 0

    with _open(f"{API}/repos/{OWNER}/{REPO}/releases/assets/{release.asset_id}",
               "application/octet-stream") as r:
        total = release.size or int(r.headers.get("Content-Length") or 0)
        with open(partial, "wb") as f:
            while True:
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(f"Downloading {got / 1048576:.0f} of "
                             f"{total / 1048576:.0f} MB", got / total)

    actual = digest.hexdigest()
    if actual != release.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError(
            "The download does not match its checksum and has been discarded. "
            f"Expected {release.sha256[:12]}…, got {actual[:12]}…. "
            "Try again; if it happens twice, the release is bad.")

    partial.replace(dest)
    return dest
