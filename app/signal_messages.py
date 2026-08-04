"""Signal envelope, group ID, mention, and command-prefix handling."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass
from typing import Any


def message_data(envelope: dict[str, Any]) -> tuple[dict[str, Any], str]:
    received = envelope.get("dataMessage")
    if isinstance(received, dict):
        return received, "received"
    sent = (envelope.get("syncMessage") or {}).get("sentMessage")
    if isinstance(sent, dict):
        return sent, "sync-sent"
    return {}, "other"


def group_ids_match(received: str | None, configured: str) -> bool:
    if received == configured:
        return True
    if not configured.startswith("group."):
        return False
    try:
        decoded = base64.b64decode(configured.removeprefix("group."), validate=True)
        return decoded.decode("utf-8") == received
    except (binascii.Error, UnicodeDecodeError):
        return False


def canonical_group_id(group_id: str) -> str:
    if not group_id.startswith("group."):
        return group_id
    try:
        return base64.b64decode(group_id.removeprefix("group."), validate=True).decode(
            "utf-8"
        )
    except (binascii.Error, UnicodeDecodeError):
        return group_id


def group_recipient(group_id: str) -> str:
    if group_id.startswith("group."):
        return group_id
    encoded = base64.b64encode(group_id.encode()).decode("ascii")
    return f"group.{encoded}"


def signal_group(data_message: dict[str, Any]) -> dict[str, str] | None:
    group_info = data_message.get("groupInfo")
    if not isinstance(group_info, dict) or not group_info.get("groupId"):
        return None
    return {
        "groupId": canonical_group_id(group_info["groupId"]),
        "groupRecipient": group_recipient(group_info["groupId"]),
        "groupName": group_info.get("groupName") or "signal group",
    }


def utf16_slice(text: str, start: int, length: int) -> str:
    encoded = text.encode("utf-16-le")
    return encoded[start * 2 : (start + length) * 2].decode(
        "utf-16-le", errors="ignore"
    )


def remove_utf16_slice(text: str, start: int, length: int) -> str:
    encoded = text.encode("utf-16-le")
    return (encoded[: start * 2] + encoded[(start + length) * 2 :]).decode(
        "utf-16-le", errors="ignore"
    )


def command_after_horse_prefix(data_message: dict[str, Any]) -> str | None:
    text = data_message.get("message")
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(?:🐴|🐌)\ufe0f?\s*(.*?)\s*", text, flags=re.DOTALL)
    if not match:
        return None
    return match.group(1) or "help"


@dataclass(frozen=True)
class SignalMessageParser:
    """Parse commands for one configured bot identity."""

    phone_number: str
    signal_uuid: str
    mention_names: tuple[str, ...]
    logger: logging.Logger

    def is_bot_mention(self, text: str, mention: dict[str, Any]) -> bool:
        start = mention.get("start")
        length = mention.get("length")
        if not isinstance(start, int) or not isinstance(length, int):
            return False
        displayed = utf16_slice(text, start, length).lstrip("@").strip().casefold()
        return (
            mention.get("number") == self.phone_number
            or mention.get("name") == self.phone_number
            or (self.signal_uuid and mention.get("uuid") == self.signal_uuid)
            or displayed in self.mention_names
        )

    def commands_after_mentions(self, data_message: dict[str, Any]) -> list[str]:
        text = data_message.get("message")
        if not isinstance(text, str):
            self.logger.info("Group command ignored: message has no text")
            return []
        mentions = sorted(
            data_message.get("mentions") or [],
            key=lambda mention: (
                mention.get("start")
                if isinstance(mention.get("start"), int)
                else 2**31
            ),
        )
        shapes = []
        for mention in mentions:
            start = mention.get("start")
            length = mention.get("length")
            displayed = (
                utf16_slice(text, start, length)
                if isinstance(start, int) and isinstance(length, int)
                else ""
            )
            shapes.append(
                {
                    "start": start,
                    "length": length,
                    "has_uuid": bool(mention.get("uuid")),
                    "has_number": bool(mention.get("number")),
                    "has_name": bool(mention.get("name")),
                    "is_placeholder": displayed.lstrip("@").strip() == "\ufffc",
                }
            )
        self.logger.info(
            "Signal mention diagnostics: count=%d shapes=%s", len(mentions), shapes
        )
        encoded = text.encode("utf-16-le")
        commands = []
        for index, mention in enumerate(mentions):
            if not self.is_bot_mention(text, mention):
                continue
            command_start = mention["start"] + mention["length"]
            command_end = (
                mentions[index + 1]["start"]
                if index + 1 < len(mentions)
                else len(encoded) // 2
            )
            command = encoded[command_start * 2 : command_end * 2].decode(
                "utf-16-le", errors="ignore"
            )
            command = command.strip(" \t\r\n,:;-")
            if command:
                commands.append(command)
        if commands:
            self.logger.info(
                "Accepted %d Signal bot mention command(s)", len(commands)
            )
        else:
            self.logger.info("Group command ignored: no mention matched the bot")
        return commands

    def message_is_from_bot(self, envelope: dict[str, Any]) -> bool:
        _, message_kind = message_data(envelope)
        identities = {self.phone_number}
        if self.signal_uuid:
            identities.add(self.signal_uuid)
        return message_kind == "sync-sent" or any(
            value in identities
            for value in (
                envelope.get("sourceNumber"),
                envelope.get("source"),
                envelope.get("sourceUuid"),
            )
        )
