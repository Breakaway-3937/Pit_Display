# secrets/

One file per credential, no extension, value on the first line. Everything here
except this README is gitignored. Read only through `app/credentials.py`; a
`PIT_SECRET_<NAME>` environment variable overrides the file.

| File | What it is | Where it comes from |
|---|---|---|
| `nexus_relay_token` | Bearer token for the relay at `nexus.bh-stack.com`. **The one a pit machine needs** | The relay's `CLIENT_TOKEN`. The copy of record is this file on the dev Mac; lost means regenerate ([relay README](../nexus-relay/README.md#changing-it)) |
| `nexus_api_key` | `Nexus-Api-Key`, for polling frc.nexus directly if the relay is unreachable. Optional | [frc.nexus/api](https://frc.nexus/api) |
| `sync_token` | Bearer token for the sync hub at `sync.bh-stack.com`. Every pit machine carries it (setup files include it); without it a machine doesn't sync | The hub's `PIT_TOKEN`. Copy of record: this file on the dev Mac ([hub README](../sync-hub/README.md#set-up-once)) |
| `sync_home_token` | The hub's `HOME_TOKEN`, which may overrule any conflict. **The app never reads it**; it's here only to be carried to the home machine, then deleted from this Mac | Generated with `PIT_TOKEN`; lost means `wrangler secret put HOME_TOKEN` with a new one |

On a pit machine this folder is in the data directory
(`%LOCALAPPDATA%\Breakaway Pit Display\secrets\`), so updates never touch it.
The Event Feed panel writes the same files. To move them without retyping, use
a setup file ([`DEPLOYMENT.md`](../DEPLOYMENT.md#b--install-on-a-new-pit-machine)).

Not here: the Nexus webhook token (a Worker secret on the relay) and the
GitHub `update_token` (predates this folder; see `app/update/release.py`).
