"""Signal reminder scopes and privacy-safe group membership filtering."""

from __future__ import annotations

import base64
from typing import Any


def personal_scope(recipient: str) -> dict[str, str]:
    """Return the private reminder namespace and delivery target for a DM user."""
    return {
        "groupRecipient": recipient,
        "groupName": "personal",
        "scopeType": "personal",
    }


def normalize_signal_identifier(value: Any) -> str | None:
    """Normalize phone numbers and ACI/UUID forms used by Signal group metadata."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().casefold()
    if normalized.startswith("aci:"):
        normalized = normalized[4:]
    return normalized


def sender_identifiers(envelope: dict[str, Any]) -> set[str]:
    identifiers = {
        normalize_signal_identifier(envelope.get(field))
        for field in ("sourceNumber", "source", "sourceUuid")
    }
    return {identifier for identifier in identifiers if identifier}


def member_identifiers(member: Any) -> set[str]:
    if isinstance(member, str):
        identifier = normalize_signal_identifier(member)
        return {identifier} if identifier else set()
    if not isinstance(member, dict):
        return set()
    identifiers = {
        normalize_signal_identifier(member.get(field))
        for field in ("number", "uuid", "aci", "id")
    }
    return {identifier for identifier in identifiers if identifier}


def as_group_recipient(group_id: str) -> str:
    if group_id.startswith("group."):
        return group_id
    encoded = base64.b64encode(group_id.encode("utf-8")).decode("ascii")
    return f"group.{encoded}"


def signal_group_scope(group: Any) -> dict[str, str] | None:
    if not isinstance(group, dict):
        return None
    recipient_value = group.get("id") or group.get("groupId")
    internal_id = group.get("internal_id") or group.get("internalId")
    if isinstance(recipient_value, str) and recipient_value.startswith("group."):
        recipient = recipient_value
    elif isinstance(internal_id, str) and internal_id:
        recipient = as_group_recipient(internal_id)
    elif isinstance(recipient_value, str) and recipient_value:
        recipient = as_group_recipient(recipient_value)
    else:
        return None
    return {
        "groupRecipient": recipient,
        "groupName": group.get("name") or group.get("groupName") or "signal group",
        "scopeType": "group",
    }


def signal_group_scopes(groups: list[Any]) -> list[dict[str, str]]:
    scopes = []
    seen = set()
    for group in groups:
        scope = signal_group_scope(group)
        if not scope or scope["groupRecipient"] in seen:
            continue
        seen.add(scope["groupRecipient"])
        scopes.append(scope)
    return scopes


def visible_groups_for_sender(
    envelope: dict[str, Any],
    groups: list[Any],
) -> list[dict[str, str]]:
    """Filter the bot's live Signal groups to those containing the DM sender."""
    sender_ids = sender_identifiers(envelope)
    if not sender_ids:
        return []
    visible_recipients = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        members = group.get("members") or []
        if not any(
            sender_ids.intersection(member_identifiers(member)) for member in members
        ):
            continue
        scope = signal_group_scope(group)
        if scope:
            visible_recipients.add(scope["groupRecipient"])
    return [
        scope
        for scope in signal_group_scopes(groups)
        if scope["groupRecipient"] in visible_recipients
    ]
