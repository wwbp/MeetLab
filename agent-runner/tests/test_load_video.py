"""Video in the load test: rooms that publish a camera, so recording (egress) is measured on
real video, and the report says how many rooms were recorded and what it cost the
recording machines.
    uv run python -m unittest tests.test_load_video -v
"""
import unittest

from load_plan import camera_frames, recordings_summary
from tests.load_report import markdown


class CameraTest(unittest.TestCase):
    def test_frames_are_i420_at_the_size_asked_and_they_move(self):
        frames = camera_frames(64, 48, 6)  # I420, the encoder's own format: no conversion per frame
        self.assertEqual(len(frames), 6)
        self.assertTrue(all(len(f) == 64 * 48 * 3 // 2 for f in frames))
        self.assertEqual(len(set(frames)), 6, "a still picture would encode to almost nothing")


class RecordingsTest(unittest.TestCase):
    MEETINGS = [
        {"id": "s1", "media_files": [{"type": "recording", "status": "available"}, {"type": "audio_track", "status": "available"}]},
        {"id": "s2", "media_files": [{"type": "recording", "status": "failed"}]},
        {"id": "s3", "media_files": [{"type": "recording", "status": "pending"}]},
        {"id": "s4", "media_files": []},
        {"id": "other", "media_files": [{"type": "recording", "status": "available"}]},  # not this run's
    ]

    def test_each_room_of_the_run_counted_once_by_its_recording(self):
        self.assertEqual(recordings_summary(self.MEETINGS, ["s1", "s2", "s3", "s4"]),
                         {"rooms": 4, "available": 1, "failed": 1, "pending": 1, "none": 1})

    def test_the_report_says_how_many_rooms_were_recorded(self):
        r = {"run": "x", "shape": "spike", "profile": "p", "config": {}, "capacity_rooms": 4, "pass": True,
             "steps": [{"rooms": 4, "turns": 9, "reply_rate": 1.0, "p50_ms": 1, "p95_ms": 1, "p99_ms": 1,
                        "join_p95_s": 1, "pass": True, "why": [], "server": {"stages": {}, "max": {"egress_hosts_cpu": 61}}}],
             "recordings": {"rooms": 4, "available": 1, "failed": 1, "pending": 1, "none": 1}}
        text = markdown(r)
        self.assertIn("**Recordings**: 1 of 4 rooms recorded (failed 1, still pending 1, none asked 1)", text)
        self.assertIn("recording machines CPU", text)
        self.assertIn("| 61 |", text)


if __name__ == "__main__":
    unittest.main()
