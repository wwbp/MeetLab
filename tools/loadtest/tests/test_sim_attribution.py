"""The verdict of the three-speaker attribution run (sim_attribution.py, diagnosis F11)."""
import asyncio
import unittest

from meetlab_loadtest.sim_attribution import drive_ticket, verdict

LINES = {
    "a": "What should we prioritise for the leadership role this quarter?",
    "b": "We are seeing higher latency on the transcription service since Tuesday.",
    "c": "Can you explain what a retrieval augmented generation system does?",
}


class VerdictTests(unittest.TestCase):
    def test_each_speaker_with_their_own_words_is_clean(self):
        stored = [("a", "what should we prioritize for the leadership role this quarter"),
                  ("b", "we are seeing higher latency on the transcription service since tuesday"),
                  ("c", "can you explain what a retrieval augmented generation system does")]
        self.assertEqual(verdict(LINES, stored), [])

    def test_words_stored_under_the_wrong_speaker(self):
        stored = [("a", "we are seeing higher latency on the transcription service since tuesday")]
        self.assertIn("a: stored b's words", verdict(LINES, stored))

    def test_two_speakers_merged_into_one_turn(self):
        stored = [("a", LINES["a"] + " " + LINES["c"])]
        self.assertIn("a: one turn holds a's and c's words", verdict(LINES, stored))

    def test_a_speaker_whose_words_never_arrived(self):
        stored = [("a", LINES["a"]), ("b", LINES["b"])]
        self.assertIn("c: never transcribed", verdict(LINES, stored))


def _reply(kind, **view):
    return {"ticket": "t1", "view": {"kind": kind, **view}}


class DriveTicketTests(unittest.TestCase):
    """GROUP_LINK=1: one participant's ticket, from searching to the room it gives."""

    def drive(self, polls, deadline_s=60):
        replies, calls, now = iter(polls[1:]), [], [0.0]

        async def poll(tid):
            calls.append("poll")
            return next(replies)

        async def ready(tid):
            calls.append("ready")
            return next(replies)

        async def sleep(s):
            now[0] += s
        room = asyncio.run(drive_ticket(polls[0], poll, ready, lambda: now[0], sleep, deadline_s))
        return room, calls

    def test_searching_then_ready_check_presses_ready_once_and_returns_the_room(self):
        room, calls = self.drive([
            _reply("searching"), _reply("searching"),
            _reply("ready_check", pressed=False),
            _reply("ready_check", pressed=True),
            _reply("starting", room=None),
            _reply("starting", room="link-x"),
        ])
        self.assertEqual(room, "link-x")
        self.assertEqual(calls, ["poll", "poll", "ready", "poll", "poll"])

    def test_a_missed_or_expired_ticket_fails_loud(self):
        for kind in ("missed", "expired", "done"):
            with self.assertRaisesRegex(RuntimeError, kind):
                self.drive([_reply("searching"), _reply(kind)])

    def test_no_room_before_the_deadline_times_out(self):
        with self.assertRaises(TimeoutError):
            self.drive([_reply("searching")] * 1000, deadline_s=5)


if __name__ == "__main__":
    unittest.main()
