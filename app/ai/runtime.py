"""
The engine the app ships: llama.cpp's `llama-server`, and the model it runs.

**Nothing to install.** The Windows build carries `llama/` (fetched by
`tools/fetch_llama.py`: `llama-server.exe` and its DLLs, the Vulkan build,
which uses the Radeon 760M and falls back to the CPU). The **model** is not in
the build: it's 5 GB and every update installs a new folder, so it lives in
the data tree (`models/`), is downloaded **once** from the panel, verified
against the SHA-256 pinned here, and survives every update. A machine with a
`llama-server` on PATH (the dev Mac: `brew install llama.cpp`) uses that.

**Started for a run, stopped when idle.** `ensure()` starts the server on a
free local port and waits for `/health`; the analysis service calls `stop()`
`keep_alive` after the last run, so a 16 GB pit machine holds the model's
memory only while analysing. On Windows the process runs below normal priority
with no console window, so the overhead screens and the music keep theirs.

`ollama.py` remains for machines that already run Ollama (`engine` setting).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app import paths

# Pinned: the file, where it comes from, and what it must hash to. Hugging
# Face's own LFS sha256 for the official Qwen release. Measured on the pit
# constraint (16 GB): ~6.7 GB resident at a 16k context.
MODEL = {
    "name": "qwen3:8b",
    "file": "Qwen3-8B-Q4_K_M.gguf",
    "url": "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
    "sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
    "bytes": 5_027_783_488,
}

EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"

# Memory, for the 16 GB constraint. Measured on the dev Mac (2026-09-30), the
# defaults peaked at 9.0 GB: a host-side prompt cache of up to 8 GB
# (`--cache-ram`), automatic parallel slots, an f16 KV cache. A run is one
# conversation at a time, whose prefix the slot already reuses, so: one slot,
# a small prompt cache, flash attention and an 8-bit KV cache (what Ollama ran
# with at 6.7 GB).
_LEAN = ("-np", "1", "-cram", "256", "-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0")
_CHUNK = 4 * 1024 * 1024


class DownloadError(RuntimeError):
    pass


def binary() -> Path | None:
    """The bundled server, else one on PATH (a developer's), else None."""
    bundled = paths.resource("llama", EXE)
    if bundled.is_file():
        return bundled
    found = shutil.which("llama-server")
    return Path(found) if found else None


def model_path() -> Path:
    """`models/` in the data tree; `PIT_AI_MODELS` names another folder (one copy
    of the 5 GB file shared by several installs or checks)."""
    override = os.environ.get("PIT_AI_MODELS")
    folder = Path(override).expanduser() if override else paths.data_dir("models")
    return folder / MODEL["file"]


def _verified_marker() -> Path:
    return model_path().with_suffix(".verified")


def model_ready() -> bool:
    """Present, the right size, and hashed once already (the marker holds the hash)."""
    p = model_path()
    try:
        return (p.is_file() and p.stat().st_size == MODEL["bytes"]
                and _verified_marker().read_text().strip() == MODEL["sha256"])
    except OSError:
        return False


def download(progress=None, cancel: threading.Event | None = None) -> None:
    """
    Fetch the model into `models/`, resuming a partial download, verifying the
    SHA-256 before it's used. `progress(done_bytes, total_bytes)`. Raises
    `DownloadError` with a sentence an operator can act on.
    """
    from app import net
    dest = model_path()
    part = dest.with_suffix(".part")
    digest = hashlib.sha256()
    have = part.stat().st_size if part.exists() else 0
    if have > MODEL["bytes"]:
        part.unlink()
        have = 0
    if have:                                   # resume: the hash covers it all
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(_CHUNK), b""):
                digest.update(block)
    req = urllib.request.Request(MODEL["url"], headers={"User-Agent": "breakaway-pit"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    host = urllib.request.urlparse(MODEL["url"]).hostname or ""
    try:
        with urllib.request.urlopen(req, timeout=60, context=net.ssl_context()) as r:
            if have and r.status != 206:      # the server ignored the range: start over
                have, digest = 0, hashlib.sha256()
            with open(part, "ab" if have else "wb") as f:
                done = have
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadError("cancelled; it resumes from here next time")
                    block = r.read(_CHUNK)
                    if not block:
                        break
                    f.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress:
                        progress(done, MODEL["bytes"])
    except urllib.error.URLError as e:
        raise DownloadError(net.describe_url_error(e, host, "the model download")) from None
    except OSError as e:
        raise DownloadError(f"couldn't write {part.name}: {e}") from None
    if part.stat().st_size != MODEL["bytes"]:
        raise DownloadError(f"incomplete ({part.stat().st_size:,} of {MODEL['bytes']:,} "
                            f"bytes); try again to resume")
    if digest.hexdigest() != MODEL["sha256"]:
        part.unlink()
        raise DownloadError("the file didn't match its published SHA-256 and was deleted; "
                            "try again (a proxy or filter may be altering downloads)")
    os.replace(part, dest)
    _verified_marker().write_text(MODEL["sha256"] + "\n")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Runtime:
    """One llama-server child process, started on demand."""

    def __init__(self, ctx: int = 16384):
        self.ctx = ctx
        self._proc: subprocess.Popen | None = None
        self._url = ""
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def available(self) -> bool:
        return binary() is not None and model_ready()

    def ensure(self, timeout_s: float = 180.0) -> str:
        """The server's base URL, starting it if needed. Blocks until healthy."""
        with self._lock:
            if self.running:
                return self._url
            exe = binary()
            if exe is None:
                raise RuntimeError("llama-server isn't bundled with this build")
            if not model_ready():
                raise RuntimeError("the model isn't downloaded yet")
            port = _free_port()
            log = open(paths.data("llama.log"), "ab")
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
            self._proc = subprocess.Popen(
                [str(exe), "-m", str(model_path()), "--host", "127.0.0.1",
                 "--port", str(port), "-c", str(self.ctx), "-ngl", "99", "--jinja",
                 "--no-webui", *_LEAN],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags)
            log.close()
            self._url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    raise RuntimeError(f"llama-server exited ({self._proc.returncode}); "
                                       f"see llama.log")
                try:
                    with urllib.request.urlopen(self._url + "/health", timeout=2) as r:
                        if r.status == 200:
                            return self._url
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(0.5)
            self.stop()
            raise RuntimeError("llama-server didn't become ready; see llama.log")

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
