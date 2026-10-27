"""The runner's bearer check: fails closed when no secret is configured."""
import unittest
from unittest import mock

from fastapi import HTTPException
from starlette.requests import Request

from meetlab_api import runner


def _request(auth: str | None) -> Request:
    headers = [(b"authorization", auth.encode())] if auth is not None else []
    return Request({"type": "http", "headers": headers})


class VerifyApiKeyTest(unittest.TestCase):
    def test_right_secret_passes(self):
        with mock.patch.object(runner, "BOT_RUNNER_SECRET", "s3cret"):
            runner.verify_api_key(_request("Bearer s3cret"))

    def test_wrong_or_missing_secret_is_rejected(self):
        with mock.patch.object(runner, "BOT_RUNNER_SECRET", "s3cret"):
            for auth in ("Bearer nope", "Bearer s3cre", None):
                with self.assertRaises(HTTPException) as cm:
                    runner.verify_api_key(_request(auth))
                self.assertEqual(cm.exception.status_code, 401)

    def test_no_secret_configured_rejects_everything(self):
        for unset in (None, ""):
            with mock.patch.object(runner, "BOT_RUNNER_SECRET", unset):
                for auth in (None, "Bearer ", "Bearer None"):
                    with self.assertRaises(HTTPException) as cm:
                        runner.verify_api_key(_request(auth))
                    self.assertEqual(cm.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
