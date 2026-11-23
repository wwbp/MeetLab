"""A turn's latency as Pipecat's voice-agent metrics define it: speech-to-text time to first byte
(the person stopped speaking -> their transcript) and user-bot latency (the person stopped
speaking -> the bot starts speaking).

Prod 2026-11-22 (load test 002e7f): participants heard replies after 2.2 s while the bot's own
timings summed to 0.9 s. `stt_ms` counted from the last audio packet, and audio never stops
arriving (silence is audio too), so the 0.58 s the transcript took was in no number."""
import unittest

from meetlab_bot.turn_latency import TurnLatency


class SpeechToText(unittest.TestCase):
    def test_from_stopped_speaking_to_the_transcript(self):
        t = TurnLatency()
        t.speech_stopped("ana", 10.0)
        self.assertAlmostEqual(t.transcript("ana", 10.575), 575)

    def test_each_person_has_their_own_clock(self):
        t = TurnLatency()
        t.speech_stopped("ana", 10.0)
        t.speech_stopped("ben", 10.4)
        self.assertAlmostEqual(t.transcript("ana", 10.6), 600)
        self.assertAlmostEqual(t.transcript("ben", 10.7), 300)

    def test_words_nobody_spoke_have_no_time(self):
        # A chat message is a transcript with no speech before it.
        self.assertIsNone(TurnLatency().transcript("ana", 10.0))

    def test_a_stop_is_used_once(self):
        t = TurnLatency()
        t.speech_stopped("ana", 10.0)
        t.transcript("ana", 10.5)
        self.assertIsNone(t.transcript("ana", 30.0))  # a later chat message is not 20 s of speech-to-text


class UserBot(unittest.TestCase):
    def test_from_stopped_speaking_to_the_bot_speaking(self):
        t = TurnLatency()
        t.speech_stopped("ana", 10.0)
        t.transcript("ana", 10.575)
        self.assertAlmostEqual(t.bot_started(11.571), 1571)

    def test_counts_from_the_last_person_heard(self):
        # Ana, then Ben, in one turn: the wait people feel runs from Ben's last word.
        t = TurnLatency()
        t.speech_stopped("ana", 10.0)
        t.transcript("ana", 10.5)
        t.speech_stopped("ben", 12.0)
        t.transcript("ben", 12.5)
        self.assertAlmostEqual(t.bot_started(13.0), 1000)

    def test_once_a_reply(self):
        # A reply has several sentences: only its first audio answers the turn.
        t = TurnLatency()
        t.speech_stopped("ana", 10.0)
        t.transcript("ana", 10.5)
        t.bot_started(11.0)
        self.assertIsNone(t.bot_started(12.0))

    def test_none_when_the_bot_speaks_unprompted(self):
        self.assertIsNone(TurnLatency().bot_started(5.0))  # the greeting

    def test_speech_with_no_transcript_does_not_start_the_clock(self):
        # A cough: the person "stopped speaking" but nothing was heard, so no reply answers it.
        t = TurnLatency()
        t.speech_stopped("ana", 10.0)
        self.assertIsNone(t.bot_started(11.0))


if __name__ == "__main__":
    unittest.main()
