# Kick-off prompts for the home session

Two prompts: one to **build** the home side, one for any later
**maintenance** session. Paste into Claude Code on the home machine, from the
home project's folder. Fill in the bracketed parts there, not here: this repo
never holds the server's details.

## Before the first session

1. On the home machine, clone the pit repo next to where the home project
   will live, and check out the branch that has `app/db/sync/`:
   ```bash
   git clone https://github.com/Breakaway-3937/Pit_Display.git
   cd Pit_Display && git checkout beta          # or main, once it's merged
   ```
2. Install Python 3.14+ and `uv`. For M6, Ollama with at least one model pulled.
3. Carry `secrets/sync_home_token` from the dev Mac to the home machine,
   however you move secrets. Once the home agent has it, delete it from the
   Mac. Have it ready when the session asks at M1.
4. Know how the session should reach SQL Server (instance, auth method, a
   login that can create schemas; later, a read-only login for M5).

## Build prompt

---

I'm building the home side of my FRC team's pit data system. The pit display
repo is checked out at [path to Pit_Display checkout] on the `beta`
branch. Start by reading, in order: `home/HANDOFF.md`, `home/OPERATIONS.md`,
`sync-hub/README.md`, the docstrings of `app/db/sync/bundle.py` and
`app/db/sync/columns.py`, `home/schema.sql`, and the two schemas in
`home/contracts/`. Then run `home/hub_probe.py` once I've given you the
home token, to see the live hub's current state.

Build the home project in [path for the home project], not inside the pit
repo. The SQL Server is [instance name / how to connect / auth method]. Keep
every credential in environment variables or a local secrets file of the
home project; never write one into the pit repo, and don't commit to the pit
repo at all unless I ask.

Work through HANDOFF.md's milestones M1 → M7 in order. At each one: tell me
your plan in a few lines, build it, run its check, and show me the result
before moving on. Adapt `schema.sql` to the real server but keep its shape.
Ask me for the hub's HOME_TOKEN at M1. Use one stable machine id for
everything that talks to the hub. Test the hub rebuild (M4) only against a
local `wrangler dev` hub, never the live one. For M6, list my Ollama models
(`ollama list`), test which ones actually do tool calling through the MCP
server, and show me the results before choosing.

---

## Maintenance prompt

---

You're maintaining the home side of my FRC team's pit data system. The home
project is at [path]; the pit repo checkout is at [path]. Read
`home/OPERATIONS.md` in the pit checkout first, then the home project's own
README/notes.

Start with a health check: run `home/hub_probe.py --cursor <home's current
cursor>` and look at the agent's recent log, `sync.blob` errors and rejected
`analysis.run` rows. Tell me what's healthy and what isn't before changing
anything. [Then: what I want done this time, e.g. "the pits updated, check
the checkout is in step" / "rotate the pit token" / "R2 is filling up".]

Follow OPERATIONS.md's procedures as written; if one doesn't fit what you
find, stop and tell me rather than improvising, especially anything that
force-pushes to the hub or deletes blobs.

---
