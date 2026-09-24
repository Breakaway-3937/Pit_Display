# secrets/

One file per credential, no extension, value on the first line. Everything here
except this README is gitignored. Read only through `app/credentials.py`; a
`PIT_SECRET_<NAME>` environment variable overrides the file.

| File | What it is | Where it comes from |
|---|---|---|
| `nexus_relay_token` | Bearer token for the relay at `nexus.bh-stack.com`. **The one a pit machine needs** | The relay's `CLIENT_TOKEN`. The copy of record is this file on the dev Mac; lost means regenerate ([relay README](../nexus-relay/README.md#changing-it)) |
| `nexus_api_key` | `Nexus-Api-Key`, for polling frc.nexus directly if the relay is unreachable. Optional | [frc.nexus/api](https://frc.nexus/api) |

On a pit machine this folder is in the data directory
(`%LOCALAPPDATA%\Breakaway Pit Display\secrets\`), so updates never touch it.
The Event Feed panel writes the same files. To move them without retyping, use
a setup file ([`DEPLOYMENT.md`](../DEPLOYMENT.md#b--install-on-a-new-pit-machine)).

Not here: the Nexus webhook token (a Worker secret on the relay) and the
GitHub `update_token` (predates this folder; see `app/update/release.py`).
