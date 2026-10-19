"""
Rewrite an Onshape glTF export so the CAD viewer can draw it quickly.

    uv run tools/cad_optimize.py <export.gltf|.glb> [out.glb] [--compress]

Onshape exports one primitive per CAD face, as JSON with the geometry in
base64. The 2026 robot was 360 MB, 72,625 draw calls and 147 ms per frame in
the app's Chromium. Rewritten, it is 148 MB, 979 draw calls and 14 ms per
frame, with the same nodes and names and the same picture pixel for pixel
(measured 2026-10-18), readable by every build. `--compress` adds meshopt
(25 MB, 12.6 ms; positions quantized, the picture within a pixel), which only
builds from v0.2.1-beta.10 can read (`assets/cad_viewer/lib/meshopt_decoder.js`):
use it once every pit runs one, since the model syncs to all of them.

Needs Node; the pinned packages install into `tools/cad_optimize/` on first
run. Then import the result in the app (Control → the pit-front panel's CAD
settings → Robot Model), which syncs it to every pit like any CAD upload.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.console import use_utf8  # noqa: E402

use_utf8()

HERE = Path(__file__).resolve().parent / "cad_optimize"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--compress"]
    compress = "--compress" in sys.argv
    if not args:
        print(__doc__.strip())
        return 1
    src = Path(args[0]).resolve()
    out = Path(args[1]).resolve() if len(args) > 1 else \
        src.with_name(src.stem + ".optimized.glb")
    if not src.is_file():
        print(f"No such file: {src}")
        return 1
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        print("Needs Node.js (node and npm on PATH).")
        return 1
    if not (HERE / "node_modules").is_dir():
        print("Installing the pinned glTF tools (once)…")
        subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--silent"], cwd=HERE, check=True)
    # Onshape names a JSON glTF `.glb`; the reader picks its parser by extension.
    tmp = None
    with open(src, "rb") as f:
        binary = f.read(4) == b"glTF"
    if src.suffix.lower() == ".glb" and not binary:
        tmp = out.with_name(out.stem + ".source.gltf")
        shutil.copyfile(src, tmp)
    try:
        r = subprocess.run([node, str(HERE / "optimize.mjs"), str(tmp or src), str(out)]
                           + (["--compress"] if compress else []),
                           cwd=HERE, capture_output=True, text=True)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        print(r.stderr.strip() or r.stdout.strip())
        return 1
    report = json.loads(r.stdout.strip().splitlines()[-1])
    mb = lambda p: p.stat().st_size / 1e6
    print(f"{src.name}: {mb(src):,.0f} MB → {out.name}: {mb(out):,.0f} MB")
    print(f"draw calls {report['draws'][0]:,} → {report['draws'][1]:,}; "
          f"triangles {report['triangles'][1]:,} (unchanged: "
          f"{report['triangles'][0] == report['triangles'][1]}); "
          f"nodes and names kept: {report['names_kept']}")
    if not report["names_kept"] or report["triangles"][0] != report["triangles"][1]:
        print("The model's structure changed; don't use this file.")
        return 1
    print("Import it in the app: Control → CAD settings → Robot Model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
