"""Deciding which worker handles a participant, and how many may exist.

Groundwork for moving off the single-pipeline design. Today every participant's
speech is multiplexed inside one pipeline by multi_speaker_stt.py (314 lines),
because the classic Pipecat model is one pipeline per user stream. The worker
model in the version we already run expresses the shape natively — a spike
confirmed three listener workers feeding one responder over the bus, with
speaker attribution intact.

What the framework does not decide is policy: which worker a frame belongs to,
when to create one, when to tear it down, and — the part production was missing
— when to refuse. On 2026-08-19 the eight-room load test accepted every session,
ran out of speech-recognition throughput, and quietly stopped replying: replies
fell from 261 to 23 while the processor sat at 77%. Nothing said no. A system
that accepts work it cannot do is worse than one that refuses, because the
refusal is visible and the degradation is not.

These functions are pure so the policy can be exhaustively tested without a bus,
a transport, or audio. The shell that owns real workers is deliberately thin.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from participant_workers import (
    MAX_PARTICIPANT_WORKERS,
    route_audio,
    teardown_for,
    worker_name,
)


class TestWorkerNaming(unittest.TestCase):
    def test_a_name_is_derived_from_the_participant_sid(self):
        self.assertEqual(worker_name("PA_abc123"), "listener-PA_abc123")

    def test_naming_is_stable(self):
        """The same participant must map to the same worker every time, or a
        second frame creates a second worker and the first leaks."""
        self.assertEqual(worker_name("PA_abc123"), worker_name("PA_abc123"))


class TestRouting(unittest.TestCase):
    def test_the_first_frame_from_a_participant_creates_their_worker(self):
        r = route_audio("PA_1", existing=set())
        self.assertEqual(r.worker, "listener-PA_1")
        self.assertTrue(r.create)

    def test_later_frames_reuse_it(self):
        r = route_audio("PA_1", existing={"listener-PA_1"})
        self.assertEqual(r.worker, "listener-PA_1")
        self.assertFalse(r.create, "creating twice would leak the first worker")

    def test_each_participant_gets_their_own(self):
        a = route_audio("PA_1", existing=set())
        b = route_audio("PA_2", existing={"listener-PA_1"})
        self.assertNotEqual(a.worker, b.worker)
        self.assertTrue(b.create)

    def test_audio_with_no_participant_is_dropped_not_routed(self):
        """Frames arrive before identity is known. Creating a worker for an
        empty sid would spawn one per stray frame."""
        self.assertIsNone(route_audio("", existing=set()))
        self.assertIsNone(route_audio(None, existing=set()))


class TestAdmissionControl(unittest.TestCase):
    """The thing the 8-room collapse lacked: the ability to say no."""

    def test_a_new_participant_is_refused_at_the_cap(self):
        existing = {f"listener-PA_{i}" for i in range(MAX_PARTICIPANT_WORKERS)}
        self.assertIsNone(
            route_audio("PA_new", existing=existing),
            "accepting past capacity is how the system stopped replying "
            "instead of refusing",
        )

    def test_participants_already_admitted_keep_working_at_the_cap(self):
        """Refusal must apply to admission only. Throttling people already in
        the meeting would break calls that are running fine."""
        existing = {f"listener-PA_{i}" for i in range(MAX_PARTICIPANT_WORKERS)}
        r = route_audio("PA_0", existing=existing)
        self.assertIsNotNone(r)
        self.assertFalse(r.create)

    def test_the_cap_can_be_lowered_per_call(self):
        self.assertIsNone(route_audio("PA_9", existing={"listener-PA_1"}, cap=1))

    def test_a_cap_of_zero_admits_nobody(self):
        self.assertIsNone(route_audio("PA_1", existing=set(), cap=0))


class TestTeardown(unittest.TestCase):
    def test_a_departing_participant_releases_their_worker(self):
        self.assertEqual(
            teardown_for("PA_1", existing={"listener-PA_1"}), "listener-PA_1"
        )

    def test_a_participant_who_never_spoke_tears_down_nothing(self):
        self.assertIsNone(teardown_for("PA_9", existing={"listener-PA_1"}))

    def test_teardown_frees_a_slot(self):
        """Otherwise a long meeting with churn silently fills the cap."""
        existing = {f"listener-PA_{i}" for i in range(MAX_PARTICIPANT_WORKERS)}
        gone = teardown_for("PA_0", existing=existing)
        existing.remove(gone)
        self.assertIsNotNone(route_audio("PA_new", existing=existing))


if __name__ == "__main__":
    unittest.main()
