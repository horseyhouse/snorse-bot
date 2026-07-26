# Future opt-in group messaging

## Status

Not implemented. For now, an ordinary direct message to snorse-bot is ignored.
The bot has no main group and never guesses where a private message should go.

## Idea

Eventually, someone could use a DM to send a message to a Signal group they are
not a member of, similar to posting to a moderated email list. A group would
explicitly opt in and share an invitation identifier or link. Possessing that
invitation would grant only the permissions configured by the group, not access
to the group's name, membership, reminders, calendars, or message history.

Possible group-configurable controls:

- choose whether submissions are disabled, moderated, or delivered directly;
- issue separate random, revocable invitation tokens for different audiences;
- set expiration dates, per-sender and per-token rate limits, and quiet hours;
- allow text only initially, with attachments and links disabled by default;
- show submitters a group-chosen public label rather than the private group name;
- require a short confirmation before the first submission;
- let moderators block a sender or revoke a token without changing other links;
- optionally restrict a token to named Signal accounts after its first use.

## Safety and privacy requirements

- A token must be high-entropy and stored as a one-way hash, like a password.
- Looking up an invalid token must not reveal whether a group exists.
- A valid token reveals only the public label and submission rules chosen by the
  group.
- The bot must never expose membership, calendars, reminders, internal IDs, or
  prior messages to an external submitter.
- Delivery should clearly identify the message as an external submission.
- Rate limits, maximum message size, duplicate suppression, and an emergency
  group-wide off switch should exist before direct delivery is enabled.
- Moderation is the safer default: submissions enter a small pending queue and a
  group member approves or rejects them from Signal.
- Audit records should contain minimal metadata, expire automatically, and avoid
  retaining message contents after delivery or rejection.

## Possible command shape

Exact language is intentionally undecided, but a future flow could resemble:

```text
🐴 group inbox enable
🐴 group inbox invite create public-label "horse questions"
🐴 group inbox invite revoke 482
🐴 send <invite token> hello from outside the group
```

Before implementation, decide who may administer inbox settings, whether Signal
group admins can be identified reliably through the current API, how moderation
works entirely inside Signal, and whether invitation URLs should use a small web
landing page or be entered only in a DM.
