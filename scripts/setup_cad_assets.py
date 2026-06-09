"""
One-time setup: downloads Three.js r128 (legacy UMD builds) and GSAP 3
into assets/cad_viewer/lib/ so the CAD viewer works fully offline at
competition venues where internet is unavailable.

Run once from the project root:
    uv run scripts/setup_cad_assets.py
"""

import urllib.request
import sys
from pathlib import Path

LIB_DIR = Path(__file__).parent.parent / "assets" / "cad_viewer" / "lib"

# Three.js r128 legacy UMD builds (adds to window.THREE, no ES module imports)
# GLTFLoader and OrbitControls in the legacy examples/js/ folder
THREE_VER = "0.128.0"
GSAP_VER  = "3.12.4"

FILES = {
    "three.min.js": f"https://unpkg.com/three@{THREE_VER}/build/three.min.js",
    "GLTFLoader.js": f"https://unpkg.com/three@{THREE_VER}/examples/js/loaders/GLTFLoader.js",
    "OrbitControls.js": f"https://unpkg.com/three@{THREE_VER}/examples/js/controls/OrbitControls.js",
    "gsap.min.js": f"https://unpkg.com/gsap@{GSAP_VER}/dist/gsap.min.js",
}


def download(url: str, dest: Path) -> None:
    print(f"  Downloading {dest.name} …", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        size_kb = dest.stat().st_size // 1024
        print(f"OK ({size_kb} KB)")
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)


def main() -> None:
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Installing CAD viewer libraries into {LIB_DIR}\n")

    for filename, url in FILES.items():
        dest = LIB_DIR / filename
        if dest.exists():
            print(f"  {filename} — already present, skipping")
        else:
            download(url, dest)

    print("\nDone. Restart the app or click 'Reload' in the CAD control panel.")


if __name__ == "__main__":
    main()
