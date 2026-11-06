"""SQLAdmin's own login (/api/db): the second line of defence behind meet's console middleware."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")
# Importing runner builds the engine; nothing here connects to it.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:1/none")

from meetlab_api import runner  # noqa: E402


def _login(password, ip="10.0.0.1"):
    async def form():
        return {"password": password}

    request = SimpleNamespace(form=form, client=SimpleNamespace(host=ip), session={})
    return asyncio.run(runner.AdminAuth(secret_key="x").login(request))


class AdminAuthTests(unittest.TestCase):
    def setUp(self):
        runner._login_failures.clear()

    def test_right_password_logs_in(self):
        with mock.patch.object(runner, "CONSOLE_PASSWORD", "hunter2"):
            self.assertTrue(_login("hunter2"))

    def test_wrong_password_fails(self):
        with mock.patch.object(runner, "CONSOLE_PASSWORD", "hunter2"):
            self.assertFalse(_login("nope"))

    def test_unset_or_empty_password_fails_closed(self):
        for value in (None, ""):
            with mock.patch.object(runner, "CONSOLE_PASSWORD", value):
                self.assertFalse(_login(""))
                self.assertFalse(_login(None))

    def test_compares_in_constant_time(self):
        with mock.patch.object(runner, "CONSOLE_PASSWORD", "hunter2"), \
                mock.patch.object(runner.hmac, "compare_digest", wraps=runner.hmac.compare_digest) as cmp:
            _login("hunter2")
        cmp.assert_called_once_with(b"hunter2", b"hunter2")

    def test_five_failures_per_ip_per_minute_then_locked(self):
        with mock.patch.object(runner, "CONSOLE_PASSWORD", "hunter2"):
            for _ in range(5):
                self.assertFalse(_login("nope"))
            self.assertFalse(_login("hunter2"), "6th attempt is refused without checking")
            self.assertTrue(_login("hunter2", ip="10.0.0.2"), "other IPs are unaffected")
            later = runner.time.monotonic() + 61
            with mock.patch.object(runner.time, "monotonic", return_value=later):
                self.assertTrue(_login("hunter2"), "the window expires after a minute")


if __name__ == "__main__":
    unittest.main()
