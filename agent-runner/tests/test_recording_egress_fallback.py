import unittest
from types import SimpleNamespace

from livekit.protocol.egress import EncodedFileOutput

import runner


class FakeEgress:
    def __init__(
        self,
        first_error: Exception | None = None,
        second_error: Exception | None = None,
    ):
        self.first_error = first_error
        self.second_error = second_error
        self.requests = []

    async def start_room_composite_egress(self, request):
        self.requests.append(request)
        if len(self.requests) == 1 and self.first_error:
            raise self.first_error
        if len(self.requests) == 2 and self.second_error:
            raise self.second_error
        return SimpleNamespace(egress_id="EG_test")


class FakeLiveKitApi:
    def __init__(
        self,
        first_error: Exception | None = None,
        second_error: Exception | None = None,
    ):
        self.egress = FakeEgress(first_error, second_error)


class RecordingEgressFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_with_legacy_file_output_when_livekit_rejects_file_outputs(self):
        lk = FakeLiveKitApi(
            Exception(
                "TwirpError(code=invalid_argument, "
                "message=request has missing or invalid field: output, status=400)"
            )
        )
        file_output = EncodedFileOutput(filepath="/recordings/session.mp4")

        info = await runner._start_room_composite_egress(lk, "room-1", file_output)

        self.assertEqual(info.egress_id, "EG_test")
        self.assertEqual(len(lk.egress.requests), 2)
        self.assertEqual(len(lk.egress.requests[0].file_outputs), 1)
        self.assertFalse(lk.egress.requests[0].HasField("file"))
        self.assertEqual(len(lk.egress.requests[1].file_outputs), 0)
        self.assertTrue(lk.egress.requests[1].HasField("file"))
        self.assertEqual(lk.egress.requests[1].file.filepath, "/recordings/session.mp4")

    async def test_does_not_retry_unrelated_livekit_errors(self):
        lk = FakeLiveKitApi(Exception("TwirpError(code=not_found, message=room not found)"))
        file_output = EncodedFileOutput(filepath="/recordings/session.mp4")

        with self.assertRaisesRegex(Exception, "not_found"):
            await runner._start_room_composite_egress(lk, "room-1", file_output)

        self.assertEqual(len(lk.egress.requests), 1)

    async def test_combines_errors_when_both_output_formats_fail(self):
        first_error = Exception(
            "TwirpError(code=invalid_argument, "
            "message=request has missing or invalid field: output, status=400)"
        )
        second_error = Exception(
            "TwirpError(code=invalid_argument, "
            "message=request has missing or invalid field: output, status=400)"
        )
        lk = FakeLiveKitApi(first_error, second_error)
        file_output = EncodedFileOutput(filepath="/recordings/session.mp4")

        with self.assertRaisesRegex(
            runner.EgressOutputCompatibilityError,
            "rejected both room composite egress output formats",
        ) as ctx:
            await runner._start_room_composite_egress(lk, "room-1", file_output)

        self.assertIs(ctx.exception.file_outputs_error, first_error)
        self.assertIs(ctx.exception.legacy_file_error, second_error)
        self.assertEqual(len(lk.egress.requests), 2)


if __name__ == "__main__":
    unittest.main()
