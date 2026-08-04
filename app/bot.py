"""Receive Signal messages and run reminder and calendar commands."""

from __future__ import annotations

import json
import logging
import os
import re
import time as monotonic_time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.reminder_parsing import (
    CommandError,
    DEFAULT_TIMEZONE,
    clean_reminder_text,
    format_clock,
    format_local,
    format_recurrence,
    parse_clock,
    parse_date,
    parse_iso_instant,
    parse_reminder,
)
from app.signal_transport import SignalWebSocket, websocket_messages
from app.presentation import calendar_link_help, help_text
from app.settings import Settings
from app.signal_messages import (
    SignalMessageParser,
    canonical_group_id,
    command_after_horse_prefix,
    group_ids_match,
    group_recipient,
    message_data,
    remove_utf16_slice,
    signal_group,
    utf16_slice,
)
from app.signal_scopes import (
    member_identifiers,
    normalize_signal_identifier,
    personal_scope,
    signal_group_scopes,
    visible_groups_for_sender,
)
from app.admission import SUPPORT_MESSAGE, WAITLIST_MESSAGE, support_due

SETTINGS = Settings.from_env()
LOG = logging.getLogger(SETTINGS.app_name)
API_URL = SETTINGS.signal_api_url
PHONE_NUMBER = SETTINGS.phone_number
# Signal UUIDs are stable public identifiers, not authentication secrets.
BOT_SIGNAL_UUID = SETTINGS.signal_uuid
REMINDER_API_URL = SETTINGS.reminder_api_url
REMINDER_API_TOKEN = SETTINGS.reminder_api_token
BOT_MENTION_NAMES = SETTINGS.mention_names
TIMEOUT_SECONDS = SETTINGS.signal_api_timeout_seconds
SCHEDULE_INTERVAL_SECONDS = SETTINGS.schedule_interval_seconds
RECONNECT_MAX_SECONDS = SETTINGS.reconnect_max_seconds
CALENDAR_LEAD_MINUTES = 24 * 60
CALENDAR_LOOKBACK_MINUTES = 12
MAX_CALENDAR_RANGE_DAYS = 93
GROUP_SYNC_INTERVAL_SECONDS = 5 * 60
GOOGLE_CALENDAR_SERVICE_ACCOUNT = SETTINGS.calendar_service_account_email
PUBLIC_SITE_URL = SETTINGS.public_site_url
SUPPORT_URL = SETTINGS.support_url
SCOPE_CACHE: list[dict[str, Any]] = []
SCOPE_CACHE_AT = 0.0

HELP_TEXT = help_text()
CALENDAR_LINK_HELP = calendar_link_help(GOOGLE_CALENDAR_SERVICE_ACCOUNT)
REMINDER_ID_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,31})"
REACTION_SUCCESS = "✅"
REACTION_WARNING = "⚠️"
REACTION_ERROR = "❌"
KNOWN_SIGNAL_CONTACTS: set[str] = set()
MESSAGE_PARSER = SignalMessageParser(
    PHONE_NUMBER, BOT_SIGNAL_UUID, BOT_MENTION_NAMES, LOG
)


def api_json(
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    method: str | None = None,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("GET" if payload is None else "POST"),
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status == 204:
            return None
        return json.load(response)


def send_to_recipient(
    recipient: str,
    text: str,
    *,
    attachments: list[str] | None = None,
    styled: bool = False,
) -> None:
    api_json(
        "/v2/send",
        payload={
            "number": PHONE_NUMBER,
            "recipients": [recipient],
            "message": text,
            "base64_attachments": attachments or [],
            "text_mode": "styled" if styled else "normal",
        },
    )


def send_to_group(
    text: str,
    *,
    recipient: str,
    attachments: list[str] | None = None,
    styled: bool = False,
) -> None:
    send_to_recipient(recipient, text, attachments=attachments, styled=styled)


def react_to_message(message: dict[str, Any], reaction: str) -> None:
    """React to the original Signal message in its DM or group conversation."""
    envelope = message.get("envelope") or {}
    data_message, message_kind = message_data(envelope)
    target_author = (
        envelope.get("sourceNumber")
        or envelope.get("source")
        or envelope.get("sourceUuid")
    )
    if not target_author and message_kind == "sync-sent":
        target_author = PHONE_NUMBER
    timestamp = data_message.get("timestamp") or envelope.get("timestamp")
    group = signal_group(data_message)
    recipient = group["groupRecipient"] if group else target_author
    if not target_author or not recipient or not isinstance(timestamp, int):
        raise ValueError("Signal message is missing reaction target metadata")
    api_json(
        f"/v1/reactions/{urllib.parse.quote(PHONE_NUMBER, safe='+')}",
        payload={
            "reaction": reaction,
            "recipient": recipient,
            "target_author": target_author,
            "timestamp": timestamp,
        },
    )


def ensure_signal_contact(recipient: str, name: str | None) -> None:
    """Add an explicit DM command sender to the bot's local Signal contacts."""
    if recipient in KNOWN_SIGNAL_CONTACTS:
        return
    contact_name = (name or "Signal contact").strip()[:100] or "Signal contact"
    api_json(
        f"/v1/contacts/{urllib.parse.quote(PHONE_NUMBER, safe='+')}",
        method="PUT",
        payload={"recipient": recipient, "name": contact_name},
    )
    KNOWN_SIGNAL_CONTACTS.add(recipient)


def command_scope(scope: dict[str, Any]) -> dict[str, str]:
    return {
        "groupRecipient": scope["groupRecipient"],
        "groupName": scope["groupName"],
        "scopeType": scope["scopeType"],
    }


def groups_for_sender(envelope: dict[str, Any]) -> list[dict[str, str]]:
    """List only bot groups whose current member roster contains the DM sender."""
    groups = api_json(f"/v1/groups/{urllib.parse.quote(PHONE_NUMBER, safe='+')}") or []
    visible_groups = visible_groups_for_sender(envelope, groups)
    configured = {
        scope["groupRecipient"]: scope
        for scope in configured_scopes()
        if scope.get("scopeType") == "group" and scope.get("active", True)
    }
    return [
        command_scope(configured[group["groupRecipient"]])
        for group in visible_groups
        if group["groupRecipient"] in configured
    ]


def is_bot_mention(text: str, mention: dict[str, Any]) -> bool:
    return MESSAGE_PARSER.is_bot_mention(text, mention)


def commands_after_bot_mentions(data_message: dict[str, Any]) -> list[str]:
    return MESSAGE_PARSER.commands_after_mentions(data_message)


def command_after_bot_mention(data_message: dict[str, Any]) -> str | None:
    commands = commands_after_bot_mentions(data_message)
    return commands[0] if commands else None


def message_is_from_bot(envelope: dict[str, Any]) -> bool:
    return MESSAGE_PARSER.message_is_from_bot(envelope)


def reminder_api(
    path: str, *, method: str = "GET", payload: dict[str, Any] | None = None
) -> Any:
    if not REMINDER_API_URL or not REMINDER_API_TOKEN:
        raise RuntimeError("Reminder API is not configured")
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{REMINDER_API_URL}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Snorse-Token": REMINDER_API_TOKEN,
            "X-Bot-Token": REMINDER_API_TOKEN,
            "Authorization": f"Bearer {REMINDER_API_TOKEN}",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status == 204:
                return None
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error")
        except Exception:
            detail = None
        raise CommandError(detail or "the reminder service had a problem!") from exc


def configured_scopes(*, force: bool = False) -> list[dict[str, Any]]:
    global SCOPE_CACHE, SCOPE_CACHE_AT
    now = monotonic_time.monotonic()
    if force or not SCOPE_CACHE or now - SCOPE_CACHE_AT >= GROUP_SYNC_INTERVAL_SECONDS:
        SCOPE_CACHE = reminder_api("/api/scopes")["scopes"]
        SCOPE_CACHE_AT = now
    return SCOPE_CACHE


def trust_group_member_identities(groups: list[Any]) -> None:
    """Trust changed Signal identities only for members of the bot's groups."""
    group_member_ids = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        for member in group.get("members") or []:
            group_member_ids.update(member_identifiers(member))
    if not group_member_ids:
        return

    account = urllib.parse.quote(PHONE_NUMBER, safe="+")
    try:
        identities = api_json(f"/v1/identities/{account}") or []
    except Exception:
        LOG.exception("Could not list Signal identities for group-member trust")
        return

    for identity in identities:
        if (
            not isinstance(identity, dict)
            or str(identity.get("status")).casefold() != "untrusted"
        ):
            continue
        identity_ids = {
            normalize_signal_identifier(identity.get(field))
            for field in ("uuid", "number")
        }
        identity_ids.discard(None)
        if not group_member_ids.intersection(identity_ids):
            continue
        recipient = identity.get("uuid") or identity.get("number")
        if not isinstance(recipient, str) or not recipient:
            continue
        try:
            api_json(
                f"/v1/identities/{account}/trust/"
                f"{urllib.parse.quote(recipient, safe='+')}",
                method="PUT",
                payload={"trust_all_known_keys": True},
            )
            LOG.info("Trusted a changed Signal identity for a current group member")
        except Exception:
            LOG.exception(
                "Could not trust a changed Signal identity for a current group member"
            )


def sync_signal_groups() -> list[dict[str, Any]]:
    """Reconcile the bot's current Signal groups into durable state scopes."""
    groups = api_json(f"/v1/groups/{urllib.parse.quote(PHONE_NUMBER, safe='+')}") or []
    trust_group_member_identities(groups)
    scopes = signal_group_scopes(groups)
    result = reminder_api(
        "/api/scopes/sync",
        method="POST",
        payload={"groups": scopes},
    )
    configured_scopes(force=True)
    for group in result.get("newGroups", []):
        try:
            message = (
                WAITLIST_MESSAGE.format(site=PUBLIC_SITE_URL)
                if group.get("admissionStatus") == "waitlisted"
                else f"hi!\n\n{HELP_TEXT}"
            )
            send_to_recipient(
                group["groupRecipient"],
                message,
                styled=True,
            )
            LOG.info("Welcomed a new Signal group")
        except Exception:
            LOG.exception("Could not welcome a new Signal group")
    for group in result.get("newlyActivated", []):
        try:
            send_to_recipient(group["groupRecipient"], f"hi!\n\n{HELP_TEXT}", styled=True)
        except Exception:
            LOG.exception("Could not welcome an activated Signal group")
    LOG.info("Synchronized %d Signal group scope(s)", len(scopes))
    return result["scopes"]


def link_calendar(command: str, *, scope: dict[str, str]) -> str:
    normalized = re.sub(r"\s+", " ", command.strip())
    match = re.fullmatch(
        r"(?:calendar\s+link|link\s+calendar)(?:\s+(.*))?",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match or not match.group(1) or match.group(1).casefold() == "help":
        return CALENDAR_LINK_HELP
    calendar_id = match.group(1).strip()
    result = reminder_api(
        "/api/calendars/link",
        method="POST",
        payload={
            **scope,
            "calendarId": calendar_id,
        },
    )
    configured_scopes(force=True)
    if result.get("alreadyLinked"):
        return f"✅ **{result['calendarName']}** is already linked!"
    return f"✅ linked **{result['calendarName']}**!"


def create_reminder(
    command: str,
    *,
    created_by: str | None,
    group: dict[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    if group is None:
        raise ValueError("a reminder scope is required")
    text, next_run, recurrence, zone_name, action = parse_reminder_command(
        command, now=now
    )
    if not text:
        raise CommandError("tell me what to remind the group about!")
    if len(text) > 500:
        raise CommandError("keep reminder text under 500 characters!")
    recurrence_data = None
    if recurrence:
        local = next_run.astimezone(ZoneInfo(zone_name))
        recurrence_data = {"type": recurrence, "localTime": local.strftime("%H:%M")}
        if recurrence != "daily":
            recurrence_data = {
                "type": "weekly",
                "weekday": local.weekday(),
                "localTime": local.strftime("%H:%M"),
            }
    result = reminder_api(
        "/api/reminders",
        method="POST",
        payload={
            "text": text,
            "nextRunUtc": next_run.isoformat(),
            "timezone": zone_name,
            "recurrence": recurrence_data,
            "action": action,
            "createdBy": created_by,
            "groupRecipient": group["groupRecipient"],
            "groupName": group["groupName"],
            "scopeType": group.get("scopeType", "group"),
        },
    )
    reminder_id = result["id"]
    schedule = (
        format_recurrence(recurrence_data, next_run, zone_name)
        if recurrence_data
        else format_local(next_run, zone_name)
    )
    return f"✅ {reminder_id}: {text} ({schedule})"


def parse_reminder_command(
    command: str, *, now: datetime | None = None
) -> tuple[str, datetime, str | None, str, dict[str, str] | None]:
    """Parse a normal reminder or an allowlisted scheduled tool action."""
    parts = re.split(r"\s*(?:->|→)\s*", command.strip(), maxsplit=1)
    if len(parts) == 1:
        text, next_run, recurrence, zone_name = parse_reminder(command, now=now)
        return text, next_run, recurrence, zone_name, None

    action_command, reminder_command = parts
    if not reminder_command:
        raise CommandError("say when to **remind** after **->**!")
    normalized_action = re.sub(
        r"\s+", " ", action_command.strip().casefold().rstrip("!?.")
    )
    if not normalized_action.startswith("calendar"):
        raise CommandError("only **calendar** can be scheduled right now!")
    calendar_window(normalized_action, now=now)

    schedule = re.sub(
        r"^remind(?:er)?(?:\s+me)?(?:\s+to)?\s+",
        "",
        reminder_command.strip(),
        flags=re.IGNORECASE,
    )
    if schedule == reminder_command.strip():
        raise CommandError("put **remind** after **->**!")
    text, next_run, recurrence, zone_name = parse_reminder(
        reminder_command,
        now=now,
    )
    return (
        text,
        next_run,
        recurrence,
        zone_name,
        {"type": "command", "command": normalized_action},
    )


def reminder_list_path(group: dict[str, str] | None = None) -> str:
    if not group:
        return "/api/reminders"
    query = urllib.parse.urlencode({"groupRecipient": group["groupRecipient"]})
    return f"/api/reminders?{query}"


def format_reminder_row(row: dict[str, Any]) -> str:
    instant = parse_iso_instant(row["nextRunUtc"])
    recurrence = row.get("recurrence")
    schedule = (
        format_recurrence(recurrence, instant, row["timezone"])
        if recurrence
        else format_local(instant, row["timezone"])
    )
    text = row["text"]
    action = row.get("action")
    if action and action.get("type") == "command":
        action_command = action["command"]
        # Early source-first reminders accidentally prefixed the action to the
        # label. Clean that legacy display without mutating stored data.
        if text.casefold().startswith(f"{action_command.casefold()} "):
            text = text[len(action_command) :].strip()
        text = f"{text} → {action_command}"
    return f"{row['id']} {text} ({schedule})"


def list_reminders(
    group: dict[str, str] | None = None,
    *,
    scopes: list[dict[str, str]] | None = None,
) -> str:
    if scopes is None:
        rows = reminder_api(reminder_list_path(group))["reminders"]
    else:
        rows = []
        for scope in scopes:
            rows.extend(reminder_api(reminder_list_path(scope))["reminders"])
    if not rows:
        return "🐴 no reminders set!"
    if group:
        return "\n".join(["🐴 reminders:", *(format_reminder_row(row) for row in rows)])

    lines = ["🐴 reminders:"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        recipient = row.get("groupRecipient")
        if not recipient:
            LOG.error("Skipping unscoped reminder %s in reminder listing", row["id"])
            continue
        grouped.setdefault(recipient, []).append(row)
    scope_names = {
        scope["groupRecipient"]: scope["groupName"] for scope in scopes or []
    }
    named_groups = [
        (
            scope_names.get(recipient)
            or max(
                group_rows,
                key=lambda row: row.get("updatedAt") or row.get("createdAt") or "",
            ).get("groupName")
            or "signal group",
            group_rows,
        )
        for recipient, group_rows in grouped.items()
    ]
    for group_name, group_rows in sorted(
        named_groups,
        key=lambda item: (item[0].casefold() != "personal", item[0].casefold()),
    ):
        lines.append(f"\n**{group_name}**")
        lines.extend(format_reminder_row(row) for row in group_rows)
    return "\n".join(lines)


def calendar_window(
    command: str, *, now: datetime | None = None
) -> tuple[datetime, datetime, str]:
    """Parse a calendar-list command into an exclusive UTC time window."""
    now = now or datetime.now(timezone.utc)
    normalized = command.strip().casefold()
    if re.fullmatch(r"calendar[!?.]*", normalized):
        return now, now + timedelta(days=7), "next 7 days"
    normalized = re.sub(r"^calendar\s+", "", normalized)

    match = re.fullmatch(r"next\s+(\d+)\s+(day|days|week|weeks)[!?.]*", normalized)
    if match:
        count = int(match.group(1))
        unit = match.group(2)
        days = count * (7 if unit.startswith("week") else 1)
        if count < 1:
            raise CommandError("the calendar range must be at least 1 day!")
        if days > MAX_CALENDAR_RANGE_DAYS:
            raise CommandError("keep calendar searches to 93 days or less!")
        label_unit = "week" if unit.startswith("week") else "day"
        if count != 1:
            label_unit += "s"
        return now, now + timedelta(days=days), f"next {count} {label_unit}"

    if re.fullmatch(r"this\s+month[!?.]*", normalized):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
        local_now = now.astimezone(zone)
        if local_now.month == 12:
            next_month = datetime(local_now.year + 1, 1, 1, tzinfo=zone)
        else:
            next_month = datetime(local_now.year, local_now.month + 1, 1, tzinfo=zone)
        return now, next_month.astimezone(timezone.utc), "rest of this month"

    raise CommandError(
        "use **calendar**, **next 10 days**, **next 3 weeks**, or **this month**!"
    )


def format_calendar_date(instant: datetime) -> str:
    return f"{instant.strftime('%a, %b').lower()} {instant.day}"


def list_calendar_events(
    command: str,
    *,
    group: dict[str, str] | None,
    groups: list[dict[str, str]] | None = None,
    now: datetime | None = None,
) -> str:
    start, end, label = calendar_window(command, now=now)
    parameters = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
    }
    if group:
        parameters["groupRecipient"] = group["groupRecipient"]
        query = urllib.parse.urlencode(parameters)
        events = reminder_api(f"/api/calendar/events?{query}")["events"]
    elif groups is not None:
        events = []
        for visible_group in groups:
            query = urllib.parse.urlencode(
                {
                    **parameters,
                    "groupRecipient": visible_group["groupRecipient"],
                }
            )
            events.extend(reminder_api(f"/api/calendar/events?{query}")["events"])
        events.sort(key=lambda event: event["start"])
    else:
        query = urllib.parse.urlencode(parameters)
        events = reminder_api(f"/api/calendar/events?{query}")["events"]
    if not events:
        return f"🐴 no events in the {label}!"

    zone = ZoneInfo(DEFAULT_TIMEZONE)
    lines = ["🐴 **calendar**", f"*{label}*"]
    if group:
        append_calendar_event_rows(lines, events, zone)
        return "\n".join(lines)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        recipient = event.get("groupRecipient")
        if not recipient:
            LOG.error("Skipping unscoped calendar event in calendar listing")
            continue
        grouped.setdefault(recipient, []).append(event)
    named_groups = [
        (
            next(
                (
                    event.get("groupName")
                    for event in group_events
                    if event.get("groupName")
                ),
                "signal group",
            ),
            group_events,
        )
        for group_events in grouped.values()
    ]
    for group_name, group_events in sorted(
        named_groups, key=lambda item: item[0].casefold()
    ):
        lines.extend(["", f"**{group_name}**"])
        append_calendar_event_rows(lines, group_events, zone)
    return "\n".join(lines)


def append_calendar_event_rows(
    lines: list[str], events: list[dict[str, Any]], zone: ZoneInfo
) -> None:
    current_date = None
    for event in events:
        event_start = parse_iso_instant(event["start"]).astimezone(zone)
        if event_start.date() != current_date:
            current_date = event_start.date()
            lines.extend(["", f"**{format_calendar_date(event_start)}**"])
        lines.append(
            f"{event.get('summary') or 'untitled event'} "
            f"({format_clock(event_start, DEFAULT_TIMEZONE)})"
        )


def cancel_reminder(command: str, *, group: dict[str, str] | None = None) -> str:
    if group is None:
        raise ValueError("a reminder scope is required")
    match = re.fullmatch(
        rf"cancel\s+#?({REMINDER_ID_PATTERN})\s*", command, flags=re.IGNORECASE
    )
    if not match:
        raise CommandError("use **cancel <id>**!")
    reminder_id = match.group(1).lower()
    reminders = reminder_api(reminder_list_path(group))["reminders"]
    reminder = next((item for item in reminders if item["id"] == reminder_id), None)
    if not reminder:
        raise CommandError(f"i couldn't find reminder {reminder_id}!")
    query = urllib.parse.urlencode(
        {
            "version": reminder["version"],
            "groupRecipient": group["groupRecipient"],
        }
    )
    reminder_api(f"/api/reminders/{reminder_id}?{query}", method="DELETE")
    return f"🗑️ cancelled reminder {reminder_id}!"


def edit_reminder(
    command: str,
    *,
    group: dict[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    if group is None:
        raise ValueError("a reminder scope is required")
    match = re.fullmatch(
        rf"edit\s+#?({REMINDER_ID_PATTERN})\s+(id|text|time|date)\s+(.+)",
        command.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        raise CommandError("use **edit <id> text|time|date|id <new value>**!")
    reminder_id, field, value = (
        match.group(1).lower(),
        match.group(2).lower(),
        match.group(3).strip(),
    )
    reminders = reminder_api(reminder_list_path(group))["reminders"]
    reminder = next((item for item in reminders if item["id"] == reminder_id), None)
    if not reminder:
        raise CommandError(f"i couldn't find reminder {reminder_id}!")

    payload: dict[str, Any] = {
        "version": reminder["version"],
        "groupRecipient": group["groupRecipient"],
    }
    if field == "id":
        payload["id"] = value.lower()
    elif field == "text":
        text = clean_reminder_text(value)
        if not text or len(text) > 500:
            raise CommandError("reminder text must be 1–500 characters!")
        payload["text"] = text
    else:
        now = now or datetime.now(timezone.utc)
        zone_name = reminder["timezone"]
        zone = ZoneInfo(zone_name)
        scheduled = parse_iso_instant(reminder["nextRunUtc"]).astimezone(zone)
        if field == "time":
            clock, zone_name = parse_clock(value)
            zone = ZoneInfo(zone_name)
            target = datetime.combine(scheduled.date(), clock, zone)
        else:
            target = datetime.combine(parse_date(value), scheduled.timetz(), zone)
        recurrence = reminder.get("recurrence")
        if target <= now.astimezone(zone):
            if not recurrence:
                raise CommandError("that date and time have already passed!")
            step = 1 if recurrence["type"] == "daily" else 7
            while target <= now.astimezone(zone):
                target += timedelta(days=step)
        payload["nextRunUtc"] = target.astimezone(timezone.utc).isoformat()
        payload["timezone"] = zone_name
        if recurrence:
            payload["recurrence"] = {
                **recurrence,
                "localTime": target.strftime("%H:%M"),
            }

    result = reminder_api(
        f"/api/reminders/{reminder_id}", method="PUT", payload=payload
    )
    return f"✅ {result['id']} updated!"


def run_command(
    command: str,
    *,
    created_by: str | None,
    group: dict[str, str] | None = None,
    dm_scope: dict[str, str] | None = None,
    visible_groups: list[dict[str, str]] | None = None,
) -> str:
    command = command.strip()
    reminder_scope = group or dm_scope
    if not command:
        return HELP_TEXT
    if re.fullmatch(r"help[!?.]*", command, flags=re.IGNORECASE):
        return HELP_TEXT
    if re.match(
        r"^(?:calendar\s+link|link\s+calendar)\b",
        command,
        flags=re.IGNORECASE,
    ):
        if not reminder_scope:
            raise CommandError("i couldn't determine where to link that calendar!")
        return link_calendar(command, scope=reminder_scope)
    if command.casefold() in {"reminders", "list"}:
        if group:
            return list_reminders(group)
        if dm_scope:
            return list_reminders(scopes=[dm_scope, *(visible_groups or [])])
        return list_reminders()
    if re.match(r"^calendar\b.*(?:->|→)\s*remind\b", command, re.IGNORECASE):
        if dm_scope:
            raise CommandError("scheduled **calendar** digests must be set in a group!")
        return create_reminder(command, created_by=created_by, group=group)
    if (
        re.fullmatch(r"calendar[!?.]*", command, flags=re.IGNORECASE)
        or re.fullmatch(
            r"(?:calendar\s+)?next\s+\d+\s+(?:day|days|week|weeks)[!?.]*",
            command,
            flags=re.IGNORECASE,
        )
        or re.fullmatch(
            r"(?:calendar\s+)?this\s+month[!?.]*",
            command,
            flags=re.IGNORECASE,
        )
    ):
        return list_calendar_events(
            command,
            group=group,
            groups=[dm_scope, *(visible_groups or [])] if dm_scope else None,
        )
    if command.casefold().startswith("cancel"):
        return cancel_reminder(command, group=reminder_scope)
    if command.casefold().startswith("edit"):
        return edit_reminder(command, group=reminder_scope)
    if re.match(r"^remind(?:er)?\b", command, flags=re.IGNORECASE):
        return create_reminder(command, created_by=created_by, group=reminder_scope)
    raise CommandError("unknown command! try **help**.")


def looks_like_command(text: str) -> bool:
    normalized = text.strip().casefold()
    return (
        re.fullmatch(r"help[!?.]*", normalized) is not None
        or re.match(r"^(?:calendar\s+link|link\s+calendar)\b", normalized) is not None
        or normalized in {"reminders", "list"}
        or re.match(r"^calendar\b.*(?:->|→)\s*remind\b", normalized) is not None
        or re.fullmatch(r"calendar[!?.]*", normalized) is not None
        or re.fullmatch(
            r"(?:calendar\s+)?next\s+\d+\s+(?:day|days|week|weeks)[!?.]*",
            normalized,
        )
        is not None
        or re.fullmatch(r"(?:calendar\s+)?this\s+month[!?.]*", normalized) is not None
        or normalized.startswith("cancel")
        or normalized.startswith("edit")
        or re.match(r"^remind(?:er)?\b", normalized) is not None
    )


def command_returns_content(command: str) -> bool:
    """Whether a successful command needs a text response, not just a reaction."""
    normalized = command.strip().casefold()
    return (
        re.fullmatch(r"help[!?.]*", normalized) is not None
        or re.fullmatch(
            r"(?:calendar\s+link|link\s+calendar)(?:\s+help)?[!?.]*",
            normalized,
        )
        is not None
        or normalized in {"reminders", "list"}
        or re.fullmatch(r"calendar[!?.]*", normalized) is not None
        or re.fullmatch(
            r"(?:calendar\s+)?next\s+\d+\s+(?:day|days|week|weeks)[!?.]*",
            normalized,
        )
        is not None
        or re.fullmatch(r"(?:calendar\s+)?this\s+month[!?.]*", normalized) is not None
    )


def run_commands(
    commands: list[str],
    *,
    created_by: str | None,
    group: dict[str, str] | None = None,
    dm_scope: dict[str, str] | None = None,
    visible_groups: list[dict[str, str]] | None = None,
) -> tuple[str, list[str]]:
    """Run all commands and aggregate their user-facing responses."""
    reaction = REACTION_SUCCESS
    responses = []
    for command in commands:
        try:
            command_options: dict[str, Any] = {
                "created_by": created_by,
                "group": group,
            }
            if dm_scope is not None:
                command_options["dm_scope"] = dm_scope
            if visible_groups is not None:
                command_options["visible_groups"] = visible_groups
            response = run_command(command, **command_options)
            if command_returns_content(command):
                responses.append(response)
        except CommandError as exc:
            if reaction != REACTION_ERROR:
                reaction = REACTION_WARNING
            responses.append(f"⚠️ {exc}")
        except Exception:
            reaction = REACTION_ERROR
            responses.append("❌ something went wrong processing that command!")
            LOG.exception("Could not run Signal command")
    return reaction, responses


def acknowledge_commands(
    message: dict[str, Any],
    reaction: str,
    responses: list[str],
    *,
    recipient: str,
) -> None:
    """React once, then send any requested content or aggregated errors."""
    try:
        react_to_message(message, reaction)
    except Exception:
        LOG.exception("Could not react to Signal command")
    if responses:
        send_to_recipient(recipient, "\n\n".join(responses), styled=True)


def handle_direct_command(message: dict[str, Any]) -> bool:
    """Run an explicitly prefixed DM command."""
    envelope = message.get("envelope") or {}
    data_message = envelope.get("dataMessage") or {}
    text = data_message.get("message")
    sender = (
        envelope.get("sourceNumber")
        or envelope.get("source")
        or envelope.get("sourceUuid")
    )
    if (
        not isinstance(text, str)
        or not sender
        or data_message.get("groupInfo") is not None
    ):
        return False
    commands = commands_after_bot_mentions(data_message)
    if not commands:
        horse_command = command_after_horse_prefix(data_message)
        if horse_command:
            commands = [horse_command]
    if not commands:
        mention_names = "|".join(re.escape(name) for name in BOT_MENTION_NAMES)
        typed_mention = re.fullmatch(
            rf"\s*@(?:{mention_names})\s*[\s,:;-]*(.*?)\s*",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if typed_mention:
            commands = [typed_mention.group(1) or "help"]
    if not commands:
        return False
    try:
        ensure_signal_contact(sender, envelope.get("sourceName"))
    except Exception:
        LOG.exception("Could not add explicit DM command sender to Signal contacts")
    private_scope = personal_scope(
        envelope.get("sourceUuid") or envelope.get("sourceNumber") or sender
    )
    # Group members use their group's access and never consume personal slots.
    if REMINDER_API_URL and REMINDER_API_TOKEN:
        try:
            if not groups_for_sender(envelope):
                admission = reminder_api(
                    "/api/admission/personal", method="POST", payload=private_scope
                )["scope"]
                if admission.get("admissionStatus") != "active":
                    acknowledge_commands(message, REACTION_WARNING, [f"this personal spot is waitlisted for now. status: {PUBLIC_SITE_URL}/capacity."], recipient=sender)
                    return True
        except Exception:
            LOG.exception("Could not determine personal admission")
            acknowledge_commands(message, REACTION_ERROR, ["❌ i couldn't check capacity! try again shortly."], recipient=sender)
            return True
    visible_groups = None
    if any(
        command.strip().casefold() in {"reminders", "list"}
        or (
            re.match(
                r"^(?:calendar\b|next\s+\d+\s+|this\s+month)",
                command.strip(),
                re.I,
            )
            and not re.match(
                r"^(?:calendar\s+link|link\s+calendar)\b",
                command.strip(),
                re.I,
            )
        )
        for command in commands
    ):
        try:
            visible_groups = groups_for_sender(envelope)
        except Exception:
            LOG.exception("Could not verify DM sender's Signal group memberships")
            acknowledge_commands(
                message,
                REACTION_ERROR,
                ["❌ i couldn't verify your group memberships! try again shortly."],
                recipient=sender,
            )
            return True
    reaction, responses = run_commands(
        commands,
        created_by=private_scope["groupRecipient"],
        dm_scope=private_scope,
        visible_groups=visible_groups,
    )
    acknowledge_commands(message, reaction, responses, recipient=sender)
    return True


def handle_group_command(message: dict[str, Any]) -> bool:
    envelope = message.get("envelope") or {}
    data_message, _ = message_data(envelope)
    group = signal_group(data_message)
    if not group:
        return False
    configured_group = next(
        (
            scope
            for scope in configured_scopes()
            if scope["groupRecipient"] == group["groupRecipient"]
            and scope.get("active", True)
            and scope.get("admissionStatus", "active") == "active"
        ),
        None,
    )
    if not configured_group:
        sync_signal_groups()
        configured_group = next(
            (
                scope
                for scope in configured_scopes()
                if scope["groupRecipient"] == group["groupRecipient"]
                and scope.get("active", True)
                and scope.get("admissionStatus", "active") == "active"
            ),
            None,
        )
        if not configured_group:
            waitlisted = next((scope for scope in configured_scopes() if scope["groupRecipient"] == group["groupRecipient"] and scope.get("admissionStatus") == "waitlisted"), None)
            if waitlisted:
                status = reminder_api("/api/admission/waitlist-status", method="POST", payload={"groupRecipient":group["groupRecipient"]})
                if status.get("notify"):
                    send_to_recipient(group["groupRecipient"], f"commands are waitlisted here for now. status: {PUBLIC_SITE_URL}/capacity.")
                return True
            LOG.info(
                "Group command ignored: recipient %s could not be configured",
                group["groupRecipient"],
            )
            return False
    group = {**group, **command_scope(configured_group)}
    if message_is_from_bot(envelope):
        LOG.info("Group command ignored: message was authored by the bot")
        return False
    LOG.info("Accepted a command in a configured Signal group")
    commands = commands_after_bot_mentions(data_message)
    if not commands:
        horse_command = command_after_horse_prefix(data_message)
        if horse_command:
            commands = [horse_command]
            LOG.info("Accepted Signal horse-prefix command")
    if not commands:
        return False
    reaction, responses = run_commands(
        commands,
        created_by=(
            envelope.get("sourceUuid")
            or envelope.get("sourceNumber")
            or envelope.get("source")
        ),
        group=group,
    )
    acknowledge_commands(
        message, reaction, responses, recipient=group["groupRecipient"]
    )
    return True


def fire_due_reminders(*, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = reminder_api("/api/reminders")["reminders"]
    active_groups = {
        scope["groupRecipient"]
        for scope in configured_scopes()
        if scope.get("scopeType") == "group" and scope.get("active", True)
    }
    fired = 0
    for row in rows:
        scheduled = parse_iso_instant(row["nextRunUtc"])
        if scheduled > now:
            continue
        recipient = row.get("groupRecipient")
        if not recipient:
            LOG.error("Skipping unscoped due reminder %s", row["id"])
            continue
        if recipient.startswith("group.") and recipient not in active_groups:
            LOG.info(
                "Paused reminder %s because its Signal group is inactive",
                row["id"],
            )
            continue
        action = row.get("action")
        if action and action.get("type") == "command":
            if not recipient.startswith("group."):
                LOG.error(
                    "Skipping unsupported private scheduled command reminder %s",
                    row["id"],
                )
                continue
            group = {
                "groupRecipient": recipient,
                "groupName": row.get("groupName") or "signal group",
            }
            output = list_calendar_events(action["command"], group=group, now=now)
            send_to_group(output, recipient=recipient, styled=True)
        else:
            send_to_group(f"⏰ reminder: {row['text']}", recipient=recipient)
        if row.get("recurrence"):
            recurrence = row["recurrence"]
            zone = ZoneInfo(row["timezone"])
            local_now = now.astimezone(zone)
            hour, minute = map(int, recurrence["localTime"].split(":"))
            days = (
                1
                if recurrence["type"] == "daily"
                else (recurrence["weekday"] - local_now.weekday()) % 7
            )
            next_local = datetime.combine(
                local_now.date() + timedelta(days=days), time(hour, minute), zone
            )
            if next_local <= local_now:
                next_local += timedelta(days=1 if recurrence["type"] == "daily" else 7)
            reminder_api(
                f"/api/reminders/{row['id']}",
                method="PUT",
                payload={
                    "version": row["version"],
                    "nextRunUtc": next_local.astimezone(timezone.utc).isoformat(),
                    "groupRecipient": recipient,
                },
            )
        else:
            query = urllib.parse.urlencode(
                {
                    "version": row["version"],
                    "groupRecipient": recipient,
                }
            )
            reminder_api(
                f"/api/reminders/{row['id']}?{query}",
                method="DELETE",
            )
        fired += 1
    return fired


def fire_calendar_events(*, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    query = urllib.parse.urlencode(
        {
            "now": now.isoformat(),
            "leadMinutes": CALENDAR_LEAD_MINUTES,
            "lookbackMinutes": CALENDAR_LOOKBACK_MINUTES,
        }
    )
    events = reminder_api(f"/api/calendar/due?{query}")["events"]
    for event in events:
        start = parse_iso_instant(event["start"]).astimezone(ZoneInfo(DEFAULT_TIMEZONE))
        send_to_group(
            f"📅 tomorrow: {event['summary']} "
            f"({format_clock(start, DEFAULT_TIMEZONE)})",
            recipient=event["groupRecipient"],
        )
    return len(events)


def fire_support_notes(*, now: datetime | None = None) -> int:
    """Send optional support copy only to eligible active groups."""
    now = now or datetime.now(timezone.utc)
    if not SUPPORT_URL:
        return 0
    sent = 0
    for scope in configured_scopes():
        if not support_due(scope, now, SUPPORT_URL):
            continue
        send_to_group(SUPPORT_MESSAGE.format(support=SUPPORT_URL), recipient=scope["groupRecipient"])
        reminder_api("/api/scopes/support-sent", method="POST", payload={"groupRecipient":scope["groupRecipient"],"sentAt":now.isoformat()})
        sent += 1
    if sent:
        configured_scopes(force=True)
    return sent


def process_messages(messages: list[dict[str, Any]]) -> tuple[int, int]:
    """Process Signal envelopes and return (ignored direct messages, commands)."""
    ignored_direct_messages = 0
    commands = 0
    for message in messages:
        try:
            envelope = message.get("envelope") or {}
            data_message, message_kind = message_data(envelope)
            LOG.info(
                "Signal envelope diagnostics: kind=%s has_group=%s",
                message_kind,
                data_message.get("groupInfo") is not None,
            )
            if data_message.get("groupInfo") is not None:
                commands += int(handle_group_command(message))
            elif message_kind == "received" and handle_direct_command(message):
                commands += 1
            elif message_kind == "received":
                ignored_direct_messages += 1
                LOG.info("Ignored a direct message without an explicit bot command")
        except Exception:
            LOG.exception("Could not process an incoming message")
    return ignored_direct_messages, commands


def run_scheduled_tasks() -> tuple[int, int]:
    """Fire due reminders and calendar notifications."""
    try:
        reminders = fire_due_reminders()
    except Exception:
        LOG.exception("Could not process due reminders")
        reminders = 0
    try:
        calendar_events = fire_calendar_events()
    except Exception:
        LOG.exception("Could not process calendar events")
        calendar_events = 0
    try:
        fire_support_notes()
    except Exception:
        LOG.exception("Could not process support notes")
    if reminders or calendar_events:
        LOG.info(
            "Scheduled work: reminders %d; calendar %d",
            reminders,
            calendar_events,
        )
    return reminders, calendar_events


def listen_forever() -> int:
    """Continuously consume Signal's push stream and run scheduled work."""
    websocket_url = (
        API_URL.replace("http://", "ws://", 1)
        + f"/v1/receive/{urllib.parse.quote(PHONE_NUMBER, safe='+')}"
    )
    next_scheduled = monotonic_time.monotonic()
    next_group_sync = monotonic_time.monotonic()
    reconnect_delay = 1
    while True:
        try:
            with SignalWebSocket(websocket_url) as stream:
                LOG.info("Connected to Signal push stream")
                reconnect_delay = 1
                while True:
                    now = monotonic_time.monotonic()
                    if now >= next_group_sync:
                        try:
                            sync_signal_groups()
                        except Exception:
                            LOG.exception("Could not synchronize Signal groups")
                        next_group_sync = now + GROUP_SYNC_INTERVAL_SECONDS
                    if now >= next_scheduled:
                        run_scheduled_tasks()
                        next_scheduled = now + SCHEDULE_INTERVAL_SECONDS
                    payload = stream.receive_text()
                    if payload is None:
                        continue
                    messages = websocket_messages(payload)
                    ignored_direct_messages, commands = process_messages(messages)
                    LOG.info(
                        "Push event: envelopes %d; ignored direct messages %d; commands %d",
                        len(messages),
                        ignored_direct_messages,
                        commands,
                    )
        except (ConnectionError, OSError, ValueError, json.JSONDecodeError):
            LOG.exception(
                "Signal push stream failed; reconnecting in %d second(s)",
                reconnect_delay,
            )
            monotonic_time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, RECONNECT_MAX_SECONDS)


def poll_once() -> int:
    """Compatibility/fallback one-shot receiver."""
    messages = api_json(f"/v1/receive/{PHONE_NUMBER}") or []
    ignored_direct_messages, commands = process_messages(messages)
    reminders, calendar_events = run_scheduled_tasks()
    LOG.info(
        "Received %d envelope(s); ignored direct messages %d; commands %d; reminders %d; calendar %d",
        len(messages),
        ignored_direct_messages,
        commands,
        reminders,
        calendar_events,
    )
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return listen_forever() if "--listen" in os.sys.argv[1:] else poll_once()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, TimeoutError):
        LOG.exception("Signal API request failed")
        raise SystemExit(1)
