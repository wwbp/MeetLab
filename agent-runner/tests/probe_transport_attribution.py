"""Does Pipecat's LiveKit input tag each participant's audio with the right id? (F11, layer 1)

Two participants play different pure tones at the same time (440 Hz and 1000 Hz). A
minimal pipeline built on the bot's own LiveKitTransport (STOCK=1: Pipecat's) collects the audio frames;
each participant's frames must carry their own tone. No speech recognition involved.

    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \\
        uv run python tests/probe_transport_attribution.py      # ALONE=1: one-participant control
"""
import asyncio
import os
import sys
from collections import defaultdict
from uuid import uuid4

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

TONES = {"probe_low": 440.0, "probe_high": 1000.0}
if os.environ.get("ALONE"):  # control: one participant, nobody overlapping
    TONES = {"probe_low": 440.0}
SECONDS = 6


def dominant_hz(pcm: bytes, sample_rate: int) -> float:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / sample_rate)[int(np.argmax(spectrum[1:])) + 1])


def crossed(frames: dict[str, list[float]], expected: dict[str, float], tolerance: float = 30) -> dict[str, float]:
    """Per participant: the share of their windows carrying another participant's tone,
    which a clean input can never produce (silence and edges only add noise)."""
    return {who: sum(any(abs(hz - other) <= tolerance for o, other in expected.items() if o != who) for hz in hzs) / len(hzs)
            for who, hzs in frames.items() if hzs}


async def main() -> int:
    from livekit import rtc
    from pipecat.frames.frames import Frame, UserAudioRawFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.transports.livekit.transport import LiveKitParams
    if os.environ.get("STOCK"):  # Pipecat's own transport, for comparison
        from pipecat.transports.livekit.transport import LiveKitTransport
    else:  # the bot's
        from livekit_input import LiveKitTransport

    from _sim_common import LIVEKIT_URL, token

    room_name = f"probe-{uuid4().hex[:6]}"
    transport = LiveKitTransport(url=LIVEKIT_URL, token=token(room_name, "bot_probe"), room_name=room_name,
                                 params=LiveKitParams(audio_in_enabled=True, audio_out_enabled=False))
    heard: dict[str, list[float]] = defaultdict(list)
    pending: dict[str, bytes] = defaultdict(bytes)

    class Collect(FrameProcessor):
        """Pools each id's 10 ms frames into 100 ms windows, then measures the tone."""

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, UserAudioRawFrame):
                pending[frame.user_id] += frame.audio
                window = frame.sample_rate // 10 * 2  # 100 ms of 16-bit samples
                while len(pending[frame.user_id]) >= window:
                    chunk, pending[frame.user_id] = pending[frame.user_id][:window], pending[frame.user_id][window:]
                    heard[frame.user_id].append(dominant_hz(chunk, frame.sample_rate))
            await self.push_frame(frame, direction)

    pipeline = Pipeline([transport.input(), Collect()])
    try:  # Pipecat >= 1.3: PipelineWorker + WorkerRunner (PipelineTask is removed in 2.0)
        from pipecat.pipeline.worker import PipelineWorker
        from pipecat.workers.runner import WorkerRunner

        workers = WorkerRunner(handle_sigint=False)
        await workers.add_workers(PipelineWorker(pipeline))
        runner = asyncio.create_task(workers.run())
        stop = workers.cancel
    except ImportError:  # the bot's current 1.4 path
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineTask

        task = PipelineTask(pipeline)
        runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
        stop = task.cancel
    await asyncio.sleep(3)

    async def play(identity: str, hz: float) -> rtc.Room:
        room = rtc.Room()
        await room.connect(LIVEKIT_URL, token(room_name, identity))
        rate, per = 48000, 480
        source = rtc.AudioSource(rate, 1)
        await room.local_participant.publish_track(
            rtc.LocalAudioTrack.create_audio_track("tone", source),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        await asyncio.sleep(1)
        t = np.arange(per) / rate
        for i in range(SECONDS * 100):
            frame = rtc.AudioFrame.create(rate, 1, per)
            np.copyto(np.frombuffer(frame.data, dtype=np.int16),
                      (8000 * np.sin(2 * np.pi * hz * (t + i * per / rate))).astype(np.int16))
            await source.capture_frame(frame)
        return room

    rooms = await asyncio.gather(*(play(who, hz) for who, hz in TONES.items()))
    await asyncio.sleep(1)
    # Pipecat 1.4 tags audio with the session id, 1.8+ with the identity; name both.
    name = {r.local_participant.sid: r.local_participant.identity for r in rooms}
    name.update({who: who for who in TONES})
    await stop()
    await runner
    for room in rooms:
        await room.disconnect()

    by_identity = {name.get(pid, pid): hzs for pid, hzs in heard.items()}
    shares = crossed({k: v for k, v in by_identity.items() if k in TONES}, TONES)
    for who in TONES:
        print(f"  {who}: {len(by_identity.get(who, []))} windows of 100 ms, {shares.get(who, 0):.0%} carry another participant's tone")
    bad = [who for who in TONES if not by_identity.get(who) or shares.get(who, 0) > 0]
    print("PASS no participant's id carries another's audio" if not bad else f"FAIL crossed or missing: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
