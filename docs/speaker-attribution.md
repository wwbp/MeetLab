# Speaker attribution: who said what when people talk at once

When two or three people speak over each other, each person's words must be stored under
their own name. This page says how MeetLab keeps them apart, what went wrong before
November 2026 (finding F11), and what is still imperfect.

Check it with `make sim-attribution`: three simulated people (a, b, c) in one room, each
saying a different sentence in a different voice, started 0.15 s apart. `OFFSET_S=3` spaces
the starts further apart; `SPEAKERS=a` is the one-speaker control.

## How speech travels, person by person

| Step | Where | Kept apart per person? |
|---|---|---|
| Each person publishes their own microphone track | LiveKit | yes |
| The transport hands the bot one audio stream per participant (`user_id` = identity) | Pipecat LiveKit transport | yes |
| Each person gets their own VAD and speech-to-text chain | `services/bot/meetlab_bot/multi_speaker_stt.py` (`MultiSpeakerSTT._ensure_stt`) | yes |
| Each transcript is labelled "name: words" for the LLM | `SpeakerLabelInjector`, same file | yes |
| **One** user aggregator collects transcripts into a user turn | Pipecat `LLMUserAggregator` (`bot.py`, `context_aggregator.user()`) | **no** |
| The turn is stored | `on_user_turn_stopped` in `services/bot/meetlab_bot/bot.py` | yes, since F11's fix |

## What went wrong (F11)

Recognition was always right per person; the merge happened afterwards. Pipecat has one
user aggregator per pipeline, and every transcript that arrives before its end-of-turn wait
(`user_speech_timeout_ms`, 50 ms by default) runs out joins the same turn. The turn was then
stored as one utterance under whoever was transcribed **last** (`_current_speaker_sid`), with
only that person's label taken out. Before the fix the database held, for room `sim-attr-6318d5`:

```
sim_c_21a5 | sim_b_3bdc: Are we seeing higher latency on the transcription service since Tuesday? Can you explain what a retrieval augmented generation system does?
```

b's and c's sentences, each recognised correctly, in one row of c's. A 50 ms window still merges
because the transcripts arrive together: the three people's segments go to the shared speech
server at about the same time and come back in the same millisecond (bot log of the
2026-11-02 run: VAD stops at 44.0, 45.4 and 45.5 s; all three transcripts at 48.162 s).

## The fix

`split_turn` (`bot.py`) cuts the committed turn back into pieces at the labels the injector put
in, and each piece is stored as its own utterance under its own speaker. The LLM still reads
the turn as one message, with every piece labelled, so it knows who asked what. Test:
`TestSplitTurn` in `services/bot/tests/test_bot.py`, which feeds the stored text above.

After the fix the same simulation passes: one turn held all three people's words, logged as
`user turn held 3 speakers' words: stored as one utterance each`, and stored as three rows.
With `OFFSET_S=3` the three transcripts came back 2 to 4 s apart and formed three turns of
their own, no merge at all: how much overlap merges depends on when transcripts return, not
on when people speak.

## What is still imperfect

- **Order inside a merged turn is the order recognition finished, not the order people
  started.** In the run above the turn read c, a, b although a started first. The pieces get
  the turn's start time plus 1 ms each, so a transcript of an overlap may list speakers in an
  order they did not speak in.
- **Everyone in a merged turn gets one reply.** The bot answers all of them at once; it
  cannot yet answer one person and hold the other's question.
- **Two people with the same display name** cannot be told apart in a merged turn: the label
  is the display name, so both pieces go to one of them.
- **A person who says "Alice: " out loud**, with the colon recognised, would split their own
  sentence there. Speech recognisers rarely write that colon.
- **One microphone, two people** (a shared laptop) is one LiveKit track: they are one speaker
  to MeetLab. Telling them apart needs diarisation, which MeetLab does not do.

## Options, if these matter for a study

1. **Store from the transcripts, not the turn.** Record each person's transcript with its own
   VAD start time as it leaves their chain (`_FrameCollector` in `multi_speaker_stt.py`),
   which fixes the order and timestamps. The LLM turn stays as it is.
2. **One aggregator per person.** Each person's chain ends its own turn, and the bot decides
   which turn to answer. This is the larger change the per-participant worker design
   aimed at (`participant_workers.py`); Pipecat 1.12's aggregator has one turn at a time.
3. **Upstream in Pipecat:** let `LLMUserAggregator` keep `user_id` per aggregated part so the
   `on_user_turn_stopped` message carries who said each piece, instead of re-parsing labels.
