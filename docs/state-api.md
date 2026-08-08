# State service contract

The bot is hosting-provider neutral. Durable state and optional Google Calendar
access are supplied by an HTTP service configured through `STATE_API_URL` and
`STATE_API_TOKEN`.

Requests are JSON and include all of these equivalent authentication headers
for compatibility:

```text
Authorization: Bearer <token>
X-Bot-Token: <token>
X-Snorse-Token: <token>
```

An implementation must provide:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/scopes` | List active personal/group scopes and calendar links |
| `POST` | `/api/scopes/sync` | Reconcile the bot's current Signal groups |
| `POST` | `/api/admission/personal` | Admit or waitlist an explicit prefixed DM sender |
| `POST` | `/api/capacity/report` | Record an authenticated one-minute host health report |
| `GET` | `/api/public/capacity` | Return aggregate capacity and plan status only |
| `POST` | `/api/calendars/link` | Verify and link a calendar to a scope |
| `GET` | `/api/reminders` | List reminders, optionally filtered by `groupRecipient` |
| `POST` | `/api/reminders` | Atomically create a scoped reminder and unique short ID |
| `PUT` | `/api/reminders/{id}` | Versioned update within an explicit scope |
| `DELETE` | `/api/reminders/{id}` | Versioned deletion within an explicit scope |
| `GET` | `/api/calendar/events` | List timed events for a scope and time window |
| `POST` | `/api/calendar/claims` | Atomically claim one calendar notification |

The application client for this contract lives in `app/bot.py`. This repository
also includes the bundled SQLite implementation in `app/sqlite_state.py`, which
is the default state service used by `compose.yaml`. Self-hosters using that
Compose setup do not need to implement an API. Provider adapters belong in
separate infrastructure projects only when an operator wants to replace the
SQLite service with DynamoDB, Postgres, or another durable backend.

Implementations must:

- partition every reminder and calendar by its explicit Signal scope;
- enforce uniqueness and the 50-active-reminder limit atomically per scope;
- use optimistic versions for edits/deletes;
- claim due work atomically so restarts or multiple workers do not duplicate it;
- never infer a default group;
- return only timed Google Calendar events, not all-day events;
- authenticate every request and avoid logging content-bearing payloads.

Calendar eligibility (including the 24-hour window, lookback, timezone handling,
and notification text) belongs to the bot. The state service fetches the
requested timed events and atomically claims a stable event identity so a
restart or concurrent worker cannot send it twice.
