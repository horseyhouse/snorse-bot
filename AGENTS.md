# Agent guide

This is a hosting-provider-neutral Signal bot application. Keep application
behavior portable; cloud resources and production-account automation belong in
separate infrastructure repositories.

## Architecture

- `main.py` is the stable executable compatibility launcher.
- Application code belongs in focused modules under `app/`.
- Durable state is accessed only through the authenticated contract documented
  in `docs/state-api.md`.
- Do not add Terraform, cloud SDKs, provider deployment workflows, or
  account-specific monitoring to this repository.
- Keep legacy environment aliases when introducing provider-neutral names so
  deployed operators can migrate without downtime.

## Runtime invariants

- Signal state must be persisted across upgrades. Never delete, overwrite,
  publish, or initialize it during an ordinary application deployment.
- Never run two active processes from copies of one Signal identity.
- Keep the Signal REST API and receive WebSocket private to the bot host.
- Never create a default or “main” group. All commands, reminders, calendars,
  and replies have explicit personal or group scopes.
- Group data is isolated. DM aggregation may include only groups in which the
  sender is currently a member; verify live membership.
- Ignore ordinary DMs and messages sent by the bot itself.
- Calendar access is read-only; ignore all-day events.

## Privacy and security

- Never commit or log credentials, service-account JSON, API tokens, SSH keys,
  Signal state, phone numbers, UUIDs, group identifiers, message/reminder text,
  calendar contents, or attachments.
- Addresses may not be authentication secrets, but installation-specific
  identifiers still do not belong in reusable source or fixtures.
- Treat the operator host and state service as trusted plaintext-processing
  compute. Do not describe the whole backend as end-to-end encrypted.
- Authenticate state-service requests, avoid content-bearing logs, and preserve
  atomic scoping, uniqueness, versioning, and due-work claims.

## Command behavior

- Group commands require a real Signal mention or leading 🐴/🐌. DM commands
  require an explicit prefix.
- Preserve Signal UTF-16 mention offsets and structured mention metadata.
- Commands and errors should be concise, lowercase, and forgiving of harmless
  punctuation.
- Prefer one reaction for a mutation batch, plus one combined error response
  when needed.
- Keep presentation copy separate from parsing and storage behavior.
- Reminder IDs are short, scope-local, reusable, and atomically unique.
- When changing command parsing, help text, prefixes, or user-facing command
  behavior, update `README.md` and `docs/commands.md` in the same change. Keep
  examples executable against the current parser and add or update parser
  tests for new grammar.

## Development

Before committing:

```sh
make test
make fmt
docker build .
git diff --check
```

Add tests for parsing, scoping, permissions, identifier allocation, transport,
and configuration compatibility. Preserve unrelated user changes. Leave
pushing and production deployment to the operator unless explicitly authorized.
