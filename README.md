# Breakaway Pit Display

The pit system for FRC Team 3937 Breakaway. It runs on one Windows machine:

- **Control screen**, for the operator: modes, screens, music, LEDs, the event feed, telemetry.
- **Two overhead screens**, for the audience: a slide rotation, or the Next Match, checklist and diagnostics boards. They can run over Ethernet on a Raspberry Pi.
- **The pit-front touch panel**: the robot's CAD model and the team's story.
- **Pit systems**: RGBW LED strips on an Arduino, a local music player with an equaliser, robot-log import, and live queuing from frc.nexus through the team's relay at `nexus.bh-stack.com`.

```bash
uv run main.py                      # the app; only the control screen opens
PIT_LEDS_FAKE=1 PIT_NEXUS_FAKE=1 uv run main.py   # no LED hardware, fake event feed
uv run main.py --self-check         # boot everything offscreen and report, exit 0/1
```

## Documentation

| Read this | When |
|---|---|
| [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md) | Using it: setup, media, CAD, logs, event feed, the pre-event checklist |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Shipping a release, installing a pit machine, updates, `--self-check` |
| [`NEXUS.md`](NEXUS.md) | The event feed: the relay, every Nexus field, the signals |
| [`nexus-relay/README.md`](nexus-relay/README.md) | Running the Cloudflare relay |
| [`DATABASE.md`](DATABASE.md) | Every table, query and invariant |
| [`CLAUDE.md`](CLAUDE.md) | Architecture and the rules the code depends on. Read before changing code |
| [`breakaway_branding.md`](breakaway_branding.md) | The brand system: colours, type, the red budget |

Smaller READMEs sit beside what they describe: `secrets/`, `packaging/`,
`tools/owlet/`.

## Checks

No unit tests; these exercise the real thing and exit 0/1.

| | |
|---|---|
| `uv run main.py --self-check` | the whole app, offscreen; the gate every update passes |
| `uv run tools/relay_check.py` | the Nexus relay end to end (`--local` against `wrangler dev`) |
| `uv run tools/webcast_check.py` | the pit-LAN screens |
| `uv run tools/upgrade_check.py` | a version change keeps every row a person typed |
| `uv run tools/led_diag.py` | the LED controller link on real hardware: round trip, queue wait, the controller's own timing and errors. `--flash-test` isolates flicker |
| `uv run tools/eq_check.py` | the equaliser's band display against real tones |
