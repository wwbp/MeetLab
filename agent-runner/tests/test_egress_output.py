"""Where a room's video recording is uploaded, and with which credentials (runner.egress_file_output).

LiveKit Cloud uploads from its own servers, so it needs a write-only key with each request.
Our own egress (egress_server.tf) runs in our AWS with a task role that may only add
recordings, and its config names the bucket: no key travels at all (the user's decision,
2026-10-05: video recording on the self-hosted stack)."""
import os
import unittest

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")

from runner import egress_file_output  # noqa: E402

S3 = {"backend": "s3", "bucket": "media", "region": "us-east-1", "endpoint": None,
      "egress_key_id": "AKIA", "egress_key_secret": "s"}
NO_KEY = {**S3, "egress_key_id": None, "egress_key_secret": None}


class TestEgressOutput(unittest.TestCase):
    def test_livekit_cloud_gets_the_write_only_key(self):
        out = egress_file_output(S3, "room-1.mp4", cloud=True, local_path="/r/room-1.mp4")
        self.assertEqual((out.filepath, out.s3.access_key, out.s3.bucket), ("recordings/room-1.mp4", "AKIA", "media"))

    def test_our_own_egress_gets_no_key(self):
        # Even when the runner holds one: our egress uploads with its task role.
        for cfg in (S3, NO_KEY):
            out = egress_file_output(cfg, "room-1.mp4", cloud=False, local_path="/r/room-1.mp4")
            self.assertEqual(out.filepath, "recordings/room-1.mp4")
            self.assertFalse(out.HasField("s3"))

    def test_livekit_cloud_without_a_key_is_refused(self):
        with self.assertRaises(ValueError):
            egress_file_output(NO_KEY, "room-1.mp4", cloud=True, local_path="/r/room-1.mp4")

    def test_local_disk_for_local_development(self):
        out = egress_file_output({**S3, "backend": "local"}, "room-1.mp4", cloud=False, local_path="/r/room-1.mp4")
        self.assertEqual(out.filepath, "/r/room-1.mp4")


if __name__ == "__main__":
    unittest.main()
