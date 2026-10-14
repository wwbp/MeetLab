"""Realistic conversations for load testing, and honest scoring of the replies.

Replaces the single question the old soak harness looped forever. That harness
published "What is the capital of France?" on repeat from a microphone that never
stopped transmitting, never subscribed to the bot's audio, and scored a run on
whether the HTTP call starting the bot returned 200. On 2026-08-20 it reported
"40/40 bots started, 0 errored rooms" for a run in which the bot said nothing
but its opening greeting.

Two design rules come out of that.

**Replies are the measurement.** A run where the bot does not speak is a failed
run, no matter how much audio was pushed at it. Latency is only meaningful
alongside the proportion of turns that got an answer at all — reporting the
average wait of the few turns that succeeded is precisely how a broken run looks
excellent.

**The conversation has to resemble one.** Repeating one short question produces
one-word answers, exercises almost no language-model or synthesis work, and never
fills a context window. These scripts change topic, ask follow-ups that only make
sense given the previous answer, and vary in length the way speech does.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Turn:
    """One thing a simulated participant says.

    Attributes:
        text: What they say. Rendered to audio by scripts/generate-conversation-audio.
        follow_up: True when this only makes sense given the previous answer, so
            it tests that context survived rather than just that STT works.
        expect_reply_within_s: How long the bot has to start speaking before this
            turn counts as unanswered.
        pause_after_s: Silence before the next turn. Real participants think.
    """

    text: str
    follow_up: bool = False
    expect_reply_within_s: float = 8.0
    pause_after_s: float = 3.0


CONVERSATIONS: list[list[Turn]] = [
    [
        Turn("What should we prioritise for the leadership role this quarter?"),
        Turn("Why does that matter more than the others?", follow_up=True),
        Turn("Candidate A has stronger follow through but candidate B is more popular with the team."),
        Turn("Which of those two would you pick?", follow_up=True),
        Turn("Alright. Can you summarise where we landed?", follow_up=True, pause_after_s=4.0),
    ],
    [
        Turn("We are seeing higher latency on the transcription service since Tuesday."),
        Turn("Could that be caused by the batch size change?", follow_up=True),
        Turn("What would you check first to narrow it down?", follow_up=True),
        Turn("Suppose the GPU is fine and the queue is empty. What then?", follow_up=True),
        Turn("Thanks, that helps."),
    ],
    [
        Turn("Can you explain what a retrieval augmented generation system does?"),
        Turn("How is that different from just fine tuning a model?", follow_up=True),
        Turn("Give me a concrete example where retrieval is the better choice.", follow_up=True),
        Turn("What tends to go wrong with those in practice?", follow_up=True, pause_after_s=5.0),
    ],
    [
        Turn("I want to plan a two week trip to Japan in spring."),
        Turn("We would rather avoid the busiest cherry blossom weeks."),
        Turn("Which cities would you put on that list?", follow_up=True),
        Turn("How many days would you give Kyoto?", follow_up=True),
        Turn("And is that enough time to get out to the mountains as well?", follow_up=True),
    ],
    [
        Turn("Our onboarding drop off is worst on the second day."),
        Turn("What are the usual reasons for that pattern?", follow_up=True),
        Turn("We already send a reminder email on day two."),
        Turn("So what would you change instead?", follow_up=True, pause_after_s=4.0),
        Turn("Okay, and how would we measure whether it worked?", follow_up=True),
    ],
]


def conversation_for(room_index: int) -> list[Turn]:
    """The script for one room. Deterministic, so runs stay comparable.

    Rooms cycle through the set rather than picking at random: a run that cannot
    be repeated cannot be compared with the last one, and comparing runs is the
    entire purpose of a load test.
    """
    return CONVERSATIONS[room_index % len(CONVERSATIONS)]


@dataclass
class ReplySummary:
    """How many turns got an answer, and how long they waited."""

    expected: int
    replied: int
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def reply_rate(self) -> float:
        return self.replied / self.expected if self.expected else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0


def summarise_replies(
    pairs: list[tuple[float, float | None]], *, expected: int
) -> ReplySummary:
    """Score a room's turns.

    Args:
        pairs: (turn_ended_at, first_bot_audio_at) per turn actually spoken.
            None means no audio ever arrived — the bot did not answer.
        expected: How many turns the script intended. Scoring against this rather
            than against the turns that happened means a room which died halfway
            is not flattered by only being marked on what it managed.

    Latency is collected only from answered turns, because an unanswered turn has
    no latency — but the unanswered ones still count against the reply rate, so
    the two numbers have to be read together. Dropping misses from the average
    while ignoring the rate is how a run in which the bot said nothing reports
    excellent response times.
    """
    latencies = [
        (start_audio - turn_end) * 1000.0
        for turn_end, start_audio in pairs
        if start_audio is not None
    ]
    return ReplySummary(expected=expected, replied=len(latencies), latencies_ms=latencies)


# The reply rate below which a run is considered a failure rather than a slow
# success. Deliberately high: at 20 concurrent rooms the bot answered essentially
# every turn, so anything much below that is a regression, not natural variance.
MIN_REPLY_RATE = 0.90

# Mean response time the team publishes, so it is the number the harness enforces.
MAX_MEAN_LATENCY_MS = 800.0


def reply_verdict(*, reply_rate: float, mean_latency_ms: float) -> tuple[bool, str]:
    """Pass or fail a run, reply rate first.

    Order matters. A run where the bot rarely answered must fail on that, not on
    the latency of the few turns it managed — the old harness's failure mode was
    reporting healthy numbers for a system that was effectively mute.
    """
    if reply_rate < MIN_REPLY_RATE:
        return False, (
            f"bot replied to only {reply_rate:.0%} of turns "
            f"(need {MIN_REPLY_RATE:.0%}) — the run is a failure regardless of latency"
        )
    if mean_latency_ms > MAX_MEAN_LATENCY_MS:
        return False, (
            f"mean latency {mean_latency_ms:.0f}ms exceeds the "
            f"{MAX_MEAN_LATENCY_MS:.0f}ms target"
        )
    return True, f"replied to {reply_rate:.0%} of turns, mean {mean_latency_ms:.0f}ms"
