"""Parsing and display formatting for the bot's reminder command grammar."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = os.getenv("SNORSE_TIMEZONE", "America/New_York")
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
TIMEZONE_ALIASES = {
    "ET": "America/New_York",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "CT": "America/Chicago",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "MT": "America/Denver",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "PT": "America/Los_Angeles",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "UTC": "UTC",
}


class CommandError(ValueError):
    """A short, user-correctable command error."""


def parse_clock(value: str) -> tuple[time, str]:
    match = re.fullmatch(
        r"\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s+([A-Za-z_/-]+))?\s*",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        raise CommandError("i couldn't read the time! try **7pm** or **7:30pm et**.")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if (
        minute > 59
        or (meridiem and not 1 <= hour <= 12)
        or (not meridiem and not 0 <= hour <= 23)
    ):
        raise CommandError("that time isn't valid! try **7pm** or **19:00**.")
    if meridiem:
        hour = hour % 12 + (12 if meridiem == "pm" else 0)

    requested_zone = match.group(4)
    zone_name = DEFAULT_TIMEZONE
    if requested_zone:
        zone_name = TIMEZONE_ALIASES.get(requested_zone.upper(), requested_zone)
    try:
        ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise CommandError(
            "i don't know that timezone! try et, ct, mt, pt, or utc."
        ) from exc
    return time(hour, minute), zone_name


def parse_date(value: str) -> date:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    raise CommandError("i couldn't read the date! use **yyyy-mm-dd**.")


def clean_reminder_text(value: str) -> str:
    text = value.strip()
    pairs = {'"': '"', "'": "'", "<": ">"}
    if len(text) >= 2 and text[0] in pairs and text[-1] == pairs[text[0]]:
        return text[1:-1].strip()
    return text


def parse_reminder(
    command: str, *, now: datetime | None = None
) -> tuple[str, datetime, str | None, str]:
    """Parse the intentionally small, predictable reminder grammar."""
    now = now or datetime.now(timezone.utc)
    normalized = re.sub(
        r"^(?:remind(?:er)?)(?:\s+me)?(?:\s+to)?\s+",
        "",
        command.strip(),
        flags=re.IGNORECASE,
    )
    if normalized == command.strip():
        raise CommandError("start with **remind**, then say what and when!")

    daily = re.fullmatch(
        r"(.+?)\s+(?:every\s+day|daily)"
        r"(?:\s+(?:morning|afternoon|evening|night))?\s+at\s+(.+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if daily:
        text = clean_reminder_text(daily.group(1))
        clock, zone_name = parse_clock(daily.group(2))
        zone = ZoneInfo(zone_name)
        local_now = now.astimezone(zone)
        target = datetime.combine(local_now.date(), clock, zone)
        if target <= local_now:
            target += timedelta(days=1)
        return text, target.astimezone(timezone.utc), "daily", zone_name

    recurring = re.fullmatch(
        r"(.+?)\s+every\s+"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"(?:\s+(?:morning|afternoon|evening|night))?\s+at\s+(.+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if recurring:
        text = clean_reminder_text(recurring.group(1))
        weekday_name = recurring.group(2).lower()
        clock, zone_name = parse_clock(recurring.group(3))
        zone = ZoneInfo(zone_name)
        local_now = now.astimezone(zone)
        days_ahead = (WEEKDAYS[weekday_name] - local_now.weekday()) % 7
        target = datetime.combine(
            local_now.date() + timedelta(days=days_ahead), clock, zone
        )
        if target <= local_now:
            target += timedelta(days=7)
        return text, target.astimezone(timezone.utc), weekday_name, zone_name

    relative = re.fullmatch(
        r"(.+?)\s+(today|tomorrow)\s+at\s+(.+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if relative:
        text = clean_reminder_text(relative.group(1))
        clock, zone_name = parse_clock(relative.group(3))
        zone = ZoneInfo(zone_name)
        local_now = now.astimezone(zone)
        target_date = local_now.date() + timedelta(
            days=1 if relative.group(2).lower() == "tomorrow" else 0
        )
        target = datetime.combine(target_date, clock, zone)
        if target <= local_now:
            raise CommandError("that time has already passed!")
        return text, target.astimezone(timezone.utc), None, zone_name

    dated = re.fullmatch(
        r"(.+?)\s+on\s+(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})" r"\s+at\s+(.+)",
        normalized,
        flags=re.IGNORECASE,
    )
    if dated:
        text = clean_reminder_text(dated.group(1))
        target_date = parse_date(dated.group(2))
        clock, zone_name = parse_clock(dated.group(3))
        target = datetime.combine(target_date, clock, ZoneInfo(zone_name))
        if target <= now.astimezone(ZoneInfo(zone_name)):
            raise CommandError("that date and time have already passed!")
        return text, target.astimezone(timezone.utc), None, zone_name

    raise CommandError(
        "i couldn't read when! try **tomorrow at 7pm**, "
        "**on 2026-07-30 at 7pm**, or **every monday at 7pm**."
    )


def format_clock(instant: datetime, zone_name: str) -> str:
    local = instant.astimezone(ZoneInfo(zone_name))
    clock = f"{local:%-I%p}" if local.minute == 0 else f"{local:%-I:%M%p}"
    if zone_name != DEFAULT_TIMEZONE:
        clock += f" {local.tzname() or zone_name}"
    return clock.lower()


def format_local(instant: datetime, zone_name: str) -> str:
    local = instant.astimezone(ZoneInfo(zone_name))
    return f"{local:%a %b %-d at} {format_clock(instant, zone_name)}".lower()


def format_recurrence(
    recurrence: dict[str, Any], instant: datetime, zone_name: str
) -> str:
    frequency = (
        "every day"
        if recurrence["type"] == "daily"
        else f"every {list(WEEKDAYS)[recurrence['weekday']]}"
    )
    return f"{frequency} at {format_clock(instant, zone_name)}"


def parse_iso_instant(value: str) -> datetime:
    """Parse ISO-8601 timestamps on Python versions that don't accept `Z`."""
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    instant = datetime.fromisoformat(normalized)
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant
