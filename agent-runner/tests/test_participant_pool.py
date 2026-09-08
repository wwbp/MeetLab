"""The shell that turns routing decisions into real workers.

participant_workers.py decides policy; this owns lifecycle. Kept deliberately
thin — its whole job is to ask the pure functions what to do and then do it —
so that everything interesting stays testable without a bus or a transport.

The worker factory is injected, which is what lets these tests run in
milliseconds against fakes instead of standing up pipelines. It also keeps the
pool ignorant of what a listener worker actually contains, so the pipeline can
change without touching lifecycle.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from participant_pool import ParticipantWorkerPool


class FakeWorker:
    def __init__(self, name):
        self.name = name
        self.ended = False

    async def end(self, reason=None):
        self.ended = True


class Harness:
    """Records what the pool built and tore down."""

    def __init__(self, cap=None):
        self.built = []
        self.started = []
        self.pool = ParticipantWorkerPool(
            create_worker=self._create, start_worker=self._start, cap=cap
        )

    def _create(self, sid, name):
        w = FakeWorker(name)
        self.built.append(name)
        return w

    async def _start(self, worker):
        self.started.append(worker.name)


class TestAdmission(unittest.TestCase):
    def test_first_audio_builds_and_starts_a_worker(self):
        h = Harness()
        w = asyncio.run(h.pool.handle_audio("PA_1"))
        self.assertIsNotNone(w)
        self.assertEqual(h.built, ["listener-PA_1"])
        self.assertEqual(h.started, ["listener-PA_1"], "a built worker must run")

    def test_later_audio_reuses_the_same_worker(self):
        h = Harness()

        async def run():
            a = await h.pool.handle_audio("PA_1")
            b = await h.pool.handle_audio("PA_1")
            return a, b

        a, b = asyncio.run(run())
        self.assertIs(a, b)
        self.assertEqual(len(h.built), 1, "second frame must not build a second worker")

    def test_two_participants_get_two_workers(self):
        h = Harness()

        async def run():
            await h.pool.handle_audio("PA_1")
            await h.pool.handle_audio("PA_2")

        asyncio.run(run())
        self.assertEqual(sorted(h.built), ["listener-PA_1", "listener-PA_2"])

    def test_audio_without_identity_builds_nothing(self):
        h = Harness()
        self.assertIsNone(asyncio.run(h.pool.handle_audio("")))
        self.assertEqual(h.built, [])


class TestRefusal(unittest.TestCase):
    """Refusing is a feature. The 8-room collapse had no way to."""

    def test_a_participant_past_the_cap_is_refused(self):
        h = Harness(cap=2)

        async def run():
            await h.pool.handle_audio("PA_1")
            await h.pool.handle_audio("PA_2")
            return await h.pool.handle_audio("PA_3")

        self.assertIsNone(asyncio.run(run()))
        self.assertEqual(len(h.built), 2, "no worker should be built for a refusal")

    def test_refusal_is_observable(self):
        """Silent refusal repeats the original failure in a new place."""
        h = Harness(cap=1)

        async def run():
            await h.pool.handle_audio("PA_1")
            await h.pool.handle_audio("PA_2")

        asyncio.run(run())
        self.assertEqual(h.pool.refused, {"PA_2"})

    def test_existing_participants_are_unaffected_by_the_cap(self):
        h = Harness(cap=1)

        async def run():
            await h.pool.handle_audio("PA_1")
            await h.pool.handle_audio("PA_2")
            return await h.pool.handle_audio("PA_1")

        self.assertIsNotNone(asyncio.run(run()))


class TestDeparture(unittest.TestCase):
    def test_leaving_ends_the_worker(self):
        h = Harness()

        async def run():
            w = await h.pool.handle_audio("PA_1")
            await h.pool.handle_leave("PA_1")
            return w

        w = asyncio.run(run())
        self.assertTrue(w.ended)
        self.assertEqual(h.pool.names, set())

    def test_leaving_frees_a_slot(self):
        h = Harness(cap=1)

        async def run():
            await h.pool.handle_audio("PA_1")
            await h.pool.handle_leave("PA_1")
            return await h.pool.handle_audio("PA_2")

        self.assertIsNotNone(asyncio.run(run()), "a freed slot must be reusable")

    def test_leaving_twice_is_harmless(self):
        """Disconnect events are not guaranteed to arrive once."""
        h = Harness()

        async def run():
            await h.pool.handle_audio("PA_1")
            await h.pool.handle_leave("PA_1")
            await h.pool.handle_leave("PA_1")

        asyncio.run(run())
        self.assertEqual(h.pool.names, set())

    def test_a_failing_teardown_still_releases_the_slot(self):
        """A worker that raises on shutdown must not permanently consume
        capacity — that would turn one bad session into a shrinking room."""

        class Exploding(FakeWorker):
            async def end(self, reason=None):
                raise RuntimeError("teardown blew up")

        h = Harness(cap=1)
        h._create = lambda sid, name: Exploding(name)

        async def run():
            await h.pool.handle_audio("PA_1")
            await h.pool.handle_leave("PA_1")
            return await h.pool.handle_audio("PA_2")

        self.assertIsNotNone(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
