# Kick-off prompt for the home session

Paste this into a Claude Code session on the home machine, in an empty folder
for the home project, with the pit repo checked out beside it (or give the
path). Fill in the two bracketed lines there, not here.

---

I'm building the home side of my FRC team's pit data system. The pit display
repo is at [path to Pit_Display checkout]. Start by reading, in order:
`home/HANDOFF.md`, `sync-hub/README.md`, `app/db/sync/bundle.py` (docstring),
`home/schema.sql`, and the two schemas in `home/contracts/`.

The SQL Server is [how to reach it: instance name / connection method]. Keep
every credential in environment variables or a local secrets file for this
project, never in the pit repo.

Work through HANDOFF.md's milestones M1 → M6 in order. At each one: tell me
your plan in a few lines, build it, run its check, and show me the result
before moving on. Adapt `schema.sql` to the real server, but keep its shape.
Ask me for the hub's HOME_TOKEN when you reach M1. For M6, list the Ollama
models I have installed (`ollama list`) and test which ones actually do tool
calling before choosing one.
