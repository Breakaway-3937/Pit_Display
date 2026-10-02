"""
Throwaway Cloudflare Workers on this machine, for checks and the validation app.

`wrangler dev --local` runs the real Worker code (the sync hub, the Nexus relay)
in workerd with local Durable Object storage, so a check talks to the same
code the pits talk to in production and never to production itself. One
launcher, shared by `sync_check.py`, `validate.py` and `validate.py --app`, so
the checks and the validation app can't drift apart.

Dev tokens only; nothing here reads `secrets/`.
"""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The sync hub's dev tokens (sync_check, the validation app).
PIT_TOKEN = "check-pit-token"
HOME_TOKEN = "check-home-token"
OLD_PIT_TOKEN = "check-old-pit-token"      # a token mid-rotation

# The relay's, as relay_check.py --local expects them.
RELAY_PORT = 8787
RELAY_VARS = {
    "CLIENT_TOKEN": "dev-client-token",
    "NEXUS_WEBHOOK_TOKEN": "dev-webhook-token",
    "NEXUS_API_KEY": "fake",
    "NEXUS_API": "http://127.0.0.1:8790/api/v1",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start_worker(folder: Path, port: int, variables: dict[str, str],
                 state: Path) -> tuple[subprocess.Popen, str]:
    """`wrangler dev` for the Worker in `folder` on `port`; waits for /healthz."""
    if not (folder / "node_modules").exists():
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=folder, check=True)
    args = ["npx", "wrangler", "dev", "--local", "--ip", "127.0.0.1", "--port", str(port),
            "--persist-to", str(state)]
    for k, v in variables.items():
        args += ["--var", f"{k}:{v}"]
    proc = subprocess.Popen(args, cwd=folder, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=2):
                return proc, url
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(1)
    proc.kill()
    raise SystemExit(f"wrangler dev for {folder.name} didn't come up on :{port}")


def start_hub(tmp: Path) -> tuple[subprocess.Popen, str]:
    """A private sync hub with the dev tokens, its state under `tmp`."""
    return start_worker(ROOT / "sync-hub", free_port(),
                        {"PIT_TOKEN": f"{PIT_TOKEN},{OLD_PIT_TOKEN}", "HOME_TOKEN": HOME_TOKEN},
                        tmp / "hub-state")


def start_relay(tmp: Path) -> tuple[subprocess.Popen, str]:
    """The Nexus relay on :8787 with the dev vars relay_check.py --local wants."""
    return start_worker(ROOT / "nexus-relay", RELAY_PORT, RELAY_VARS, tmp / "relay-state")


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
