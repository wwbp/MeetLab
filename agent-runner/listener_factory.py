"""Assembles one participant's listener worker.

A listener is the small pipeline that turns one person's audio into transcripts:
voice-activity detection, then speech recognition, then a bridge onto the bus
where the responder waits. It replaces the per-participant chains that
multi_speaker_stt.py builds and then demultiplexes inside a single pipeline.

The parts already exist and are tested elsewhere. This module owns only the
assembly, because two things about the assembly are easy to get wrong and quiet
when wrong:

**Order.** The recognisers in use are segmented — they transcribe what falls
between VAD boundaries and emit nothing at all without them. A reversed pipeline
therefore produces silence, not an error.

**Targeting.** The bridge must be aimed at the responder. The spike that
validated this architecture first used a bridge accepting from every peer, and
three utterances produced 22,147 deliveries as the workers echoed each other.
Targeting gave exactly three. One line, and the difference between working and
unusable.

Constructors are injected rather than imported so assembly can be tested without
building Silero and a recognition client, and so the pipeline's contents can
change without touching this file.
"""
from participant_workers import worker_name

# The single name every listener bridges to. One spelling, in one place: two
# spellings would route transcripts nowhere and report no error while doing it.
RESPONDER_WORKER = "responder"


def build_listener(sid: str, *, make_chain, make_bridge, make_worker):
    """Build the listener worker for one participant.

    Args:
        sid: The participant's LiveKit SID.
        make_chain: ``(sid) -> (vad, stt)``. Built per participant so the VAD's
            speech-onset handlers are bound to this speaker — handlers bound to
            the wrong sid attribute speech to the wrong person.
        make_bridge: ``(sid, target) -> processor``. Bridges transcripts onto the
            bus, aimed at ``target``.
        make_worker: ``(*, name, processors) -> worker``.

    Returns:
        The worker, not yet started. The pool decides when it runs.
    """
    vad, stt = make_chain(sid)
    bridge = make_bridge(sid, RESPONDER_WORKER)
    return make_worker(name=worker_name(sid), processors=[vad, stt, bridge])
