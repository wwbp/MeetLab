"""Log→event mirroring: how a loguru record becomes a durable event row.

The decision of *whether* and *what* to record is a pure function so it can be
tested without a database or an event loop; the sink around it is a thin wrapper.
This is the safety net that means a new `logger.error` anywhere in the runner or
the bot shows up in the admin console without anyone remembering to wire it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from event_log import EVENT_SEVERITIES, event_from_log_record


def _record(level="ERROR", message="boom", extra=None, exception=None, name="bot"):
    """Shape of the bits of a loguru record this code reads."""
    levels = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    return {
        "level": type("L", (), {"name": level, "no": levels[level]})(),
        "message": message,
        "name": name,
        "function": "on_participant_connected",
        "line": 812,
        "extra": extra or {},
        "exception": exception,
    }


class TestEventFromLogRecord(unittest.TestCase):
    def test_error_becomes_an_error_event(self):
        event = event_from_log_record(_record(level="ERROR"))
        self.assertIsNotNone(event)
        self.assertEqual(event["severity"], "error")
        self.assertIn(event["severity"], EVENT_SEVERITIES)

    def test_critical_is_recorded_as_error(self):
        # There is no 'critical' severity — the console filters three levels.
        self.assertEqual(event_from_log_record(_record(level="CRITICAL"))["severity"], "error")

    def test_warning_becomes_a_warning_event(self):
        self.assertEqual(event_from_log_record(_record(level="WARNING"))["severity"], "warning")

    def test_info_and_debug_are_not_recorded(self):
        # Every session logs hundreds of info lines; mirroring them would bury
        # the incidents this table exists to surface.
        self.assertIsNone(event_from_log_record(_record(level="INFO")))
        self.assertIsNone(event_from_log_record(_record(level="DEBUG")))

    def test_event_type_names_the_source_module(self):
        event = event_from_log_record(_record(level="ERROR", name="bot"))
        self.assertEqual(event["event_type"], "bot.log.error")
        event = event_from_log_record(_record(level="WARNING", name="runner"))
        self.assertEqual(event["event_type"], "runner.log.warning")

    def test_payload_carries_enough_to_find_the_code(self):
        event = event_from_log_record(_record(level="ERROR", message="egress failed"))
        self.assertEqual(event["payload"]["message"], "egress failed")
        self.assertEqual(event["payload"]["function"], "on_participant_connected")
        self.assertEqual(event["payload"]["line"], 812)
        self.assertEqual(event["payload"]["level"], "ERROR")

    def test_room_and_conversation_come_from_bound_context(self):
        # logger.bind(room_name=..., conv_id=...) makes an error joinable to the
        # session it happened in, which is the whole point for a study incident.
        event = event_from_log_record(
            _record(extra={"room_name": "link-abc", "conv_id": "conv-1", "user": "ann"})
        )
        self.assertEqual(event["room_name"], "link-abc")
        self.assertEqual(event["conv_id"], "conv-1")
        # Other bound values are kept, but out of the routing columns.
        self.assertEqual(event["payload"]["extra"]["user"], "ann")

    def test_missing_context_is_allowed(self):
        event = event_from_log_record(_record())
        self.assertIsNone(event["room_name"])
        self.assertIsNone(event["conv_id"])

    def test_exception_type_is_surfaced(self):
        exc = type("E", (), {"type": ValueError})()
        event = event_from_log_record(_record(exception=exc))
        self.assertEqual(event["payload"]["exception"], "ValueError")

    def test_a_malformed_record_never_raises(self):
        # The sink runs inside logging; an exception here would be a log loop.
        self.assertIsNone(event_from_log_record({}))
        self.assertIsNone(event_from_log_record(None))

    def test_long_messages_are_truncated(self):
        event = event_from_log_record(_record(message="x" * 10_000))
        self.assertLessEqual(len(event["payload"]["message"]), 2_048)


if __name__ == "__main__":
    unittest.main()
