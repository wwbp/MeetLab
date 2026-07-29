"""Bot-config defaults — the STT default is parakeet-tdt-0.6b-v2 at ep=100 (Experiment 6)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db.models import BotConfig


class TestBotConfigDefaults(unittest.TestCase):
    def test_stt_model_default_is_parakeet(self):
        col = BotConfig.__table__.c.stt_model
        self.assertEqual(col.default.arg, "parakeet-tdt-0.6b-v2")

    def test_endpointing_default_is_100(self):
        col = BotConfig.__table__.c.stt_endpointing_ms
        self.assertEqual(col.default.arg, 100)
        self.assertEqual(str(col.server_default.arg), "100")

    def test_auto_record_default_is_false(self):
        col = BotConfig.__table__.c.auto_record
        self.assertEqual(col.default.arg, False)
        self.assertEqual(str(col.server_default.arg).lower(), "false")

    def test_session_limit_minutes_default_is_zero(self):
        """0 = unlimited, so an unconfigured room behaves exactly as it did before."""
        col = BotConfig.__table__.c.session_limit_minutes
        self.assertEqual(col.default.arg, 0)
        self.assertEqual(str(col.server_default.arg), "0")
        self.assertFalse(col.nullable)


if __name__ == "__main__":
    unittest.main()
