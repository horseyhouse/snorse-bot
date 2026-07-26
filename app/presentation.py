"""User-facing help and setup messages."""

from __future__ import annotations


def help_text() -> str:
    return (
        "🐴 commands:\n"
        "• **remind** [thing] [**tomorrow** | **on** [date] | **every** [day] | "
        "**every day**] **at** [time]\n"
        "• **reminders**\n"
        "• **calendar** [**next** [number] [**days** | **weeks**] | "
        "**this month**] [**-> remind** [thing] [schedule]]\n"
        "• **calendar link** [**help** | calendar id]\n"
        "• **cancel** [id]\n"
        "• **edit** [id] [**text** | **time** | **date** | **id**] [new value]\n"
        "*dates: yyyy-mm-dd, times default to et*"
    )


def calendar_link_help(service_account_email: str) -> str:
    email = (
        f"here's my email: **{service_account_email}**\n"
        if service_account_email
        else "ask the bot operator for its Google service-account email\n"
    )
    return (
        "🐴 **link a Google calendar**\n"
        f"{email}"
        "1. add me to your calendar using *see all event details*\n"
        "2. send 🐴 **calendar link** [calendar id]\n"
        "*only Google Calendar is supported rn*"
    )
