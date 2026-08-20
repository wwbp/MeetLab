"""Assembling one participant's listener worker.

A listener is the small pipeline that turns one person's audio into transcripts:
voice-activity detection, then speech recognition, then a bridge onto the bus
where the responder is waiting. It is the replacement for the per-participant
chains that multi_speaker_stt.py builds and demultiplexes internally.

The pieces themselves already exist and are tested elsewhere — the VAD builder,
the recognition chains, the bus bridge. What is untested, and worth pinning, is
the assembly:

**Order.** Audio must meet the detector before the recogniser. The recognisers we
use are segmented: they transcribe what falls between VAD boundaries and emit
nothing without them, so a reversed pipeline produces silence rather than an
error — the worst kind of wiring bug.

**Targeting.** The bridge must be aimed at the responder. The spike that
validated this architecture used a bridge that accepted from every peer, and
three utterances produced 22,147 deliveries because the workers echoed each
other. Correct targeting gave exactly three. That is a one-line difference
between working and catastrophic, so it gets a test.

Constructors are injected so this runs against fakes in microseconds rather than
building Silero and a recognition client per case.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from listener_factory import RESPONDER_WORKER, build_listener


class Fake:
    def __init__(self, kind, sid=None, target=None):
        self.kind = kind
        self.sid = sid
        self.target = target

    def __repr__(self):
        return f"<{self.kind}>"


class Harness:
    def __init__(self):
        self.chain_calls = []
        self.bridge_calls = []
        self.worker_kwargs = None

    def make_chain(self, sid):
        self.chain_calls.append(sid)
        return Fake("vad", sid=sid), Fake("stt", sid=sid)

    def make_bridge(self, sid, target):
        self.bridge_calls.append((sid, target))
        return Fake("bridge", sid=sid, target=target)

    def make_worker(self, *, name, processors):
        self.worker_kwargs = {"name": name, "processors": processors}
        return Fake("worker")

    def build(self, sid="PA_1"):
        return build_listener(
            sid,
            make_chain=self.make_chain,
            make_bridge=self.make_bridge,
            make_worker=self.make_worker,
        )


class TestPipelineOrder(unittest.TestCase):
    def test_detection_comes_before_recognition_and_the_bridge_is_last(self):
        h = Harness()
        h.build()
        kinds = [p.kind for p in h.worker_kwargs["processors"]]
        self.assertEqual(
            kinds,
            ["vad", "stt", "bridge"],
            "segmented recognisers emit nothing without VAD boundaries, so a "
            "reversed pipeline goes quiet rather than failing loudly",
        )


class TestPerParticipantWiring(unittest.TestCase):
    def test_the_chain_is_built_for_that_participant(self):
        """Handlers bound to the wrong sid attribute speech to the wrong person."""
        h = Harness()
        h.build("PA_7")
        self.assertEqual(h.chain_calls, ["PA_7"])

    def test_each_participant_gets_their_own_pieces(self):
        h = Harness()
        h.build("PA_1")
        first = h.worker_kwargs["processors"]
        h.build("PA_2")
        second = h.worker_kwargs["processors"]
        self.assertNotEqual(
            [id(p) for p in first],
            [id(p) for p in second],
            "sharing a recogniser between participants is the bug this "
            "architecture exists to remove",
        )

    def test_the_worker_name_identifies_the_participant(self):
        h = Harness()
        h.build("PA_7")
        self.assertEqual(h.worker_kwargs["name"], "listener-PA_7")


class TestBridgeTargeting(unittest.TestCase):
    """The 22,147-delivery lesson."""

    def test_the_bridge_is_aimed_at_the_responder(self):
        h = Harness()
        h.build("PA_1")
        sid, target = h.bridge_calls[0]
        self.assertEqual(sid, "PA_1")
        self.assertEqual(
            target,
            RESPONDER_WORKER,
            "an untargeted bridge makes every worker echo every other one",
        )

    def test_the_responder_name_is_a_single_constant(self):
        """Two spellings of the responder's name would route transcripts into
        the void, silently."""
        self.assertTrue(RESPONDER_WORKER)
        self.assertEqual(RESPONDER_WORKER, RESPONDER_WORKER.strip())


if __name__ == "__main__":
    unittest.main()
