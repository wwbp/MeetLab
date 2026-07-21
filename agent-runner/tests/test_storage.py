"""Unit tests for storage.py.

All tests run without I/O by patching os.environ and the filesystem.
S3 tests patch boto3 so the real library is never called.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import storage


class TestBuildFilename(unittest.TestCase):
    def test_contains_room_name(self):
        name = storage.build_filename("my-room", "transcript", "md")
        self.assertIn("my-room", name)

    def test_special_chars_replaced(self):
        name = storage.build_filename("room/with spaces!", "transcript", "md")
        self.assertNotIn("/", name)
        self.assertNotIn(" ", name)
        self.assertNotIn("!", name)

    def test_correct_extension(self):
        self.assertTrue(storage.build_filename("r", "transcript", "md").endswith(".md"))
        self.assertTrue(storage.build_filename("r", "recording", "mp4").endswith(".mp4"))

    def test_contains_type_label(self):
        self.assertIn("transcript", storage.build_filename("r", "transcript", "md"))
        self.assertIn("recording", storage.build_filename("r", "recording", "mp4"))


class TestBuildRecordingPath(unittest.TestCase):
    def test_local_returns_absolute_path(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "local", "RECORDINGS_PATH": "/rec"}):
            path = storage.build_recording_path("test-room")
        self.assertTrue(path.startswith("/rec/"))
        self.assertIn("test-room", path)
        self.assertTrue(path.endswith(".mp4"))

    def test_s3_returns_key(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}):
            path = storage.build_recording_path("test-room")
        self.assertTrue(path.startswith("recordings/"))
        self.assertTrue(path.endswith(".mp4"))


class TestWriteFileLocal(unittest.IsolatedAsyncioTestCase):
    async def test_writes_bytes_to_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STORAGE_BACKEND": "local", "RECORDINGS_PATH": tmpdir}):
                path = await storage.write_file("test.md", b"hello world")
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).read_bytes(), b"hello world")

    async def test_returns_full_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STORAGE_BACKEND": "local", "RECORDINGS_PATH": tmpdir}):
                path = await storage.write_file("out.txt", b"data")
            self.assertTrue(path.startswith(tmpdir))


class TestReadBytesLocal(unittest.IsolatedAsyncioTestCase):
    async def test_reads_bytes_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STORAGE_BACKEND": "local", "RECORDINGS_PATH": tmpdir}):
                path = await storage.write_file("blob.wav", b"\x00\x01\x02\x03")
                self.assertEqual(await storage.read_bytes(path), b"\x00\x01\x02\x03")


class TestWriteFileS3(unittest.IsolatedAsyncioTestCase):
    async def test_calls_put_object(self):
        mock_s3 = MagicMock()
        mock_boto = MagicMock()
        mock_boto.client.return_value = mock_s3

        env = {
            "STORAGE_BACKEND": "s3",
            "S3_KEY_ID": "kid",
            "S3_KEY_SECRET": "ksec",
            "S3_BUCKET": "mybucket",
            "S3_REGION": "us-east-1",
        }
        with patch.dict(os.environ, env):
            with patch.dict("sys.modules", {"boto3": mock_boto}):
                path = await storage.write_file("file.mp4", b"video")

        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs["Bucket"], "mybucket")
        self.assertIn("file.mp4", call_kwargs["Key"])
        self.assertEqual(call_kwargs["Body"], b"video")
        self.assertTrue(path.startswith("recordings/"))

    async def test_raises_on_missing_credentials(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "s3", "S3_KEY_ID": ""}):
            with self.assertRaises(RuntimeError):
                await storage.write_file("f.mp4", b"x")


class TestGetDownloadUrl(unittest.TestCase):
    def test_local_returns_path_unchanged(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            url = storage.get_download_url("/recordings/file.md")
        self.assertEqual(url, "/recordings/file.md")

    def test_s3_calls_presign(self):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"
        mock_boto = MagicMock()
        mock_boto.client.return_value = mock_s3

        env = {
            "STORAGE_BACKEND": "s3",
            "S3_KEY_ID": "kid",
            "S3_KEY_SECRET": "ksec",
            "S3_BUCKET": "bkt",
            "S3_REGION": "us-east-1",
            "PRESIGNED_URL_TTL_SECONDS": "900",
        }
        with patch.dict(os.environ, env):
            with patch.dict("sys.modules", {"boto3": mock_boto}):
                url = storage.get_download_url("recordings/file.mp4")

        mock_s3.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "bkt", "Key": "recordings/file.mp4"},
            ExpiresIn=900,
        )
        self.assertEqual(url, "https://s3.example.com/presigned")

    def test_s3_raises_on_missing_credentials(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "s3", "S3_KEY_ID": ""}):
            with self.assertRaises(RuntimeError):
                storage.get_download_url("recordings/x.mp4")


class TestIsLocal(unittest.TestCase):
    def test_local_backend(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            self.assertTrue(storage.is_local())

    def test_s3_backend(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}):
            self.assertFalse(storage.is_local())

    def test_default_is_local(self):
        env = {k: v for k, v in os.environ.items() if k != "STORAGE_BACKEND"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(storage.is_local())


if __name__ == "__main__":
    unittest.main()
