"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def csv_names(value: str) -> tuple[str, ...]:
    return tuple(name.strip().casefold() for name in value.split(",") if name.strip())


def env_value(primary: str, legacy: str, default: str = "") -> str:
    """Read the provider-neutral name first while preserving old deployments."""
    return os.getenv(primary, os.getenv(legacy, default))


@dataclass(frozen=True)
class Settings:
    app_name: str
    signal_api_url: str
    phone_number: str
    signal_uuid: str
    reminder_api_url: str
    reminder_api_token: str
    mention_names: tuple[str, ...]
    calendar_service_account_email: str
    signal_api_timeout_seconds: int
    schedule_interval_seconds: int
    reconnect_max_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        app_name = os.getenv("BOT_DISPLAY_NAME", "snorse-bot").strip() or "snorse-bot"
        default_mentions = f"{app_name},{app_name.replace('-', ' ')}"
        return cls(
            app_name=app_name,
            signal_api_url=os.getenv(
                "SIGNAL_BASE_API_URL", "http://127.0.0.1:8080"
            ).rstrip("/"),
            phone_number=env_value(
                "BOT_PHONE_NUMBER", "SNORSE_PHONE_NUMBER"
            ),
            signal_uuid=env_value(
                "BOT_SIGNAL_UUID", "SNORSE_SIGNAL_UUID"
            ).strip(),
            reminder_api_url=env_value(
                "STATE_API_URL", "SNORSE_REMINDER_API_URL"
            ).rstrip("/"),
            reminder_api_token=env_value(
                "STATE_API_TOKEN", "SNORSE_REMINDER_API_TOKEN"
            ),
            mention_names=csv_names(
                env_value("BOT_MENTION_NAMES", "SNORSE_BOT_MENTION_NAMES", default_mentions)
            ),
            calendar_service_account_email=os.getenv(
                "GOOGLE_SERVICE_ACCOUNT_EMAIL", ""
            ).strip(),
            signal_api_timeout_seconds=int(
                os.getenv("SIGNAL_API_TIMEOUT_SECONDS", "120")
            ),
            schedule_interval_seconds=int(
                env_value(
                    "SCHEDULE_INTERVAL_SECONDS",
                    "SNORSE_SCHEDULE_INTERVAL_SECONDS",
                    "30",
                )
            ),
            reconnect_max_seconds=int(
                env_value(
                    "SIGNAL_RECONNECT_MAX_SECONDS",
                    "SNORSE_RECONNECT_MAX_SECONDS",
                    "30",
                )
            ),
        )
