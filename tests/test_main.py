import base64
import importlib
import os
import socket
import struct
import unittest
import urllib.parse
from datetime import datetime, timezone
from unittest.mock import patch


os.environ.setdefault("SNORSE_PHONE_NUMBER", "+15555550100")
os.environ.setdefault(
    "SNORSE_SIGNAL_UUID", "11111111-2222-4333-8444-555555555555"
)
os.environ.setdefault(
    "GOOGLE_SERVICE_ACCOUNT_EMAIL",
    "calendar-bot@example-project.iam.gserviceaccount.com",
)
bot = importlib.import_module("main")
bot.SCOPE_CACHE = [
    {
        "groupRecipient": "group.example",
        "groupName": "example house",
        "scopeType": "group",
        "active": True,
        "calendarIds": [],
    },
    {
        "groupRecipient": "group.Y28tb3Atc3RhYmxlLWlk",
        "groupName": "alpha group",
        "scopeType": "group",
        "active": True,
        "calendarIds": [],
    },
    {
        "groupRecipient": "group.house",
        "groupName": "example house",
        "scopeType": "group",
        "active": True,
        "calendarIds": [],
    },
    {
        "groupRecipient": "group.co-op",
        "groupName": "alpha group",
        "scopeType": "group",
        "active": True,
        "calendarIds": [],
    },
]
bot.SCOPE_CACHE_AT = bot.monotonic_time.monotonic()
TEST_GROUP = {
    "groupRecipient": "group.example",
    "groupName": "example house",
    "scopeType": "group",
}


class DirectMessageTests(unittest.TestCase):
    def test_signal_message_contents_are_not_logged(self):
        secret_text = "private sentinel message"
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {"message": secret_text},
            }
        }
        with (
            patch.object(bot, "handle_direct_command", return_value=False),
            self.assertLogs(bot.LOG, level="INFO") as captured,
        ):
            ignored, commands = bot.process_messages([message])

        self.assertNotIn(secret_text, "\n".join(captured.output))
        self.assertEqual((ignored, commands), (1, 0))

    def test_unprefixed_private_text_is_ignored(self):
        message = {
            "envelope": {
                "sourceName": "Pony",
                "sourceNumber": "+15555550101",
                "dataMessage": {"message": "hello", "attachments": []},
            }
        }
        with patch.object(bot, "api_json") as api:
            ignored, commands = bot.process_messages([message])
        self.assertEqual((ignored, commands), (1, 0))
        api.assert_not_called()

    def test_bot_reply_uses_signal_styled_text_mode(self):
        with patch.object(bot, "api_json") as api:
            bot.send_to_recipient("+15555550101", "try **help**.", styled=True)
        self.assertEqual(api.call_args.kwargs["payload"]["text_mode"], "styled")

    def test_signal_contact_upsert_is_cached(self):
        recipient = "9f849566-ab94-490f-b7ca-c0537054ba1a"
        bot.KNOWN_SIGNAL_CONTACTS.discard(recipient)
        with patch.object(bot, "api_json") as api:
            bot.ensure_signal_contact(recipient, "moog")
            bot.ensure_signal_contact(recipient, "moog")
        api.assert_called_once_with(
            "/v1/contacts/+15555550100",
            method="PUT",
            payload={"recipient": recipient, "name": "moog"},
        )
        bot.KNOWN_SIGNAL_CONTACTS.discard(recipient)


class ListenerTests(unittest.TestCase):
    @staticmethod
    def server_frame(opcode, payload, *, final=True):
        first = (0x80 if final else 0) | opcode
        if len(payload) < 126:
            return bytes([first, len(payload)]) + payload
        return bytes([first, 126]) + struct.pack("!H", len(payload)) + payload

    def test_json_rpc_receive_notification_is_normalized(self):
        payload = {
            "jsonrpc": "2.0",
            "method": "receive",
            "params": {
                "envelope": {
                    "sourceNumber": "+15555550101",
                    "dataMessage": {"message": "hello"},
                },
                "account": "+15555550100",
            },
        }
        self.assertEqual(
            bot.websocket_messages(bot.json.dumps(payload)),
            [payload["params"]],
        )

    def test_subscribed_receive_notification_is_normalized(self):
        result = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {"message": "hello"},
            },
            "account": "+15555550100",
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "receive",
            "params": {"subscription": 0, "result": result},
        }
        self.assertEqual(
            bot.websocket_messages(bot.json.dumps(payload)),
            [result],
        )

    def test_process_messages_reuses_existing_command_path(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {"message": "help"},
            }
        }
        with patch.object(bot, "handle_direct_command", return_value=True):
            self.assertEqual(bot.process_messages([message]), (0, 1))

    def test_websocket_reassembles_fragmented_text(self):
        client, server = socket.socketpair()
        stream = bot.SignalWebSocket("ws://localhost/example")
        stream.socket = client
        try:
            server.sendall(self.server_frame(0x1, b'{"mess', final=False))
            self.assertIsNone(stream.receive_text())
            server.sendall(self.server_frame(0x0, b'age":"hi"}'))
            self.assertEqual(stream.receive_text(), '{"message":"hi"}')
        finally:
            client.close()
            server.close()


class ReminderTests(unittest.TestCase):
    def test_daily_reminder_with_quoted_emoji_text(self):
        text, next_run, recurrence, zone_name = bot.parse_reminder(
            'remind "have a good day!! 😃🐴" every day at 8:03am',
            now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(text, "have a good day!! 😃🐴")
        self.assertEqual(recurrence, "daily")
        self.assertEqual(zone_name, "America/New_York")
        self.assertEqual(next_run, datetime(2026, 7, 24, 12, 3, tzinfo=timezone.utc))

    def test_multiple_mentioned_commands_in_one_message(self):
        first = '\ufffc remind "have a good day!! 😃🐴" every monday at 8:03am'
        second = '\ufffc remind "have a good day!! 😃🐴" every tuesday at 8:03am'
        text = f"{first}\n{second}"
        second_start = len(f"{first}\n".encode("utf-16-le")) // 2
        data_message = {
            "message": text,
            "mentions": [
                {
                    "uuid": bot.BOT_SIGNAL_UUID,
                    "start": 0,
                    "length": 1,
                },
                {
                    "uuid": bot.BOT_SIGNAL_UUID,
                    "start": second_start,
                    "length": 1,
                },
            ],
        }
        self.assertEqual(
            bot.commands_after_bot_mentions(data_message),
            [
                'remind "have a good day!! 😃🐴" every monday at 8:03am',
                'remind "have a good day!! 😃🐴" every tuesday at 8:03am',
            ],
        )

    def test_javascript_iso_timestamp_is_accepted(self):
        self.assertEqual(
            bot.parse_iso_instant("2026-07-27T23:00:00.000Z"),
            datetime(2026, 7, 27, 23, tzinfo=timezone.utc),
        )

    def test_rest_recipient_group_id_matches_incoming_raw_group_id(self):
        raw = "synthetic-group-id"
        configured = "group." + base64.b64encode(raw.encode()).decode()
        self.assertTrue(bot.group_ids_match(raw, configured))
        self.assertFalse(bot.group_ids_match("different", configured))

    def test_recurring_natural_language_example(self):
        text, next_run, recurrence, zone_name = bot.parse_reminder(
            "reminder to take out trash every monday night at 7pm ET",
            now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(text, "take out trash")
        self.assertEqual(recurrence, "monday")
        self.assertEqual(zone_name, "America/New_York")
        self.assertEqual(next_run, datetime(2026, 7, 27, 23, tzinfo=timezone.utc))

    def test_one_time_reminder(self):
        with patch.object(bot, "reminder_api", return_value={"id": "527"}) as api:
            response = bot.create_reminder(
                "remind submit the form tomorrow at 7:30pm ET",
                created_by="+15555550101",
                group={
                    "groupId": "co-op-raw",
                    "groupRecipient": "group.co-op",
                    "groupName": "alpha group",
                },
                now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            )
        self.assertIn("✅ 527", response)
        self.assertEqual(api.call_args.kwargs["method"], "POST")
        self.assertEqual(
            api.call_args.kwargs["payload"]["groupRecipient"], "group.co-op"
        )
        self.assertEqual(api.call_args.kwargs["payload"]["groupName"], "alpha group")

    def test_calendar_tool_can_be_scheduled_with_ascii_arrow(self):
        group = {
            "groupId": "co-op-raw",
            "groupRecipient": "group.co-op",
            "groupName": "alpha group",
        }
        with patch.object(bot, "reminder_api", return_value={"id": "527"}) as api:
            response = bot.create_reminder(
                "calendar next 7 days -> remind weekly digest every monday at 8am",
                created_by="+15555550101",
                group=group,
                now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(
            response,
            "✅ 527: weekly digest (every monday at 8am)",
        )
        payload = api.call_args.kwargs["payload"]
        self.assertEqual(
            payload["action"],
            {"type": "command", "command": "calendar next 7 days"},
        )
        self.assertEqual(payload["text"], "weekly digest")
        self.assertEqual(payload["recurrence"]["weekday"], 0)
        self.assertEqual(payload["recurrence"]["localTime"], "08:00")
        self.assertEqual(payload["groupRecipient"], "group.co-op")

    def test_unicode_arrow_is_accepted_for_scheduled_calendar(self):
        parsed = bot.parse_reminder_command(
            "calendar this month → remind monthly overview tomorrow at 9am",
            now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(parsed[0], "monthly overview")
        self.assertEqual(
            parsed[4],
            {"type": "command", "command": "calendar this month"},
        )

    def test_scheduled_tool_allowlist_rejects_mutating_commands(self):
        with self.assertRaisesRegex(bot.CommandError, "only.*calendar"):
            bot.parse_reminder_command(
                "cancel 123 -> remind every monday at 8am",
                now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            )

    def test_calendar_pipeline_routes_to_reminder_creation(self):
        command = "calendar next 7 days -> remind weekly digest every monday at 8am"
        with patch.object(
            bot, "create_reminder", return_value="✅ 527 created!"
        ) as create:
            response = bot.run_command(
                command,
                created_by="+15555550101",
                group={"groupRecipient": "group.co-op", "groupName": "alpha group"},
            )
        self.assertEqual(response, "✅ 527 created!")
        create.assert_called_once()
        self.assertEqual(create.call_args.args[0], command)
        self.assertTrue(bot.looks_like_command(command))
        self.assertFalse(bot.command_returns_content(command))

    def test_piped_reminder_display_separates_label_and_action(self):
        row = {
            "id": "415",
            "text": "horsie digest",
            "nextRunUtc": "2026-07-27T13:00:00+00:00",
            "timezone": "America/New_York",
            "recurrence": {
                "type": "weekly",
                "weekday": 0,
                "localTime": "09:00",
            },
            "action": {
                "type": "command",
                "command": "calendar next 7 days",
            },
        }
        self.assertEqual(
            bot.format_reminder_row(row),
            "415 horsie digest → calendar next 7 days (every monday at 9am)",
        )
        row["text"] = "calendar next 7 days horsie digest"
        self.assertEqual(
            bot.format_reminder_row(row),
            "415 horsie digest → calendar next 7 days (every monday at 9am)",
        )

    def test_cancel_reminder(self):
        responses = [
            {"reminders": [{"id": "1234abcd", "version": 2}]},
            None,
        ]
        with patch.object(bot, "reminder_api", side_effect=responses):
            result = bot.cancel_reminder("cancel #1234abcd", group=TEST_GROUP)
        self.assertEqual(result, "🗑️ cancelled reminder 1234abcd!")

    def test_recurring_reminder_list_omits_next_occurrence_date(self):
        with patch.object(
            bot,
            "reminder_api",
            return_value={
                "reminders": [
                    {
                        "id": "527",
                        "text": "feed the dog 😘 🐕",
                        "nextRunUtc": "2026-07-26T07:00:00Z",
                        "timezone": "America/New_York",
                        "recurrence": {
                            "type": "weekly",
                            "weekday": 6,
                            "localTime": "03:00",
                        },
                    }
                ]
            },
        ):
            result = bot.list_reminders(
                {
                    "groupRecipient": "group.example",
                    "groupName": "example house",
                    "scopeType": "group",
                }
            )
        self.assertEqual(
            result,
            "🐴 reminders:\n527 feed the dog 😘 🐕 (every sunday at 3am)",
        )
        self.assertNotIn("jul 26", result)

    def test_group_reminder_list_requests_only_that_group(self):
        group = {
            "groupId": "co-op-raw",
            "groupRecipient": "group.co-op",
            "groupName": "alpha group",
        }
        with patch.object(bot, "reminder_api", return_value={"reminders": []}) as api:
            result = bot.list_reminders(group)
        self.assertEqual(result, "🐴 no reminders set!")
        api.assert_called_once_with("/api/reminders?groupRecipient=group.co-op")

    def test_dm_reminder_list_is_grouped_by_signal_group(self):
        rows = [
            {
                "id": "420",
                "text": "coop task",
                "nextRunUtc": "2026-07-26T12:00:00Z",
                "timezone": "America/New_York",
                "recurrence": None,
                "groupRecipient": "group.co-op",
                "groupName": "alpha group",
            },
            {
                "id": "527",
                "text": "house task",
                "nextRunUtc": "2026-07-27T12:00:00Z",
                "timezone": "America/New_York",
                "recurrence": None,
                "groupRecipient": "group.house",
                "groupName": "example house",
            },
        ]
        with patch.object(bot, "reminder_api", return_value={"reminders": rows}):
            result = bot.list_reminders()
        self.assertIn("**alpha group**\n420 coop task", result)
        self.assertIn("**example house**\n527 house task", result)

    def test_schedule_shows_minutes_and_only_non_default_timezone(self):
        instant = datetime(2026, 7, 26, 12, 3, tzinfo=timezone.utc)
        self.assertEqual(
            bot.format_recurrence(
                {"type": "daily", "localTime": "08:03"},
                instant,
                "America/New_York",
            ),
            "every day at 8:03am",
        )
        self.assertEqual(
            bot.format_recurrence(
                {"type": "daily", "localTime": "05:03"},
                instant,
                "America/Los_Angeles",
            ),
            "every day at 5:03am pdt",
        )

    def test_due_one_time_reminder_is_sent_and_removed(self):
        responses = [
            {
                "reminders": [
                    {
                        "id": "1234abcd",
                        "version": 1,
                        "text": "test the alarm",
                        "nextRunUtc": "2026-07-24T12:00:00+00:00",
                        "timezone": "America/New_York",
                        "recurrence": None,
                        "groupRecipient": "group.example",
                        "groupName": "example house",
                    }
                ]
            },
            None,
        ]
        with (
            patch.object(bot, "reminder_api", side_effect=responses),
            patch.object(bot, "send_to_group") as send,
        ):
            fired = bot.fire_due_reminders(
                now=datetime(2026, 7, 24, 13, tzinfo=timezone.utc)
            )
        self.assertEqual(fired, 1)
        send.assert_called_once_with(
            "⏰ reminder: test the alarm", recipient="group.example"
        )

    def test_due_private_reminder_is_sent_only_to_its_dm_owner(self):
        now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        reminder = {
            "id": "321",
            "version": 1,
            "text": "drink water",
            "nextRunUtc": now.isoformat(),
            "timezone": "America/New_York",
            "recurrence": None,
            "action": None,
            "groupRecipient": "sender-uuid",
            "groupName": "personal",
            "scopeType": "personal",
        }
        with (
            patch.object(
                bot,
                "reminder_api",
                side_effect=[{"reminders": [reminder]}, None],
            ),
            patch.object(bot, "send_to_group") as send,
        ):
            self.assertEqual(bot.fire_due_reminders(now=now), 1)
        send.assert_called_once_with(
            "⏰ reminder: drink water",
            recipient="sender-uuid",
        )

    def test_calendar_event_is_announced_24_hours_before(self):
        with (
            patch.object(
                bot,
                "reminder_api",
                return_value={
                    "events": [
                        {
                            "id": "event-1",
                            "summary": "dentist",
                            "start": "2026-07-25T23:00:00-04:00",
                            "groupRecipient": "group.co-op",
                        }
                    ]
                },
            ) as api,
            patch.object(bot, "send_to_group") as send,
        ):
            fired = bot.fire_calendar_events(
                now=datetime(2026, 7, 24, 23, tzinfo=timezone.utc)
            )
        self.assertEqual(fired, 1)
        self.assertIn("leadMinutes=1440", api.call_args.args[0])
        self.assertIn("lookbackMinutes=12", api.call_args.args[0])
        send.assert_called_once_with(
            "📅 tomorrow: dentist (11pm)", recipient="group.co-op"
        )

    def test_due_calendar_action_sends_styled_group_digest(self):
        reminder = {
            "id": "527",
            "version": 1,
            "text": "calendar next 7 days",
            "nextRunUtc": "2026-07-27T12:00:00+00:00",
            "timezone": "America/New_York",
            "recurrence": {
                "type": "weekly",
                "weekday": 0,
                "localTime": "08:00",
            },
            "action": {
                "type": "command",
                "command": "calendar next 7 days",
            },
            "groupRecipient": "group.co-op",
            "groupName": "alpha group",
        }
        now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        with (
            patch.object(
                bot,
                "reminder_api",
                side_effect=[
                    {"reminders": [reminder]},
                    {"events": []},
                    {**reminder, "version": 2},
                ],
            ) as api,
            patch.object(bot, "send_to_group") as send,
        ):
            fired = bot.fire_due_reminders(now=now)
        self.assertEqual(fired, 1)
        self.assertIn("/api/calendar/events?", api.call_args_list[1].args[0])
        send.assert_called_once_with(
            "🐴 no events in the next 7 days!",
            recipient="group.co-op",
            styled=True,
        )
        self.assertEqual(api.call_args_list[2].kwargs["method"], "PUT")
        self.assertEqual(
            api.call_args_list[2].kwargs["payload"]["nextRunUtc"],
            "2026-08-03T12:00:00+00:00",
        )

    def test_calendar_command_lists_actual_event_times_grouped_by_date(self):
        group = {
            "groupId": "co-op-raw",
            "groupRecipient": "group.co-op",
            "groupName": "alpha group",
        }
        now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
        with patch.object(
            bot,
            "reminder_api",
            return_value={
                "events": [
                    {
                        "id": "event-1",
                        "summary": "dentist",
                        "start": "2026-07-25T23:00:00-04:00",
                    },
                    {
                        "id": "event-2",
                        "summary": "co-op meeting",
                        "start": "2026-07-27T19:30:00-04:00",
                    },
                ]
            },
        ) as api:
            response = bot.list_calendar_events("calendar", group=group, now=now)
        self.assertEqual(
            response,
            "🐴 **calendar**\n"
            "*next 7 days*\n\n"
            "**sat, jul 25**\n"
            "dentist (11pm)\n\n"
            "**mon, jul 27**\n"
            "co-op meeting (7:30pm)",
        )
        parsed = urllib.parse.urlsplit(api.call_args.args[0])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/calendar/events")
        self.assertEqual(query["groupRecipient"], ["group.co-op"])
        self.assertEqual(query["timeMin"], ["2026-07-24T12:00:00+00:00"])
        self.assertEqual(query["timeMax"], ["2026-07-31T12:00:00+00:00"])

    def test_empty_calendar_has_exact_short_response(self):
        group = {
            "groupId": "house-raw",
            "groupRecipient": "group.house",
            "groupName": "example house",
        }
        with patch.object(bot, "reminder_api", return_value={"events": []}):
            response = bot.list_calendar_events(
                "calendar",
                group=group,
                now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(response, "🐴 no events in the next 7 days!")

    def test_dm_calendar_groups_events_by_signal_group(self):
        now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
        with patch.object(
            bot,
            "reminder_api",
            return_value={
                "events": [
                    {
                        "id": "coop-event",
                        "summary": "co-op meeting",
                        "start": "2026-07-27T19:30:00-04:00",
                        "groupRecipient": "group.co-op",
                        "groupName": "alpha group",
                    },
                    {
                        "id": "house-event",
                        "summary": "house dinner",
                        "start": "2026-07-25T20:00:00-04:00",
                        "groupRecipient": "group.house",
                        "groupName": "example house",
                    },
                ]
            },
        ) as api:
            response = bot.list_calendar_events("calendar", group=None, now=now)
        self.assertEqual(
            response,
            "🐴 **calendar**\n"
            "*next 7 days*\n\n"
            "**alpha group**\n\n"
            "**mon, jul 27**\n"
            "co-op meeting (7:30pm)\n\n"
            "**example house**\n\n"
            "**sat, jul 25**\n"
            "house dinner (8pm)",
        )
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(api.call_args.args[0]).query
        )
        self.assertNotIn("groupRecipient", query)

    def test_calendar_window_supports_days_weeks_and_this_month(self):
        now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
        self.assertEqual(
            bot.calendar_window("next 10 days", now=now),
            (now, datetime(2026, 8, 3, 12, tzinfo=timezone.utc), "next 10 days"),
        )
        self.assertEqual(
            bot.calendar_window("next 3 weeks", now=now),
            (now, datetime(2026, 8, 14, 12, tzinfo=timezone.utc), "next 3 weeks"),
        )
        start, end, label = bot.calendar_window("this month", now=now)
        self.assertEqual(start, now)
        self.assertEqual(end, datetime(2026, 8, 1, 4, tzinfo=timezone.utc))
        self.assertEqual(label, "rest of this month")

    def test_calendar_commands_are_content_commands(self):
        for command in (
            "calendar",
            "calendar next 10 days",
            "calendar next 3 weeks",
            "calendar this month",
            "next 10 days",
            "next 3 weeks",
            "this month",
        ):
            with self.subTest(command=command):
                self.assertTrue(bot.looks_like_command(command))
                self.assertTrue(bot.command_returns_content(command))

    def test_calendar_link_help_explains_google_sharing(self):
        response = bot.run_command(
            "calendar link help",
            created_by="+15555550101",
            group={
                "groupRecipient": "group.example",
                "groupName": "example house",
                "scopeType": "group",
            },
        )
        self.assertIn(
            "calendar-bot@example-project.iam.gserviceaccount.com", response
        )
        self.assertIn("🐴 **calendar link** [calendar id]", response)
        self.assertIn("only Google Calendar is supported rn", response)
        self.assertTrue(bot.command_returns_content("link calendar help"))

    def test_calendar_link_saves_to_the_current_scope(self):
        scope = {
            "groupRecipient": "group.example",
            "groupName": "example house",
            "scopeType": "group",
        }
        with (
            patch.object(
                bot,
                "reminder_api",
                return_value={
                    "calendarId": "house@example.com",
                    "calendarName": "house",
                    "alreadyLinked": False,
                },
            ) as api,
            patch.object(bot, "configured_scopes"),
        ):
            response = bot.run_command(
                "calendar link house@example.com",
                created_by="+15555550101",
                group=scope,
            )
        self.assertEqual(response, "✅ linked **house**!")
        api.assert_called_once_with(
            "/api/calendars/link",
            method="POST",
            payload={**scope, "calendarId": "house@example.com"},
        )
        self.assertFalse(bot.command_returns_content("calendar link house@example.com"))

    def test_signal_groups_are_synchronized_to_scope_api(self):
        signal_groups = [
            {
                "id": "group.house",
                "name": "example house",
                "members": [],
            },
            {
                "id": "group.coop",
                "name": "alpha group",
                "members": [],
            },
        ]
        saved = [
            {
                "groupRecipient": "group.house",
                "groupName": "example house",
                "scopeType": "group",
            }
        ]
        with (
            patch.object(bot, "api_json", return_value=signal_groups),
            patch.object(bot, "reminder_api", return_value={"scopes": saved}) as api,
            patch.object(bot, "configured_scopes", return_value=saved),
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertEqual(bot.sync_signal_groups(), saved)
        send.assert_not_called()
        api.assert_called_once_with(
            "/api/scopes/sync",
            method="POST",
            payload={
                "groups": [
                    {
                        "groupRecipient": "group.house",
                        "groupName": "example house",
                        "scopeType": "group",
                    },
                    {
                        "groupRecipient": "group.coop",
                        "groupName": "alpha group",
                        "scopeType": "group",
                    },
                ]
            },
        )
        self.assertIn(
            "• **calendar** [**next** [number] [**days** | **weeks**] | "
            "**this month**]",
            bot.HELP_TEXT,
        )
        self.assertIn(
            "[**-> remind** [thing] [schedule]]",
            bot.HELP_TEXT,
        )

    def test_new_signal_group_is_welcomed_with_help(self):
        new_group = {
            "groupRecipient": "group.new",
            "groupName": "new ponies",
            "scopeType": "group",
            "active": True,
            "calendarIds": [],
        }
        with (
            patch.object(
                bot,
                "api_json",
                return_value=[
                    {
                        "id": "group.new",
                        "name": "new ponies",
                        "members": [],
                    }
                ],
            ),
            patch.object(
                bot,
                "reminder_api",
                return_value={"scopes": [new_group], "newGroups": [new_group]},
            ),
            patch.object(bot, "configured_scopes", return_value=[new_group]),
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertEqual(bot.sync_signal_groups(), [new_group])

        send.assert_called_once_with(
            "group.new",
            f"hi!\n\n{bot.HELP_TEXT}",
            styled=True,
        )

    def test_calendar_subcommand_forms_match_short_aliases(self):
        now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
        for full, short in (
            ("calendar next 10 days", "next 10 days"),
            ("calendar next 3 weeks", "next 3 weeks"),
            ("calendar this month", "this month"),
        ):
            with self.subTest(command=full):
                self.assertEqual(
                    bot.calendar_window(full, now=now),
                    bot.calendar_window(short, now=now),
                )

    def test_bad_schedule_has_short_actionable_error(self):
        with self.assertRaisesRegex(bot.CommandError, "couldn't read when"):
            bot.parse_reminder("remind take out trash sometime")

    def test_edit_reminder_id(self):
        existing = {
            "id": "1234abcd",
            "version": 2,
            "text": "trash",
            "nextRunUtc": "2026-07-27T23:00:00.000Z",
            "timezone": "America/New_York",
            "recurrence": None,
        }
        with patch.object(
            bot,
            "reminder_api",
            side_effect=[
                {"reminders": [existing]},
                {**existing, "id": "take-trash-out", "version": 3},
            ],
        ) as api:
            result = bot.edit_reminder(
                "edit 1234abcd id take-trash-out", group=TEST_GROUP
            )
        self.assertEqual(result, "✅ take-trash-out updated!")
        self.assertEqual(api.call_args.kwargs["payload"]["id"], "take-trash-out")

    def test_edit_reminder_text(self):
        existing = {
            "id": "1234abcd",
            "version": 2,
            "text": "trash",
            "nextRunUtc": "2026-07-27T23:00:00.000Z",
            "timezone": "America/New_York",
            "recurrence": None,
        }
        with patch.object(
            bot,
            "reminder_api",
            side_effect=[
                {"reminders": [existing]},
                {**existing, "text": "take out trash", "version": 3},
            ],
        ) as api:
            bot.edit_reminder(
                'edit 1234abcd text "take out trash"', group=TEST_GROUP
            )
        self.assertEqual(api.call_args.kwargs["payload"]["text"], "take out trash")

    def test_edit_reminder_time(self):
        existing = {
            "id": "1234abcd",
            "version": 2,
            "text": "trash",
            "nextRunUtc": "2026-07-27T23:00:00.000Z",
            "timezone": "America/New_York",
            "recurrence": {
                "type": "weekly",
                "weekday": 0,
                "localTime": "19:00",
            },
        }
        with patch.object(
            bot,
            "reminder_api",
            side_effect=[
                {"reminders": [existing]},
                {**existing, "version": 3},
            ],
        ) as api:
            bot.edit_reminder(
                "edit 1234abcd time 8pm",
                group=TEST_GROUP,
                now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            )
        payload = api.call_args.kwargs["payload"]
        self.assertEqual(payload["nextRunUtc"], "2026-07-28T00:00:00+00:00")
        self.assertEqual(payload["recurrence"]["localTime"], "20:00")

    def test_edit_reminder_date(self):
        existing = {
            "id": "1234abcd",
            "version": 2,
            "text": "appointment",
            "nextRunUtc": "2026-07-27T23:00:00.000Z",
            "timezone": "America/New_York",
            "recurrence": None,
        }
        with patch.object(
            bot,
            "reminder_api",
            side_effect=[
                {"reminders": [existing]},
                {**existing, "version": 3},
            ],
        ) as api:
            bot.edit_reminder(
                "edit 1234abcd date 2026-08-03",
                group=TEST_GROUP,
                now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(
            api.call_args.kwargs["payload"]["nextRunUtc"],
            "2026-08-03T23:00:00+00:00",
        )

    def test_only_real_signal_mention_triggers_command(self):
        text = "@snorse-bot help"
        data_message = {
            "message": text,
            "mentions": [
                {
                    "number": "+15555550100",
                    "start": 0,
                    "length": len("@snorse-bot"),
                }
            ],
        }
        self.assertEqual(bot.command_after_bot_mention(data_message), "help")
        self.assertIsNone(
            bot.command_after_bot_mention({"message": text, "mentions": []})
        )

    def test_uuid_only_signal_mention_triggers_command(self):
        data_message = {
            "message": "\ufffc help",
            "mentions": [
                {
                    "uuid": bot.BOT_SIGNAL_UUID,
                    "start": 0,
                    "length": 1,
                }
            ],
        }
        with self.assertLogs("snorse-bot", level="INFO") as logs:
            self.assertEqual(bot.command_after_bot_mention(data_message), "help")
        diagnostics = "\n".join(logs.output)
        self.assertIn("'has_uuid': True", diagnostics)
        self.assertIn("'is_placeholder': True", diagnostics)
        self.assertNotIn(bot.BOT_SIGNAL_UUID, diagnostics)

    def test_typed_bot_name_without_mention_record_stays_ignored(self):
        self.assertIsNone(
            bot.command_after_bot_mention(
                {"message": "@snorse-bot help", "mentions": []}
            )
        )

    def test_horse_prefix_extracts_command(self):
        self.assertEqual(
            bot.command_after_horse_prefix({"message": "  🐴 next 10 days  "}),
            "next 10 days",
        )
        self.assertEqual(bot.command_after_horse_prefix({"message": "🐴"}), "help")
        self.assertIsNone(
            bot.command_after_horse_prefix({"message": "hello 🐴 calendar"})
        )

    def test_different_uuid_placeholder_does_not_trigger_command(self):
        self.assertIsNone(
            bot.command_after_bot_mention(
                {
                    "message": "\ufffc help",
                    "mentions": [
                        {
                            "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "start": 0,
                            "length": 1,
                        }
                    ],
                }
            )
        )

    def test_group_mention_gets_help_response(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "timestamp": 1784923786795,
                "dataMessage": {
                    "message": "@snorse-bot help",
                    "timestamp": 1784923786795,
                    "mentions": [
                        {
                            "number": "+15555550100",
                            "start": 0,
                            "length": len("@snorse-bot"),
                        }
                    ],
                    "groupInfo": {"groupId": "group.example"},
                },
            }
        }
        with (
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_group_command(message))
        react.assert_called_once_with(message, "✅")
        send.assert_called_once_with("group.example", bot.HELP_TEXT, styled=True)

    def test_group_horse_prefix_runs_without_a_mention(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "timestamp": 1784923786795,
                "dataMessage": {
                    "message": "🐴 calendar",
                    "timestamp": 1784923786795,
                    "mentions": [],
                    "groupInfo": {"groupId": "group.example"},
                },
            }
        }
        with (
            patch.object(bot, "run_command", return_value="calendar results") as run,
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_group_command(message))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], "calendar")
        react.assert_called_once_with(message, "✅")
        send.assert_called_once_with("group.example", "calendar results", styled=True)

    def test_bot_authored_horse_message_cannot_trigger_recursively(self):
        for envelope in (
            {
                "sourceNumber": "+15555550100",
                "dataMessage": {
                    "message": "🐴 calendar",
                    "groupInfo": {"groupId": "group.example"},
                },
            },
            {
                "syncMessage": {
                    "sentMessage": {
                        "message": "🐴 calendar",
                        "groupInfo": {"groupId": "group.example"},
                    }
                }
            },
        ):
            with self.subTest(envelope=envelope):
                with (
                    patch.object(bot, "run_command") as run,
                    patch.object(bot, "send_to_recipient") as send,
                ):
                    self.assertFalse(bot.handle_group_command({"envelope": envelope}))
                run.assert_not_called()
                send.assert_not_called()

    def test_commands_in_another_group_use_that_group_scope(self):
        raw_group_id = "co-op-stable-id"
        recipient = "group." + base64.b64encode(raw_group_id.encode()).decode()
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {
                    "groupInfo": {
                        "groupId": raw_group_id,
                        "groupName": "alpha group",
                    }
                },
            }
        }
        with (
            patch.object(
                bot, "commands_after_bot_mentions", return_value=["reminders"]
            ),
            patch.object(
                bot, "run_command", return_value="🐴 no reminders set!"
            ) as run,
            patch.object(bot, "react_to_message"),
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_group_command(message))
        run.assert_called_once_with(
            "reminders",
            created_by="+15555550101",
            group={
                "groupId": raw_group_id,
                "groupRecipient": recipient,
                "groupName": "alpha group",
                "scopeType": "group",
            },
        )
        send.assert_called_once_with(recipient, "🐴 no reminders set!", styled=True)

    def test_commands_from_unregistered_group_are_ignored(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {
                    "message": "🐴 reminders",
                    "groupInfo": {
                        "groupId": "not-configured",
                        "groupName": "mystery group",
                    },
                },
            }
        }
        with (
            patch.object(bot, "sync_signal_groups", return_value=[]),
            patch.object(bot, "run_command") as run,
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertFalse(bot.handle_group_command(message))
        run.assert_not_called()
        react.assert_not_called()
        send.assert_not_called()

    def test_help_accepts_trailing_punctuation(self):
        self.assertEqual(
            bot.run_command("help!!", created_by="+15555550101"),
            bot.HELP_TEXT,
        )

    def test_multiple_group_command_replies_have_blank_line_between_them(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {
                    "groupInfo": {"groupId": "group.example"},
                },
            }
        }
        with (
            patch.object(
                bot, "commands_after_bot_mentions", return_value=["help", "reminders"]
            ),
            patch.object(bot, "run_command", side_effect=["first", "second"]),
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_group_command(message))
        react.assert_called_once_with(message, "✅")
        send.assert_called_once_with("group.example", "first\n\nsecond", styled=True)

    def test_successful_mutation_reacts_without_sending_a_reply(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "timestamp": 1784923786795,
                "dataMessage": {
                    "timestamp": 1784923786795,
                    "groupInfo": {"groupId": "group.example"},
                },
            }
        }
        with (
            patch.object(
                bot, "commands_after_bot_mentions", return_value=["edit 12 id 7"]
            ),
            patch.object(bot, "run_command", return_value="✅ 7 updated!"),
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_group_command(message))
        react.assert_called_once_with(message, "✅")
        send.assert_not_called()

    def test_multiple_commands_react_warning_and_send_errors_once(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "timestamp": 1784923786795,
                "dataMessage": {
                    "timestamp": 1784923786795,
                    "groupInfo": {"groupId": "group.example"},
                },
            }
        }
        with (
            patch.object(
                bot,
                "commands_after_bot_mentions",
                return_value=[
                    "edit 12 text trash",
                    "cancel missing",
                    "edit 12 time nope",
                ],
            ),
            patch.object(
                bot,
                "run_command",
                side_effect=[
                    "✅ 12 updated!",
                    bot.CommandError("i couldn't find reminder missing!"),
                    bot.CommandError("i couldn't read the time!"),
                ],
            ),
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_group_command(message))
        react.assert_called_once_with(message, "⚠️")
        send.assert_called_once_with(
            "group.example",
            "⚠️ i couldn't find reminder missing!\n\n⚠️ i couldn't read the time!",
            styled=True,
        )

    def test_unexpected_command_failure_reacts_x_and_sends_one_error(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {"groupInfo": {"groupId": "group.example"}},
            }
        }
        with (
            patch.object(
                bot, "commands_after_bot_mentions", return_value=["edit 12 id 7"]
            ),
            patch.object(bot, "run_command", side_effect=RuntimeError("boom")),
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_group_command(message))
        react.assert_called_once_with(message, "❌")
        send.assert_called_once_with(
            "group.example",
            "❌ something went wrong processing that command!",
            styled=True,
        )

    def test_group_reaction_targets_original_message(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "timestamp": 1784923786795,
                "dataMessage": {
                    "timestamp": 1784923786795,
                    "groupInfo": {"groupId": "group.example"},
                },
            }
        }
        with patch.object(bot, "api_json") as api:
            bot.react_to_message(message, "✅")
        api.assert_called_once_with(
            "/v1/reactions/+15555550100",
            payload={
                "reaction": "✅",
                "recipient": "group.example",
                "target_author": "+15555550101",
                "timestamp": 1784923786795,
            },
        )

    def test_synced_group_mention_is_ignored_as_bot_authored(self):
        message = {
            "envelope": {
                "syncMessage": {
                    "sentMessage": {
                        "message": "\ufffc help",
                        "mentions": [
                            {
                                "uuid": bot.BOT_SIGNAL_UUID,
                                "start": 0,
                                "length": 1,
                            }
                        ],
                        "groupInfo": {"groupId": "group.example"},
                    }
                }
            }
        }
        with (
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertFalse(bot.handle_group_command(message))
        react.assert_not_called()
        send.assert_not_called()

    def test_direct_help_replies_privately_without_relaying(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {"message": "🐴 help!!", "attachments": []},
            }
        }
        with (
            patch.object(bot, "ensure_signal_contact"),
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_direct_command(message))
        react.assert_called_once_with(message, "✅")
        send.assert_called_once_with("+15555550101", bot.HELP_TEXT, styled=True)

    def test_private_sender_uuid_works_when_phone_number_is_hidden(self):
        sender_uuid = "9f849566-ab94-490f-b7ca-c0537054ba1a"
        message = {
            "envelope": {
                "source": sender_uuid,
                "sourceUuid": sender_uuid,
                "sourceName": "moog",
                "dataMessage": {
                    "message": "🐴 calendar",
                    "attachments": [],
                },
            }
        }
        with (
            patch.object(bot, "run_command", return_value="calendar results") as run,
            patch.object(bot, "groups_for_sender", return_value=[]) as groups,
            patch.object(bot, "ensure_signal_contact") as ensure_contact,
            patch.object(bot, "react_to_message") as react,
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_direct_command(message))
        run.assert_called_once_with(
            "calendar",
            created_by=sender_uuid,
            group=None,
            dm_scope={
                "groupRecipient": sender_uuid,
                "groupName": "personal",
                "scopeType": "personal",
            },
            visible_groups=[],
        )
        groups.assert_called_once_with(message["envelope"])
        ensure_contact.assert_called_once_with(sender_uuid, "moog")
        react.assert_called_once_with(message, "✅")
        send.assert_called_once_with(sender_uuid, "calendar results", styled=True)

    def test_typed_mention_prefix_runs_a_dm_command(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {
                    "message": "@snorse-bot calendar",
                    "attachments": [],
                },
            }
        }
        with (
            patch.object(bot, "run_command", return_value="calendar results") as run,
            patch.object(bot, "groups_for_sender", return_value=[]),
            patch.object(bot, "ensure_signal_contact"),
            patch.object(bot, "react_to_message"),
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_direct_command(message))
        run.assert_called_once_with(
            "calendar",
            created_by="+15555550101",
            group=None,
            dm_scope={
                "groupRecipient": "+15555550101",
                "groupName": "personal",
                "scopeType": "personal",
            },
            visible_groups=[],
        )
        send.assert_called_once_with("+15555550101", "calendar results", styled=True)

    def test_private_calendar_link_does_not_require_group_membership(self):
        message = {
            "envelope": {
                "sourceUuid": "sender-uuid",
                "source": "sender-uuid",
                "sourceName": "Pony",
                "dataMessage": {
                    "message": "🐴 calendar link help",
                    "attachments": [],
                },
            }
        }
        with (
            patch.object(bot, "groups_for_sender") as groups,
            patch.object(bot, "ensure_signal_contact"),
            patch.object(bot, "react_to_message"),
            patch.object(bot, "send_to_recipient") as send,
        ):
            self.assertTrue(bot.handle_direct_command(message))
        groups.assert_not_called()
        send.assert_called_once_with(
            "sender-uuid",
            bot.CALENDAR_LINK_HELP,
            styled=True,
        )

    def test_dm_reminder_is_created_in_private_scope(self):
        now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        with patch.object(
            bot, "reminder_api", return_value={"id": "321"}
        ) as reminder_api:
            bot.run_command(
                "remind stretch tomorrow at 9am",
                created_by="sender-uuid",
                dm_scope=bot.personal_scope("sender-uuid"),
            )
        payload = reminder_api.call_args.kwargs["payload"]
        self.assertEqual(payload["groupRecipient"], "sender-uuid")
        self.assertEqual(payload["groupName"], "personal")
        self.assertEqual(payload["scopeType"], "personal")

    def test_signal_group_membership_matches_uuid_and_excludes_other_groups(self):
        envelope = {
            "sourceNumber": "+15555550101",
            "sourceUuid": "sender-uuid",
        }
        groups = [
            {
                "id": "group.house",
                "name": "example house",
                "members": [{"uuid": "sender-uuid"}],
            },
            {
                "id": "group.coop",
                "name": "alpha group",
                "members": [{"number": "+15555550999"}],
            },
        ]
        with patch.object(bot, "api_json", return_value=groups):
            self.assertEqual(
                bot.groups_for_sender(envelope),
                [
                    {
                        "groupRecipient": "group.house",
                        "groupName": "example house",
                        "scopeType": "group",
                    }
                ],
            )

    def test_dm_reminders_queries_personal_and_member_groups_only(self):
        personal = bot.personal_scope("sender-uuid")
        house = {
            "groupRecipient": "group.house",
            "groupName": "example house",
            "scopeType": "group",
        }
        requested = []

        def reminder_api(path, **_kwargs):
            requested.append(path)
            return {"reminders": []}

        with patch.object(bot, "reminder_api", side_effect=reminder_api):
            self.assertEqual(
                bot.run_command(
                    "reminders",
                    created_by="sender-uuid",
                    dm_scope=personal,
                    visible_groups=[house],
                ),
                "🐴 no reminders set!",
            )
        self.assertEqual(
            requested,
            [
                "/api/reminders?groupRecipient=sender-uuid",
                "/api/reminders?groupRecipient=group.house",
            ],
        )
        self.assertNotIn("group.coop", " ".join(requested))

    def test_dm_calendar_queries_member_groups_only(self):
        personal = bot.personal_scope("sender-uuid")
        house = {
            "groupRecipient": "group.house",
            "groupName": "example house",
            "scopeType": "group",
        }
        requested = []

        def reminder_api(path, **_kwargs):
            requested.append(path)
            return {"events": []}

        with patch.object(bot, "reminder_api", side_effect=reminder_api):
            response = bot.run_command(
                "calendar",
                created_by="sender-uuid",
                dm_scope=personal,
                visible_groups=[house],
            )
        self.assertEqual(response, "🐴 no events in the next 7 days!")
        self.assertEqual(len(requested), 2)
        self.assertTrue(any("groupRecipient=sender-uuid" in path for path in requested))
        self.assertTrue(any("groupRecipient=group.house" in path for path in requested))
        self.assertNotIn("group.coop", " ".join(requested))

    def test_unprefixed_calendar_dm_is_ignored_not_executed(self):
        message = {
            "envelope": {
                "sourceName": "Pony",
                "sourceNumber": "+15555550101",
                "dataMessage": {"message": "calendar", "attachments": []},
            }
        }
        with (
            patch.object(bot, "run_command") as run,
            patch.object(bot, "ensure_signal_contact") as ensure_contact,
            patch.object(bot, "send_to_recipient") as send,
        ):
            ignored, commands = bot.process_messages([message])
        self.assertEqual((ignored, commands), (1, 0))
        run.assert_not_called()
        ensure_contact.assert_not_called()
        send.assert_not_called()

    def test_ordinary_direct_message_is_not_consumed_as_command(self):
        message = {
            "envelope": {
                "sourceNumber": "+15555550101",
                "dataMessage": {"message": "hello horse friends", "attachments": []},
            }
        }
        with patch.object(bot, "send_to_recipient") as send:
            self.assertFalse(bot.handle_direct_command(message))
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
