"""
`--mcp`: this machine's robot logs as an MCP server, over stdio.

    "Breakaway Pit Display.exe" --mcp           (an installed pit machine)
    uv run main.py --mcp                        (a checkout)

For Claude Desktop / Claude Code (or any MCP host) on a machine running the
pit app: the same nine read-only tools the analyst uses (`tools.py`, the home
MCP server's contract), served from **this machine's synced copy**, so it works
anywhere, offline, with no tunnel to the house (`home/REQUESTS.md` R4). Two
more tools review the analysis itself: `analysis_runs` (recent runs, their
findings and the crew's verdicts) and `analysis_scoreboard`.

**Read-only, and written by hand.** MCP over stdio is newline-delimited
JSON-RPC 2.0; the four methods a tools-only server needs (`initialize`,
`tools/list`, `tools/call`, `ping`) are below, so the bundle carries no SDK.
`tools/ai_check.py` proves it against the official `mcp` client. Nothing here
writes to the database, and stdout carries only protocol: diagnostics go to
stderr.

**The shipped app has no console** (`console=False`), so `sys.stdin` /
`sys.stdout` are None there. A host that launches it hands over pipes anyway;
`_stdio()` opens those OS handles directly. Without them it says so in
`mcp.log` beside the database and exits 2.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, BinaryIO

SERVER_NAME = "breakaway-pit"
# Versions this server speaks. Only the tools subset is implemented, which is
# unchanged across them; a client asking for another gets the newest.
PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")

INSTRUCTIONS = (
    "Read-only access to Team 3937's robot logs on this pit machine, and to the "
    "analysis runs the local model made of them with the crew's verdicts. Start with "
    "list_sessions, then session_overview for a session_uid. Every figure you report "
    "must come from a tool result; results echo their arguments as `args`.")

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
              "openWorldHint": False}

_EXTRA = [
    {"name": "analysis_runs",
     "description": "Recent analysis runs (any machine's, synced): model, status, why a "
                    "rejected run was rejected, its findings, and the crew's verdict on "
                    "each finding (useful / not_useful / wrong, acted) and the run (rank 1-5).",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}},
                      "required": []}},
    {"name": "analysis_scoreboard",
     "description": "Each model and prompt version, judged by the crew: share of rated "
                    "findings called useful or wrong, how many were acted on, mean rank, "
                    "and how often a run passed the checks.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
]


def _call_extra(name: str, args: dict) -> dict:
    from app.ai import feedback
    if name == "analysis_runs":
        limit = max(1, min(int(args.get("limit") or 10), 50))
        runs = feedback.recent_runs(limit)
        for r in runs:
            r["verdicts"] = feedback.verdicts(r["id"])
        return {"args": {"limit": limit}, "runs": runs}
    return {"args": {}, "scoreboard": feedback.scoreboard()}


def _tools() -> list[dict]:
    from app.ai import tools
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": t["input_schema"], "annotations": _READ_ONLY}
            for t in tools.SPECS + _EXTRA]


def _call(name: str, args: dict) -> dict:
    from app.ai import tools
    if name in {t["name"] for t in _EXTRA}:
        try:
            return _call_extra(name, args)
        except (TypeError, ValueError) as e:
            return {"args": args, "error": f"bad arguments: {e}"}
    return tools.call(name, args)


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, its response out (None for a notification)."""
    mid, method = msg.get("id"), msg.get("method")
    params = msg.get("params") or {}
    if mid is None:
        return None                     # notifications: initialized, cancelled, …

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        from app import version
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[-1],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "version": version.describe()},
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tools()})
    if method == "tools/call":
        name = params.get("name", "")
        result = _call(name, params.get("arguments") or {})
        return ok({"content": [{"type": "text",
                                "text": json.dumps(result, ensure_ascii=False, default=str)}],
                   "structuredContent": result,
                   "isError": "error" in result})
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def _stdio() -> tuple[BinaryIO, BinaryIO] | None:
    """The pipes the host gave us, even in a console-less build."""
    if sys.stdin is not None and sys.stdout is not None:
        return sys.stdin.buffer, sys.stdout.buffer
    if sys.platform != "win32":
        try:
            return os.fdopen(0, "rb", buffering=0), os.fdopen(1, "wb", buffering=0)
        except OSError:
            return None
    import ctypes
    import msvcrt
    k32 = ctypes.windll.kernel32
    handles = [k32.GetStdHandle(n) for n in (-10, -11)]      # STD_INPUT / STD_OUTPUT
    if any(h in (0, -1, None) for h in handles):
        return None
    try:
        fin = msvcrt.open_osfhandle(handles[0], os.O_RDONLY | os.O_BINARY)
        fout = msvcrt.open_osfhandle(handles[1], os.O_WRONLY | os.O_BINARY)
        return os.fdopen(fin, "rb", buffering=0), os.fdopen(fout, "wb", buffering=0)
    except OSError:
        return None


def _log(text: str) -> None:
    from app import paths
    try:
        with open(paths.data("mcp.log"), "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except OSError:
        pass
    if sys.stderr is not None:
        print(text, file=sys.stderr, flush=True)


def serve() -> int:
    pipes = _stdio()
    if pipes is None:
        _log("--mcp: no stdin/stdout from the host; start it from an MCP client "
             "(Claude Desktop, Claude Code), not by hand.")
        return 2
    fin, fout = pipes
    os.environ["PIT_AI_QUIET"] = "1"
    import app.db.migrations  # noqa: F401 — registers migrations before init_db()
    from app.db import init_db
    init_db()

    def send(obj: Any) -> None:
        fout.write(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
        fout.flush()

    for raw in iter(fin.readline, b""):
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": f"parse error: {e}"}})
            continue
        try:
            reply = handle(msg)
        except Exception as e:          # a tool bug is an answer, never a dead server
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        if reply is not None:
            send(reply)
    return 0
