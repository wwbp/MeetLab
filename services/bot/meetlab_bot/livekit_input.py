"""Pipecat's LiveKit transport with one resampler per participant (diagnosis F11).

Pipecat's LiveKit input (1.4 through 1.12) converts every participant's audio from
48 kHz to the pipeline's rate through one shared stream resampler. A stream
resampler keeps filter history between calls, so with two people speaking at once
each output frame is built partly from the other person's samples: 60-64% of each
speaker's audio carried the other's voice (tests/probe_transport_attribution.py),
0% when alone. Speech recognition then attributed one person's words to another.

Remove when Pipecat resamples per participant upstream (pipecat-ai/pipecat#6033).
"""
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import UserAudioRawFrame
from pipecat.transports.livekit.transport import LiveKitInputTransport
from pipecat.transports.livekit.transport import LiveKitTransport as _PipecatLiveKitTransport


class _PerParticipantInput(LiveKitInputTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resamplers = {}  # participant id -> its own stream resampler

    async def _audio_in_task_handler(self):
        async for audio_data in self._client.get_next_audio_frame():
            if not audio_data:
                continue
            event, participant_id = audio_data
            if participant_id not in self._resamplers:
                self._resamplers[participant_id] = create_stream_resampler()
            frame = event.frame
            audio = await self._resamplers[participant_id].resample(
                frame.data.tobytes(), frame.sample_rate, self.sample_rate)
            if audio:
                await self.push_audio_frame(UserAudioRawFrame(
                    user_id=participant_id, audio=audio,
                    sample_rate=self.sample_rate, num_channels=frame.num_channels))


class LiveKitTransport(_PipecatLiveKitTransport):
    def input(self) -> LiveKitInputTransport:
        if not self._input:
            self._input = _PerParticipantInput(self, self._client, self._params, name=self._input_name)
        return self._input
