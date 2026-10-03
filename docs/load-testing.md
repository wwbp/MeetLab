# Load testing v2 — how much can it carry, and how well

A load test answers two questions: **how many meetings can staging hold at once**, and
**what does a participant experience while it does**. We answer them the same way for
every stack we might run (our own models, v1's paid vendors, anything later), so the
answers can be compared side by side.

## What a test does

Each test room is a real meeting. The console creates it, a synthetic participant joins
and talks, the console starts the bot, and the participant **listens**: a reply counts
only when the bot's voice actually arrives in the room. The participant speaks one of
five scripted conversations (24 recorded sentences, kept in
`agent-runner/tests/fixtures/conversations/`), waits for the bot to finish, pauses like a
person, and speaks again. The recordings never change, so every run hears the same audio.

The synthetic participants run inside AWS (a "load generator" task), not on a laptop, so
the numbers measure staging and not someone's home network.

## The six standard tests ("shapes")

| Shape | What it does | The question it answers |
|---|---|---|
| **smoke** | 1 room for 3 minutes | Does everything work at all? Run first, always. |
| **load** | Climbs to the expected number of rooms in 5 steps, then holds it 15 minutes | Does it work well at the size we expect? |
| **stress** | Steps from a quarter of the expected size to **double** it, 5 minutes per step | How does it degrade past our expectations? |
| **spike** | All rooms start within 30 seconds, held 5 minutes | What happens when a whole study opens at once? |
| **soak** | Climbs to the expected size and holds it for an **hour** | Does anything leak or wear out over time? |
| **breakpoint** | Adds a fifth of the expected size every 5 minutes, up to triple, and **stops at the first failing step** | What is the capacity? |

Every test ends with 2 minutes of no rooms, to check that every session closed by itself.

## What "passing" means

Every step of every test is judged by the same rules, whatever stack is under test:

| Rule | Threshold | Why |
|---|---|---|
| **Reply rate** | at least 95% of turns answered | A bot that skips turns is broken, however fast it is |
| **Response time (p95)** | at most 2 seconds from the end of a turn to the bot's first sound | Past 2 s a conversation feels broken (see `performance-tests.md`) |
| **Start errors** | none | Every room asked for a bot gets one |
| **Disconnects** | none | No participant is dropped |

"p95" means 19 turns in 20 were at least this fast. We also report p50 (the typical
turn), p99 (the worst 1 in 100), and how long bots took to join (join p95).

The **capacity** of a stack is the largest step that passed. After the run, any session
still marked running counts as a failure: something didn't close.

If the load generator itself falls behind (its participants can't speak in real time), the
result says **HARNESS OVERLOADED** and its numbers are not trusted. Rerun with more CPU.

## Quality: is it still good while it is fast?

Speed alone can mislead: a faster model or a busier server can hear worse or answer worse.
So every load test also scores quality, from the same rooms, step by step:

| Score | What it means | How it is measured |
|---|---|---|
| **Word error rate** | Of the words the participant said, the share the bot got wrong (misheard, missed or invented). 0% is perfect | Each recorded sentence is compared with what the bot stored for it. British and American spellings count as the same word |
| **Fragmented** | Sentences the bot stored as two or more turns, because it took a pause for the end of the turn. The pilot's "chained answers" came from this | Count of sentences with 2+ stored turns |
| **Missed** | Sentences the bot never stored at all | Count of sentences with no stored turn |
| **Reply length** | How many words the bot says per answer (typical, worst 1 in 20, longest), and how many answers ran over 40 words. Spoken answers are heard, not skimmed: long ones feel slow and hard to follow | Word count of each stored reply (the greeting excluded) |
| **Answers** (1–5) | *answers*: does the reply respond to what was said, correctly? *context*: on the scripts' follow-up turns, does it use what was said earlier? *suits speech*: short and natural enough to follow by ear? *overall* | Up to 20 replies per step, each with the conversation before it, scored by a fixed judge model (OpenAI `gpt-5.4`) against a fixed rubric (`agent-runner/judge.py`). Only the test's synthetic conversations are sent. Results compare only within the same rubric version (`RUBRIC_VERSION`) |

**The rule for speed changes:** a change that makes the bot faster (like 8-bit weights) stays
only if quality holds: judged *overall* no more than 0.2 lower, word error rate no more than
1 point higher, than the configuration it replaces.

**How the bot sounds** is scored from recordings: the synthetic participant records the bot's
reply to every third turn in the first 10 rooms (at most 40 clips a run), as a person in the
room hears it. After the run:

| Score | What it means | How it is measured |
|---|---|---|
| **Intelligibility** | Of the words the bot meant to say, the share a listener gets wrong. 0% is perfectly clear | An independent transcriber (open Whisper, `base.en`) listens to each clip; its words are compared with the reply the bot stored |
| **Naturalness** (1–5) | How natural the voice sounds, as listeners would rate it | UTMOS22, an open model trained to predict listener ratings (`tarepan/SpeechMOS` v1.2.0) |

Both models run on the report's machine (GitHub's runner), never in the bot's image. The
clips are saved next to the result, under `loadtests/…-clips/`.

The scores come from the conversation's stored turns, which the console serves as data at
`/api/meetings/<conversation id>/utterances` (the Markdown transcript is the readable version).

## Stack profiles

A profile is the bot configuration every test room gets, in `agent-runner/load_profiles/`:

| Profile | Language model | Voice |
|---|---|---|
| `ours` | Qwen2.5-7B-Instruct on our own GPU (vLLM) | Kokoro on our own GPU |
| `v1` | gpt-5.4-nano (OpenAI) | ElevenLabs |
| `stored` | whatever the console's global config says | |

Speech-to-text is set for all of staging: Parakeet on our GPU when the NIM is on,
Deepgram otherwise. A new stack to compare is a new profile file, nothing more.

`v1` costs money per turn: run it small (smoke, load at a few rooms) as the reference
point, and run the big shapes on `ours`.

## Running one

1. **Switch on what the profile needs.** For `ours`: `model_services = ["llm", "tts"]` and
   `stt_nim_enabled = true` (see `v2-deployment.md`). Beyond about 6 rooms, also raise
   `bot_pool_max` (each bot machine holds about 3 sessions; default 2 machines).
2. **Start it** from GitHub → Actions → *Load test v2* → *Run workflow* on branch `v2`,
   or from a terminal:
   ```bash
   gh workflow run loadtest-v2.yml --ref v2 -f profile=ours -f shape=smoke -f target=10
   ```
   `hold_s=60` shortens every step for a quick rehearsal.
3. **Read it.** The run's summary page shows one row per step and the verdict; the full
   result (JSON) is saved in the media bucket under `loadtests/`.
4. **Switch things back off** with a PR. GPUs cost $0.805/hour each while on.

The test presses **Prepare for study** for its largest step before starting and **Stop
preparing** at the end, exactly as a researcher would, so bot machines are warm and the
test measures the system under load, not machines booting. (A cold start is measured
separately: the acceptance tests record how long a bot takes to join.)

## Rehearsing locally

The same driver runs against the local Docker stack:

```bash
docker compose -f .devcontainer/docker-compose.yml exec -T \
  -e MEET_URL=http://meet:3000 -e CONSOLE_PASSWORD=... -e LIVEKIT_URL=ws://transport-server:7880 \
  -e PROFILE=stored -e SHAPE=load -e TARGET=2 -e HOLD_S=45 -e PREPARE=0 \
  agent-runner uv run python tests/load_run.py
```

## Where the code is

| What | Where |
|---|---|
| Shapes, rules, measurements (pure, unit-tested) | `agent-runner/load_plan.py`, `tests/test_load_plan.py` |
| Quality scores (pure, unit-tested) | `agent-runner/quality.py`, `tests/test_quality.py` |
| The answer judge: rubric, sampling, parsing (unit-tested; the model call is one function) | `agent-runner/judge.py`, `tests/test_judge.py` |
| Voice scores: which turns are recorded, matching clips to replies (unit-tested) | `agent-runner/quality.py` (`record_turn`, `reply_for`, `voice`) |
| A conversation's turns as data | runner `GET /conversations/{id}/utterances`, console `/api/meetings/{id}/utterances` |
| The report (both tables, quality, staging's side) | `agent-runner/tests/load_report.py` |
| The driver: rooms, participants, listening | `agent-runner/tests/load_run.py` |
| Load generator in AWS | `infra/v2/staging/loadgen.tf` |
| The workflow | `.github/workflows/loadtest-v2.yml` |
