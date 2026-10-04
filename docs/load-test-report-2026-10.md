# MeetLab v2 load and quality report — October 2026

**v2 staging held 84 concurrent rooms (about 140 people) inside every rule, and answered every
person up to 102 rooms (about 170 people) with no loss of quality.** Past 84 rooms the slowest
1 in 20 replies took just over 2 seconds (2.01–2.05 s). Our own model stack matches v1's paid vendors on quality and is
faster, and one prompt line raised judged quality from 3.4–3.9 to 4.5 out of 5.

Runs of 2026-10-03 and 04 on staging. How the tests work and what each number means:
[load-testing.md](load-testing.md). Raw numbers: `infra/v2/LEDGER.md` → Measurements.

## Capacity: 60 rooms passed

A `breakpoint` test added 4 rooms every 5 minutes, from 4 to 60. Each room ran an everyday
conversation from the library, with 1, 2 or 3 people (1.7 on average) and the bot.

| Rooms (people) | Turns in the step | Answered | Typical reply time (p50) | Slowest 1 in 20 (p95) |
|---|---|---|---|---|
| 4 (~7) | 103 | 100% | 1.27 s | 1.62 s |
| 24 (~41) | 731 | 100% | 1.42 s | 1.77 s |
| 40 (~68) | 1,220 | 100% | 1.48 s | 1.88 s |
| **60 (~102)** | **1,823** | **100%** | **1.54 s** | **1.99 s** |

Every session closed by itself afterwards. Bots joined in 5–8 seconds, or about 40 seconds
when a machine took its first bot after a deploy (it downloads the bot's software first).

**Where the next limits are**, from how each part grew with load:

| Part of staging | At 60 rooms | Limit expected around | Way to raise it |
|---|---|---|---|
| Reply time (the 2 s rule) | 1.99 s | just past 60 rooms | Speak at the first clause instead of the first full sentence; a second voice GPU (the voice's start time grew most: 116 → 260 ms) |
| LiveKit media server (one c6i.large) | 57% CPU | ~100 rooms | A larger machine, or a second server (needs Redis) |
| Database connections | 101 of ~180 | ~100 rooms | A connection pooler or a larger database |
| Bot machines | 61% CPU | not near | — |

## A study launch, and past 60 rooms (2026-10-04)

With extra headroom for the session (a bigger LiveKit machine and database, two voice GPUs):

| Test | Rooms | Answered | Typical / slowest 1 in 20 | Notes |
|---|---|---|---|---|
| **Study launch:** every room starting within 30 seconds | 50 | 100% | 1.46 / 1.90 s | Passed. Bots took up to ~44 s to join (fresh machines download the bot first); **meet was busy (63% CPU)** for the burst |
| **Climb:** +6 rooms every 3 minutes | **72 (~122 people)** | 100% | 1.52 / 1.97 s | The largest valid step. A second voice GPU kept the voice's start time at 176 ms |
| Climb | 78 | 95% | 1.50 / 2.03 s | Not valid: **our load generator** could no longer keep up (its synthetic people stuttered), not staging |

### 100 rooms, from two load generators (2026-10-04, run 37236394995)

The same climb, now from two load generators (each runs half the rooms) so the test itself
can't be the bottleneck:

| Rooms | Answered | Typical / slowest 1 in 20 | Verdict |
|---|---|---|---|
| 60 | 100% | 1.51 / 1.93 s | PASS |
| 72 | 99% | 1.54 / 1.96 s | PASS |
| **84 (~140 people)** | 100% | 1.55 / **2.00 s** | **PASS: the capacity** |
| 90 | 100% | 1.56 / 2.02 s | over the 2 s rule |
| 102 (~170 people) | 99% | 1.57 / 2.05 s | over the 2 s rule |

- **What slows down:** the models' queues, not a machine running out. From 6 to 102 rooms
  the LLM's first word went 117 → 198 ms and the voice's first sound 108 → 220 ms; speech to
  text stayed at ~367 ms. The GPUs themselves stayed under 15% CPU.
- **Quality held all the way:** word error rate 2.3–2.9%, judged overall 3.9–4.2 out of 5,
  replies ~9 words. The voice: 4.5% heard back wrong, naturalness 4.35.
- **meet hit 106% CPU** at 102 rooms (it shares a small machine with the runner): the next
  thing to give for bigger studies.
- The test was valid at every step (neither load generator fell behind), and every session
  closed afterwards.

To go past ~85 rooms inside the 2 s rule: a third voice GPU or a faster LLM GPU (the
latency work, B4), and a bigger machine for meet.

## Our stack against v1's

The same 3-room test, the same recorded speech, and the same speech-to-text (our Parakeet
server) for both. "ours" is Qwen 2.5 7B (8-bit) for answers and Kokoro for the voice, on our
own GPUs; "v1" is OpenAI's gpt-5.4-nano and ElevenLabs.

| | ours | v1 | ours + short replies |
|---|---|---|---|
| Passed every step | yes | no (2.02 s at 2 rooms) | no (2.08–2.11 s: twice the turns) |
| Typical / slowest reply time | 1.3 s / 1.5–1.8 s | 1.5 s / 1.7–2.0 s | 1.3 s / 1.6–2.1 s |
| The LLM starts answering after | **~0.1 s** | ~0.75 s | ~0.1 s |
| Words the bot heard wrong | 0.7–4.3% | 0.5–3.5% | 0–1.9% |
| Judged: answers the question (1–5) | 4.0–4.2 | 4.2–4.8 | 4.3–4.5 |
| Judged: suits being heard (1–5) | 2.4–3.1 | 2.3–2.7 | **4.8–4.9** |
| Judged: overall (1–5) | 3.4–3.9 | 3.2–4.0 | **4.5** |
| Typical reply length | 46–61 words | 74–89 words | 13–21 words |
| Voice: words a listener misses | 5.9% | 4.7% | 0.4% |
| Voice: naturalness (1–5, predicted) | 4.46 | 3.90 | 4.44 |

**What this means:**
1. Our stack answers about 8× sooner and is otherwise on a par with v1, at no per-turn cost.
2. Both stacks talked too long for a spoken conversation. One added sentence, *"Keep each
   reply to one or two short sentences, the way people speak in conversation."*, fixed it
   and raised every judged score. Short replies also mean more turns per minute, so more
   load per room; the capacity test above used them.
3. Kokoro was rated more natural than ElevenLabs. The naturalness model was trained on human
   speech, so a small human-rated sample should confirm it before we rely on it.

## Problems the tests found, and what happened

| Problem | Effect on a real session | Status |
|---|---|---|
| Long conversations outgrew the LLM's memory (8,192 tokens) and the bot went silent, staying in the room | A study session ~40 minutes in would lose its bot without anyone noticing | **Fixed** (#155): older turns are summarised; a broken LLM ends the session visibly |
| People's stored words began with their speaker label ("load_000: Why does…") | Every transcript researchers download had it | **Fixed** (#145) |
| The voice's timing was filed as the LLM's | Latency diagnostics pointed at the wrong part | **Fixed** (#141) |
| Changing a GPU service could never finish deploying (and the speech server re-built for 20 minutes on every restart) | A model change would silently never arrive | **Fixed** (#143, #144, #146) |
| A push to a pull request could cancel a waiting deploy | Merged changes might not reach staging | **Fixed** (#140) |
| About 1 sentence in 5 is stored as 2+ turns when the speaker pauses, and the bot answers the pieces | The pilot's "chained answers"; double replies; more load | **Open**: the next quality target |
| A room's first sentence reaches the LLM without the speaker's name | In a group, the bot can't tell who spoke first | **Open** |
| A machine's first bot after a deploy joins in ~40 s instead of 5–8 s | A participant may wait ~40 s for the bot | **Open**: pull the software when preparing for a study |

## How much it cost

Staging costs about $180 a month always on. The GPUs (speech, LLM, voice) cost about $2.50
an hour together and are switched off between test runs. The 60-room run cost about $6.

## What these results don't show yet

- **One run each.** Numbers move from run to run; repeat a run before a decision rests on a
  small difference.
- **Synthetic people.** The speech is computer-generated (20 voices) and follows scripts;
  real people interrupt, mumble and talk over each other.
- **The real limit.** 60 rooms passed; the next test should go past it (to ~100) to find
  which limit above comes first.
- **Speech-to-text was the same for every stack**, our Parakeet server, so the comparison
  is about answers and voice only.
