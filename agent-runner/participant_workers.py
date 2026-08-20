"""Policy for per-participant listener workers: who gets one, and how many.

Pipecat's worker model can express a multi-party meeting natively — several
listener workers feeding one responder over a shared bus, each with its own
pipeline and its own speech recognition. What the framework does not decide is
policy, and policy is where this system has been hurt:

  * **Which worker owns a frame.** Today one pipeline demultiplexes every
    participant internally (multi_speaker_stt.py), which is why turn-taking and
    interruption had to be rebuilt by hand — the shared pipeline starves
    Pipecat's own paths of the audio they expect.

  * **When to refuse.** The 2026-08-19 load test accepted eight concurrent
    rooms, exhausted speech-recognition throughput, and stopped replying rather
    than declining: replies fell from 261 to 23 while the processor sat at 77%.
    Silent degradation is worse than refusal, because refusal is visible at the
    edge and degradation is only visible in a log nobody is reading.

Everything here is pure. The shell that owns real workers reads these decisions
and acts on them, so the policy can be tested exhaustively without a bus, a
transport, or a second of audio.
"""
from dataclasses import dataclass

# How many participants one runner will carry before refusing new ones.
#
# Sized from the 2026-08-19 production ramp rather than guessed: five concurrent
# one-participant sessions held a 500ms median response at 64% CPU, and eight
# collapsed. Participants and sessions cost roughly the same here — each needs
# its own voice-activity detection and recognition stream — so the ceiling is
# expressed in participants, which is the thing actually being admitted.
#
# Deliberately conservative. Raising it is a decision that should follow a
# measurement, and lowering it is the fastest lever if a room starts to struggle.
MAX_PARTICIPANT_WORKERS = 6


@dataclass(frozen=True)
class Routing:
    """Where a participant's audio goes, and whether that worker exists yet."""

    worker: str
    create: bool


def worker_name(sid: str) -> str:
    """Stable worker name for a participant.

    Stability matters more than readability: if the same participant ever mapped
    to two names, the second frame would build a second pipeline and the first
    would linger holding a recognition stream.
    """
    return f"listener-{sid}"


def route_audio(sid: str | None, existing: set[str], *, cap: int | None = None) -> Routing | None:
    """Decide which worker handles this participant's audio.

    Returns None when the frame should be dropped: either it carries no
    participant identity, or admitting a new participant would exceed the cap.

    The cap applies to *admission only*. Someone already in the meeting keeps
    their worker even at the ceiling — throttling a conversation that is running
    fine, in order to protect capacity, would break the very thing being
    protected.
    """
    if not sid:
        return None

    name = worker_name(sid)
    if name in existing:
        return Routing(worker=name, create=False)

    limit = MAX_PARTICIPANT_WORKERS if cap is None else cap
    if len(existing) >= limit:
        return None

    return Routing(worker=name, create=True)


def teardown_for(sid: str, existing: set[str]) -> str | None:
    """The worker to shut down when a participant leaves, if they had one.

    Returning the name rather than mutating lets the caller decide when the
    worker is genuinely finished — a departing speaker may still have audio in
    flight worth flushing.
    """
    name = worker_name(sid)
    return name if name in existing else None
