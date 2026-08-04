# snorse-bot

A self-hosted Signal bot for shared and private reminders, recurring schedules,
and read-only Google Calendar digests.

People interact with the bot inside Signal by mentioning it or starting a
message with 🐴. Each Signal group has isolated reminders and calendars.
Private reminders belong to their sender, and DM listings expose group data
only while that sender is a current member of the group.

## Project status

The bot logic is hosting-provider neutral and runs anywhere Python and
`signal-cli-rest-api` can run: a laptop, home server, VM, or container platform.
Hosting, backups, monitoring, and durable state are intentionally controlled by
the operator.

The bot currently uses a small authenticated HTTP
[state-service contract](docs/state-api.md) for reminders, scopes, scheduling,
and calendar access. The production implementation is AWS-specific and lives
outside this application repository. A SQLite reference implementation is
planned. Until it lands, new operators must provide a compatible state service
or adapt that boundary.

The project is free and open-source under the [MIT License](LICENSE).

## Features

- one-time and recurring reminders with natural compact syntax;
- personal DM reminders and isolated per-group reminders;
- list, edit, rename, and cancel using short scope-local IDs;
- real Signal mentions and a leading 🐴 command prefix;
- reactions for mutation success/failure;
- automatic group discovery and a welcome/help message;
- optional read-only Google Calendar listings and 24-hour notifications;
- scheduled calendar digests;
- low-latency Signal receive WebSocket with a polling fallback.

Ordinary DMs are ignored. The bot has no default or “main” group and never
guesses where private content should go.

## Commands

```text
🐴 help
🐴 remind take out trash every monday at 7pm
🐴 remind submit the form tomorrow at 7:30pm
🐴 remind renew the permit on 2026-08-15 at 9am ET
🐴 reminders
🐴 edit 123 text take out recycling
🐴 edit 123 time 8pm
🐴 edit 123 date 2026-08-15
🐴 edit 123 id recycling-night
🐴 cancel recycling-night
🐴 calendar
🐴 calendar next 3 weeks
🐴 calendar this month
🐴 calendar link help
🐴 calendar next 7 days -> remind weekly digest every monday at 9am
```

Times default to `America/New_York`; ET, CT, MT, PT, and UTC are accepted.
Custom IDs use lowercase letters, numbers, and hyphens. Each scope supports up
to 50 active reminders.

## Run with containers

Requirements:

- Docker with Compose;
- a Signal-capable phone number for the one-time registration;
- a state service implementing [`docs/state-api.md`](docs/state-api.md).

```sh
cp .env.example .env
# Fill in the bot number, state API URL, and a long random state API token.
docker compose up -d --build
```

The Signal API is exposed only on `127.0.0.1:8080`. Its registered identity is
stored in the named `signal-state` volume. Never delete that volume during an
upgrade and never run two active copies of one Signal identity.

Signal registration is interactive and depends on the selected
`signal-cli-rest-api` version. Follow its upstream registration documentation,
then confirm the account appears at:

```sh
curl --silent http://127.0.0.1:8080/v1/accounts
```

The core runtime environment variables are:

| Variable | Purpose |
|---|---|
| `BOT_PHONE_NUMBER` | Registered Signal number in international format |
| `BOT_DISPLAY_NAME` | User-facing bot/mention name |
| `SIGNAL_BASE_API_URL` | Local Signal REST API |
| `STATE_API_URL` | Durable state-service base URL |
| `STATE_API_TOKEN` | Shared machine token for that service |
| `BOT_SIGNAL_UUID` | Optional stable Signal identity address |
| `BOT_MENTION_NAMES` | Optional comma-separated mention aliases |
| `GOOGLE_SERVICE_ACCOUNT_EMAIL` | Optional address shown by calendar-link help |

Legacy `SNORSE_*` names remain accepted so existing deployments can upgrade
without downtime.

## Architecture

```text
Signal clients
     │ encrypted Signal transport
     ▼
signal-cli-rest-api ── localhost ── Python bot
                                        │ authenticated JSON
                                        ▼
                                 operator state service
```

- `main.py` is the stable executable.
- `app/bot.py` coordinates commands and scheduling.
- `app/reminder_parsing.py` parses reminder syntax.
- `app/signal_messages.py` handles mentions, UTF-16 offsets, and envelopes.
- `app/signal_transport.py` implements the local receive WebSocket.
- `app/signal_scopes.py` enforces personal/group visibility.
- `app/presentation.py` contains user-facing help.
- `app/settings.py` loads provider-neutral runtime configuration.

Signal provides end-to-end encryption between Signal participants and the
bot's Signal endpoint. The bot's own host processes plaintext and must be
trusted by its users. This project does not claim that an operator's complete
backend is end-to-end encrypted.

## Development

```sh
make test
make fmt
docker build .
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and
[AGENTS.md](AGENTS.md). Use synthetic data in tests and reports—never publish
real messages, phone numbers, group/calendar identifiers, credentials, or
Signal identity files.

## Hosting

This repository deliberately does not prescribe AWS, GCP, or any other
provider. A host must provide:

- persistent Signal identity storage;
- the state-service contract and its durable database;
- one continuously running bot process;
- outbound HTTPS access to Signal and any enabled calendar provider;
- backups, upgrades, monitoring, and secret management appropriate to the
  operator's risk tolerance.

Provider-specific deployment examples can live in separate repositories without
becoming dependencies of this application.
## Turnkey self-hosting

Copy `.env.example` to `.env`, configure a separate Signal-capable number and a long random `STATE_API_TOKEN`, register it with the included Signal API, then run `docker compose up -d`. The bundled SQLite state service needs no cloud credentials; `state-data` and `signal-state` are durable volumes and both must be backed up before upgrades. Never run two bot copies with the same Signal identity.

Google Calendar is optional. Reminders and group scopes continue to work without credentials; calendar commands report that the integration is not configured.

Private self-hosts default to effectively unlimited pools. Set `MAX_ACTIVE_GROUPS`, `MAX_PERSONAL_USERS`, and `ADMISSION_MODE=auto` to opt into the shared-host gate.
