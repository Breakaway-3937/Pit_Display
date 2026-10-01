"""
Robot-log analysis by a local model: analyst → validator → designer → publisher.

The same pipeline runs on a pit machine (over this database) and at home (over
the home MCP server); only the two ends differ:

* a **toolbox** (`pipeline.Toolbox`): the nine read-only tools. `local.py`
  serves them from this SQLite; home wraps its MCP client. Same names, same
  arguments, same result keys, every result echoing its `args`.
* a **sink** (`pipeline.Sink`): where runs are logged and boards go. `local.py`
  writes `analysis_run` and `analysis_board` here; home writes `analysis.run`
  and `sync.push_queue`.

**Models choose and word; code checks every figure** (`checks.py`). A number
that isn't in a tool result rejects the run, and the rejection is recorded:
never "fixed". Nothing here imports Qt, so home can import it from a checkout
the way it imports `app.db.sync.bundle`.

    tools.py     the nine tools over SQLite
    ollama.py    a small client for Ollama's /api/chat (standard library only)
    schema.py    the contracts in home/contracts/, and a validator for them
    checks.py    "every number is real", for findings and for boards
    pipeline.py  the run itself
    local.py     the pit's toolbox and sink
"""
