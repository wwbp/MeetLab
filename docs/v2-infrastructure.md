# v2 staging: what runs, how big, and what it carries

What v2 staging is made of, the size of each part, and the load it measured in each
configuration (October 2026). For how the numbers were measured, see
[load-testing.md](load-testing.md); for the runs themselves,
[the load test report](load-test-report-2026-10.md). Costs and every decision are in
[`infra/v2/LEDGER.md`](https://github.com/wwbp/MeetLab/blob/v2/infra/v2/LEDGER.md).

## In one table

**Which configuration for which study** (one room = one meeting with its bot; rooms had 1.7
people on average):

| Configuration | Rooms inside every rule | Reply time p50 / p95 at that load | Running cost |
|---|---|---|---|
| **Idle**: always-on parts, models off (Deepgram + OpenAI) | not load-tested | — | ~$200 / month |
| **Study**: our models on (3 GPUs) | **60** (~100 people) | 1.54 / 1.99 s | idle + ~$2.50 / hour |
| **Big study**: a bigger LiveKit and database, a 2nd voice GPU | **84** (~140 people) | 1.55 / 2.00 s | idle + ~$3.50 / hour |

"Inside every rule" means at least 95% of turns answered, the slowest 1 in 20 replies within
2 seconds, no failed starts, no dropped connections, and every session closed afterwards.
In the big-study configuration everyone was still answered at 102 rooms (~170 people), just
over 2 s for the slowest 1 in 20.

## The parts

| Part | What it does | Machine | How many | On when |
|---|---|---|---|---|
| **meet** | The website, the console, the API | t3.medium (2 vCPU, 4 GB), shared with the runner | 1 | always |
| **agent-runner** | Starts and tracks bots | same t3.medium | 1 | always |
| **Bot machines** | One small task per meeting's bot | c6i.large (2 vCPU, 4 GB), ~3 bots each | 0 → up to 35 | while bots run; *Prepare for study* warms them |
| **LiveKit** | Carries the audio and video (self-hosted, v1.12.0) | c6i.large (big study: c6i.xlarge) | 1 | always |
| **TURN** | Lets people behind strict firewalls join, over port 443 | a network load balancer in front of LiveKit | 1 | always |
| **Speech to text** | NVIDIA Parakeet (NIM) | g6.xlarge (1 NVIDIA L4 GPU) | 0 or 1 | switched on for studies and tests |
| **LLM** | Qwen 2.5 7B Instruct, 8-bit, on vLLM | g6.xlarge | 0 or 1 | switched on |
| **Voice** | Kokoro | g6.xlarge | 0, 1 or 2 | switched on (2 for big studies) |
| **Database** | Postgres 17: sessions, turns, config, events | db.t4g.small (big study: db.t3.medium) | 1 | always |
| **Web load balancer** | HTTPS for meet and LiveKit's signalling | AWS application load balancer | 1 | always |
| **Storage** | Recordings | S3 | — | always |

With the models off, rooms use vendors instead: Deepgram for speech to text, OpenAI and
ElevenLabs for the LLM and voice. Switching the models on or off is a pull request (see
[Deploying v2](v2-deployment.md)).

## What each part measured

From the 84-room run (big-study configuration) at its capacity, and where each part was heading:

| Part | At 84 rooms | At 102 rooms | Limit |
|---|---|---|---|
| Reply time p95 (the 2 s rule) | 2.00 s | 2.05 s | **the limit**: queueing in the models |
| LLM, time to first word p95 | 188 ms | 198 ms | GPU queue, not GPU power (under 15% CPU) |
| Voice, time to first sound p95 | 181 ms | 220 ms | doubles from light load (108 ms): the first to grow |
| Speech to text p95 | 367 ms | 367 ms | flat: far from its limit |
| LiveKit CPU | ~46% | 49% | about half used on c6i.xlarge (57% at 60 rooms on c6i.large) |
| meet CPU (% of its reservation) | 17% | 106% (a burst) | not a limit: ~0.3 vCPU at most (123% at the 100-room spike), host under 15%; reservation now 512 |
| Bot machines CPU | ~45% | 46% | not near |
| Database connections | 142 | 165 | ~180 on db.t4g.small (enough for ~100 rooms); about twice that on the 4 GB sizes |

Bot join time: about 6 s on a warm machine, about 37–44 s when a fresh machine first
downloads the bot. *Prepare for study* warms them before participants arrive.

## Growing past 84 rooms

In order of what gives first:

1. **Reply time**: a third voice GPU, and speaking at the first clause instead of the first
   full sentence (the latency work, B4). Both shorten the queues that set the 2 s limit.
2. **meet**: its own, larger machine (it shares a t3.medium with the runner today).
3. **Database connections**: a connection pooler, or a larger database.
4. **LiveKit**: a larger machine; beyond one machine, a second server needs Redis.

Every size above is a single setting in `infra/v2/staging/variables.tf`, raised by a pull
request for a test or study and lowered after, so the always-on cost stays the same.

## Caveats

- **One run per configuration.** Repeat a run before a decision rests on a small difference.
- **Synthetic speech.** Computer voices following scripts; real people pause, interrupt and
  talk over each other.
- **One AWS zone.** The database lives in one zone; a size change can only use that zone's
  spare capacity (db.t4g.medium had none on 2026-10-04, so the big study used db.t3.medium).
