"""Shared admission policy and user-facing copy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

WAITLIST_MESSAGE = (
    "hi! this shared snorse-bot host is at its safe capacity, so commands aren’t "
    "active here yet. existing groups stay online while the host is upgraded. "
    "donations are optional and never affect queue position. see current capacity, "
    "costs, and self-hosting options at {site}."
)

SUPPORT_MESSAGE = (
    "small horsekeeping note: snorse-bot is donation-supported. contributions help "
    "cover hosting and make room for more groups, but there’s absolutely no pressure "
    "and service never depends on donating. costs and support options: {support}."
)


def support_due(scope: dict, now: datetime, support_url: str) -> bool:
    """Return whether an active group may receive its infrequent support note."""
    if not support_url or scope.get("scopeType") != "group":
        return False
    if scope.get("admissionStatus") != "active" or not scope.get("active", True):
        return False
    base_text = scope.get("supportEligibleAt") or scope.get("admittedAt")
    if not base_text:
        return False
    base = datetime.fromisoformat(base_text.replace("Z", "+00:00"))
    last_text = scope.get("supportLastSentAt")
    if last_text:
        base = datetime.fromisoformat(last_text.replace("Z", "+00:00"))
    # Stable 0–13 day spread, while preserving the minimum 180-day interval.
    jitter = int(sha256(scope["groupRecipient"].encode()).hexdigest()[:8], 16) % 14
    return now >= base + timedelta(days=180 + jitter)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
