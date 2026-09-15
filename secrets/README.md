# secrets/

One file per credential, no extension, the value on the first line. Everything
in this folder except this README is ignored by git. Read through
`app/credentials.py` — never open these files from anywhere else.

| File | What it is | Where to get it |
|---|---|---|
| `nexus_api_key` | The `Nexus-Api-Key` header the app sends to `https://frc.nexus/api/v1` | [frc.nexus/api](https://frc.nexus/api), signed in as the team |
| `nexus_webhook_token` | The `Nexus-Token` Nexus sends **to us** when it calls our webhook; the app refuses any push that does not carry it | Same page, after registering a webhook URL |

Either can be supplied as an environment variable instead —
`PIT_SECRET_NEXUS_API_KEY`, `PIT_SECRET_NEXUS_WEBHOOK_TOKEN` — which wins over
the file. That is how a script or a CI job gets a key without writing it down.

On an installed pit machine this folder is not in the app's install directory;
it is beside the database in the per-user data directory
(`%LOCALAPPDATA%\Breakaway Pit Display\secrets\` on Windows), so an upgrade
never loses it. The Event Feed panel on the control screen writes the same
files when an admin pastes a key there.

**To get these onto a pit machine**, don't retype them: with the files
filled in here, `uv run python tools/pit_setup.py make ~/Desktop/pit-setup.json
--event 2026casf` writes one file carrying both keys and the event, and the
pit machine takes it as `pit-setup.json` in its data directory, from Control →
Event Feed → Import setup file…, or with `--provision`. See `NEXUS.md`.

The GitHub update token is the one credential *not* here — see
`app/update/release.py` for why it stays as `update_token`.
