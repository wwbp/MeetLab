"""Fans one room's audio out to per-participant listener workers.

LiveKitTransport hardcodes ``RoomOptions(auto_subscribe=True)``, so a worker
cannot subscribe to a single participant — one transport holds the room and
emits ``UserAudioRawFrame(user_id=...)`` for everyone in it. The fan-out has to
happen one hop later, and this is that hop.

It replaces the demultiplexing currently buried inside multi_speaker_stt.py.
The difference is not the routing, which is a dictionary lookup either way, but
where the audio ends up: today every participant shares one pipeline, which is
what starves Pipecat's own turn-taking and interruption paths and forced us to
rebuild both by hand. Here each participant's audio lands in its own worker with
its own pipeline, so those paths see what they expect.

Two rules govern this class, because it sits in front of everything else on the
live audio path:

  * audio is **consumed**, never also forwarded — otherwise every sample is
    processed twice
  * everything else passes through untouched, and nothing here raises; a
    swallowed lifecycle frame stalls the session and an exception ends it
"""
from loguru import logger

from pipecat.frames.frames import Frame, UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class ParticipantAudioRouter(FrameProcessor):
    """Routes each participant's audio to their own worker.

    Args:
        pool: A :class:`ParticipantWorkerPool`. Owns worker lifecycle and the
            decision to refuse; this class only moves frames.
    """

    def __init__(self, pool):
        super().__init__()
        self._pool = pool

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        is_participant_audio = (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, UserAudioRawFrame)
        )
        if not is_participant_audio:
            await self.push_frame(frame, direction)
            return

        # From here the frame belongs to a participant worker, so it does not
        # continue downstream — including when it is dropped. Letting refused or
        # unattributed audio through would feed the very pipeline this design
        # exists to keep single-purpose.
        try:
            worker = await self._pool.handle_audio(frame.user_id)
        except Exception as e:
            logger.warning(f"participant routing failed for {frame.user_id}: {e}")
            return

        if worker is None:
            return

        try:
            await worker.queue_frame(frame, FrameDirection.DOWNSTREAM)
        except Exception as e:
            # One participant's broken worker costs that participant, not the
            # meeting.
            logger.warning(f"listener worker for {frame.user_id} rejected audio: {e}")

    async def participant_left(self, sid: str) -> None:
        """Release a departing participant's worker.

        Called from the transport's disconnect handler rather than driven by a
        frame, because departure is not something that arrives in the stream.
        """
        try:
            await self._pool.handle_leave(sid)
        except Exception as e:
            logger.warning(f"listener teardown failed for {sid}: {e}")
