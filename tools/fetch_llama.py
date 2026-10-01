#!/usr/bin/env python3
"""
Put llama.cpp's `llama-server` in `llama/`, so the packaged app has an engine.

    uv run tools/fetch_llama.py            # this platform's build, if `llama/` is missing
    uv run tools/fetch_llama.py --force    # again, over the top

**Why.** The analysis pipeline (`app/ai/`) needs a model server, and a pit
machine must need nothing installed (CLAUDE.md, "Robot-log analysis"). This
is the same idea as `fetch_vlc.py`: one pinned upstream build, checked, cut
down to what runs, licence kept. The **model** is not fetched here; it's 5 GB
and lives in the data tree, downloaded once from the Analysis panel
(`app/ai/runtime.py`).

**Which build.** Windows: the **Vulkan** build, which uses the pit machines'
Radeon 760M (ROCm doesn't cover it) and falls back to the CPU backends it
ships. macOS (the dev Mac): the arm64 build, Metal. Pinned to one release with
GitHub's own SHA-256 for each archive; a bump is the tag and two digests.

**What's kept:** `llama-server` and every shared library beside it (the
server loads its backends and CPU variants by name at run time, so pruning
them is a crash on someone else's CPU), plus `LICENSE*`. The other tools in
the archive are left behind. llama.cpp is MIT; `SOURCE.txt` records where
this came from.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.console import use_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "llama"
TAG = "b11310"
BUILDS = {
    "win32": (f"llama-{TAG}-bin-win-vulkan-x64.zip",
              "b93a7e765c09ce5151457b71028fae2a61d4887af88d5280faa324f87162b4c1"),
    "darwin": (f"llama-{TAG}-bin-macos-arm64.tar.gz",
               "d0f1c60b33aa56ec09286a7e8c76cf6b4bb0086b4b80debeee806ee99eb7ab80"),
}
URL = "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{name}"


def _keep(name: str) -> bool:
    base = Path(name).name
    return (base in ("llama-server", "llama-server.exe") or base.startswith("LICENSE")
            or base.endswith((".dll", ".dylib", ".so")) or ".so." in base)


def fetch(platform: str, force: bool) -> int:
    if platform not in BUILDS:
        print(f"No pinned llama.cpp build for {platform}; install llama-server yourself.")
        return 1
    exe = DEST / ("llama-server.exe" if platform == "win32" else "llama-server")
    if exe.exists() and not force:
        print(f"{exe.relative_to(ROOT)} is already there (--force to fetch again).")
        return 0
    name, sha = BUILDS[platform]
    url = URL.format(tag=TAG, name=name)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / name
        print(f"Downloading {url}")
        from app import net
        with urllib.request.urlopen(url, timeout=120, context=net.ssl_context()) as r, \
                open(archive, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        got = hashlib.sha256(archive.read_bytes()).hexdigest()
        if got != sha:
            print(f"SHA-256 mismatch for {name}: expected {sha}, got {got}")
            return 1
        shutil.rmtree(DEST, ignore_errors=True)
        DEST.mkdir()
        kept = 0
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if not info.is_dir() and _keep(info.filename):
                        (DEST / Path(info.filename).name).write_bytes(z.read(info))
                        kept += 1
        else:
            with tarfile.open(archive) as t:
                for m in t.getmembers():
                    if (m.isfile() or m.issym()) and _keep(m.name):
                        m.name = Path(m.name).name
                        t.extract(m, DEST, filter="data")
                        kept += 1
        if exe.exists():
            exe.chmod(0o755)
        (DEST / "SOURCE.txt").write_text(
            f"llama.cpp {TAG} (MIT), unmodified upstream build\n{url}\nsha256 {sha}\n"
            f"Source: https://github.com/ggml-org/llama.cpp/tree/{TAG}\n")
    print(f"llama/: {kept} files from {name}")
    return 0 if exe.exists() else 1


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--platform", default=sys.platform, choices=sorted(BUILDS))
    opts = ap.parse_args()
    return fetch(opts.platform, opts.force)


if __name__ == "__main__":
    sys.exit(main())
