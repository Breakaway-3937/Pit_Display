"""
Read-only health check of the sync hub, for the home machine. Exit 0/1/2.

    SYNC_HOME_TOKEN=… python hub_probe.py                 human-readable report
    SYNC_HOME_TOKEN=… python hub_probe.py --json          the raw /v1/status
    SYNC_HOME_TOKEN=… python hub_probe.py --cursor 214    also: how far behind home is

Standard library only (plus `truststore` if installed, for the OS's own
certificate check). Calls only `GET /healthz` and `GET /v1/status`, which
write nothing, so running it never registers a machine on the hub.

Exit 0 = healthy, 1 = warnings (see the ! lines), 2 = hub unreachable or
token refused. Warn thresholds are at the top; home/OPERATIONS.md explains
each one and what to do.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("SYNC_URL", "https://sync.bh-stack.com").rstrip("/")

R2_FREE_BYTES = 10 * 1000 ** 3        # R2 free tier: 10 GB-month stored
WARN_R2_FRACTION = 0.7
WARN_UNACKED = 20                      # blobs home hasn't archived
WARN_LAG = 500                         # changes home hasn't pulled
WARN_PIT_SILENT_DAYS = 14              # a pit not seen for this long


def _context() -> ssl.SSLContext:
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


def _get(path: str, token: str | None) -> dict:
    headers = {"User-Agent": "breakaway-hub-probe"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Pit-Machine"] = "home-probe"
    req = urllib.request.Request(URL + path, headers=headers)
    with urllib.request.urlopen(req, timeout=20, context=_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def _ago(ms: float | None) -> str:
    if not ms:
        return "never"
    s = max(0.0, time.time() - ms / 1000)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s / n:.1f}{unit} ago"
    return f"{s:.0f}s ago"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cursor", type=int, help="home's sync.cursor.seq, to report lag")
    args = ap.parse_args()
    token = os.environ.get("SYNC_HOME_TOKEN", "").strip()
    if not token:
        print("SYNC_HOME_TOKEN is not set.")
        return 2

    try:
        health = _get("/healthz", None)
        st = _get("/v1/status", token)
    except urllib.error.HTTPError as e:
        print(f"hub answered HTTP {e.code}" + (" (token refused)" if e.code == 401 else ""))
        return 2
    except Exception as e:
        print(f"hub unreachable: {e}")
        return 2
    if args.json:
        print(json.dumps(st, indent=2))
        return 0

    warn: list[str] = []
    print(f"hub        {URL}  (pit token set: {health.get('pitToken')}, home token set: {health.get('homeToken')})")
    print(f"epoch      {st['epoch']}")
    print(f"head       {st['head']}   rows {st['rows']}   tombstones {st['tombstones']}")
    if args.cursor is not None:
        lag = st["head"] - args.cursor
        print(f"home lag   {lag} change(s) behind")
        if lag > WARN_LAG:
            warn.append(f"home is {lag} changes behind: is the agent running?")
    used = st["blobBytes"]
    print(f"R2         {st['blobs']} blobs, {used / 1e6:.1f} MB "
          f"({used / R2_FREE_BYTES:.1%} of the 10 GB free tier), {st['blobsUnacked']} not yet archived by home")
    if used > R2_FREE_BYTES * WARN_R2_FRACTION:
        warn.append("R2 above 70% of the free tier: prune archived raw/bundle blobs (OPERATIONS.md)")
    if st["blobsUnacked"] > WARN_UNACKED:
        warn.append(f"{st['blobsUnacked']} blobs not archived: is the home agent's M3 loop running?")
    print("tables     " + ", ".join(f"{t['tbl']} {t['n']}" for t in st.get("tables", [])))
    print("machines")
    for m in st.get("machines", []):
        print(f"  {m.get('name') or m['id']:<24} {m['id']:<26} {m.get('role') or '':<5} "
              f"v{m.get('version') or '?':<10} seen {_ago(m.get('last_seen'))}, "
              f"pulled to {m.get('last_pull_seq')}, pushed {m.get('pushed')}, conflicts {m.get('conflicts')}")
        silent = (time.time() * 1000 - (m.get("last_seen") or 0)) / 86_400_000
        if m.get("role") == "pit" and silent > WARN_PIT_SILENT_DAYS:
            warn.append(f"{m.get('name')} not seen for {silent:.0f} days: retired? "
                        f"DELETE /v1/machine/{m['id']} takes it off every pit's list")
    for w in warn:
        print(f"! {w}")
    return 1 if warn else 0


if __name__ == "__main__":
    sys.exit(main())
