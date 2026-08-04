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
| `GET` | `/api/calendar/due` | Atomically claim calendar notifications due now |

The application client for this contract lives in `app/bot.py`. Provider
adapters belong in separate infrastructure projects. A small SQLite reference
implementation is planned; until then, self-hosters must provide this service
or adapt that client boundary.

Implementations must:

- partition every reminder and calendar by its explicit Signal scope;
- enforce uniqueness and the 50-active-reminder limit atomically per scope;
- use optimistic versions for edits/deletes;
- claim due work atomically so restarts or multiple workers do not duplicate it;
- never infer a default group;
- return only timed Google Calendar events, not all-day events;
- authenticate every request and avoid logging content-bearing payloads.
