"""Owns the lifecycle of per-participant listener workers.

Thin by design. Policy — who gets a worker, how many may exist, what to tear
down — lives in participant_workers.py as pure functions; this asks and acts.
The split is deliberate: lifecycle code is awkward to test and easy to get
subtly wrong, so it should contain as few decisions as possible.

The worker factory is injected rather than imported. That keeps the pool
ignorant of what a listener pipeline contains, so recognition, voice-activity
detection or transport wiring can all change without touching lifecycle, and so
these paths can be tested against fakes in milliseconds.
"""
from loguru import logger

from participant_workers import route_audio, teardown_for


class ParticipantWorkerPool:
    """Creates a listener worker per participant, and refuses past the cap.

    Args:
        create_worker: ``(sid, name) -> worker``. Builds, does not start.
        start_worker: ``async (worker) -> None``. Registers it on the bus.
        cap: Optional override of the participant ceiling.
    """

    def __init__(self, *, create_worker, start_worker, cap: int | None = None):
        self._create = create_worker
        self._start = start_worker
        self._cap = cap
        self._workers: dict[str, object] = {}   # worker name -> worker
        self._by_sid: dict[str, str] = {}       # participant sid -> worker name
        self.refused: set[str] = set()

    @property
    def names(self) -> set[str]:
        return set(self._workers)

    async def handle_audio(self, sid: str | None):
        """Return the worker for this participant, building one if needed.

        None means the frame should be dropped — no identity yet, or the room is
        at capacity.
        """
        decision = route_audio(sid, self.names, cap=self._cap)
        if decision is None:
            if sid:
                # Recorded and logged once per participant. A refusal nobody can
                # see is the failure mode this cap exists to prevent.
                if sid not in self.refused:
                    self.refused.add(sid)
                    logger.warning(
                        f"Participant {sid} refused: at capacity "
                        f"({len(self._workers)} workers). Their audio is not "
                        f"being transcribed."
                    )
            return None

        if not decision.create:
            return self._workers[decision.worker]

        worker = self._create(sid, decision.worker)
        self._workers[decision.worker] = worker
        self._by_sid[sid] = decision.worker
        await self._start(worker)
        logger.info(f"Listener worker started for {sid} ({len(self._workers)} active)")
        return worker

    async def handle_leave(self, sid: str) -> None:
        """Shut down a departing participant's worker and free the slot."""
        name = teardown_for(sid, self.names)
        if name is None:
            return

        worker = self._workers.pop(name, None)
        self._by_sid.pop(sid, None)
        self.refused.discard(sid)

        if worker is None:
            return
        try:
            await worker.end(reason="participant left")
        except Exception as e:
            # The slot is released above, before this can fail. A worker that
            # raises on shutdown must not hold capacity forever — that turns one
            # bad session into a room that shrinks for the rest of its life.
            logger.warning(f"Listener worker for {sid} failed to stop cleanly: {e}")
        logger.info(f"Listener worker stopped for {sid} ({len(self._workers)} active)")
