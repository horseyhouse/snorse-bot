import os
import unittest
from unittest.mock import patch

from app.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_custom_brand_provides_default_mention_names(self):
        with patch.dict(
            os.environ,
            {
                "BOT_DISPLAY_NAME": "pony-pal",
                "SNORSE_PHONE_NUMBER": "+15551234567",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.app_name, "pony-pal")
        self.assertEqual(settings.mention_names, ("pony-pal", "pony pal"))
        self.assertEqual(settings.signal_uuid, "")
        self.assertEqual(settings.calendar_service_account_email, "")

    def test_explicit_mentions_are_normalized(self):
        with patch.dict(
            os.environ,
            {
                "SNORSE_PHONE_NUMBER": "+15551234567",
                "SNORSE_BOT_MENTION_NAMES": " Horse Bot, HORSE ",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.mention_names, ("horse bot", "horse"))

    def test_provider_neutral_names_override_legacy_names(self):
        with patch.dict(
            os.environ,
            {
                "BOT_PHONE_NUMBER": "+15550000001",
                "SNORSE_PHONE_NUMBER": "+15550000002",
                "STATE_API_URL": "https://state.example/v1/",
                "SNORSE_REMINDER_API_URL": "https://legacy.example",
                "STATE_API_TOKEN": "portable-token",
                "SNORSE_REMINDER_API_TOKEN": "legacy-token",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.phone_number, "+15550000001")
        self.assertEqual(settings.reminder_api_url, "https://state.example/v1")
        self.assertEqual(settings.reminder_api_token, "portable-token")


if __name__ == "__main__":
    unittest.main()
