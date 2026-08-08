# snorse-bot command reference

This is the long-form command guide. It follows the parser in
`app/reminder_parsing.py` and the short `help` response. Commands are
case-insensitive and extra punctuation such as `!` is generally harmless.

## How to invoke a command

In a group, either mention the bot or put a command after a leading `🐴` or
`🐌`:

```text
🐴 remind take out the recycling tomorrow at 7pm
🐌 reminders
@snorse-bot calendar next 3 days
```

In a direct message, use an explicit prefix (`🐴`, `🐌`, or the configured bot
mention). Ordinary unprefixed DMs are ignored. Group members use their group
scope for DMs; unrelated DM users must be admitted to the separate personal
pool first.

## Reminders

Create a one-time reminder with:

```text
remind <what> tomorrow at <time>
remind <what> today at <time>
remind <what> on <YYYY-MM-DD> at <time>
remind <what> on <MM/DD/YYYY> at <time>
```

Examples:

```text
🐴 remind submit the form tomorrow at 7:30pm
🐴 remind call the dentist on 2026-09-14 at 9am
🐴 remind water the plants on 08/20/2026 at 18:00
```

Create a recurring reminder with:

```text
remind <what> every day at <time>
remind <what> every <weekday> at <time>
```

Examples:

```text
🐴 remind take medication every day at 8am
🐴 remind put the bins out every monday at 7pm
```

Times may be written as `7pm`, `7:30pm`, or 24-hour `19:30`. Times default to
the configured timezone (`America/New_York` by default). Add `ET`, `CT`, `MT`,
`PT`, or `UTC`, or an IANA timezone name:

```text
🐴 remind standup every monday at 9am PT
🐴 remind deploy on 2026-09-14 at 19:30 UTC
```

Reminder text may be quoted when it contains punctuation:

```text
🐴 remind "send the 'final' file" tomorrow at 4pm
```

Each group or personal scope can have up to 50 active reminders.

## List, edit, and cancel

List reminders in the current scope:

```text
🐴 reminders
```

Edit a reminder using its displayed ID:

```text
🐴 edit 527 text take out recycling
🐴 edit 527 time 8pm
🐴 edit 527 date 2026-09-15
🐴 edit 527 id recycling-night
```

Cancel by numeric or custom ID:

```text
🐴 cancel 527
🐴 cancel recycling-night
```

IDs are scope-local. The same ID can exist in another group without affecting
this scope.

## Calendar listings

Google Calendar is optional. When configured, the bot lists timed events (not
all-day events) in the requested window:

```text
🐴 calendar                 # next 7 days
🐴 calendar next 10 days
🐴 calendar next 3 weeks
🐴 calendar this month      # from now through month-end
```

Calendar searches are limited to 93 days. The bot also checks upcoming events
for a 24-hour reminder and claims each event occurrence once, so moving an
event before its reminder updates the time without creating a duplicate.

## Link a Google Calendar

The calendar owner must share the calendar with the service-account address
shown by `calendar link help`, granting **See all event details**:

```text
🐴 calendar link help
🐴 calendar link <calendar id>
```

The calendar ID is often the calendar’s email-like address, or the value shown
under Google Calendar’s **Integrate calendar** settings. Linking is read-only;
snorse-bot never writes or edits events.

## Scheduled calendar digests

You can schedule a recurring reminder whose action runs a calendar listing:

```text
🐴 calendar next 7 days -> remind weekly digest every monday at 9am
🐴 calendar next 3 weeks -> remind planning digest every friday at 4pm
```

Only calendar listing actions are allowed for scheduled reminders. The normal
reminder schedule still controls when the digest is sent.

## Common errors

- **“start with remind”**: include `remind` (or `reminder`) before the text.
- **“i couldn't read when”**: include `tomorrow at …`, `on DATE at …`, or
  `every DAY at …`.
- **“that time has already passed”**: choose a future time in the selected
  timezone.
- **“calendar integration is not configured”**: the current state service does
  not have Google Calendar access; reminders still work.
