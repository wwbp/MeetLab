# Capacity: what production runs, and what to run for a study

What production (meet.wwbp.org) runs, what to change for a study of N rooms, and why the
speech-to-text is on CPU and the media on LiveKit Cloud. Every number was measured (October
2026; [load-testing.md](load-testing.md), [the load test report](load-test-report-2026-10.md));
every decision, with its date, is in [`infra/v2/LEDGER.md`](https://github.com/wwbp/MeetLab/blob/main/infra/v2/LEDGER.md).

## Decision matrix: rooms at once → machines

One room = one meeting with its bot (about 1.7 people). The table is
[`capacity_model.matrix()`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/capacity_model.py);
`make test-static` fails if the two differ.

| Rooms at once | Bot machines (c6i.large) | Speech-to-text (CPU) | Database | What grows first | Cost / hour | If held a whole month |
|---|---|---|---|---|---|---|
| 1 | 1 | c6i.large | db.t4g.small | bot machines | $0.23 | $171 |
| 5 | 1 | c6i.large | db.t4g.small | bot machines | $0.23 | $171 |
| 10 | 2 | c6i.large | db.t4g.small | bot machines | $0.32 | $233 |
| 20 | 4 | c6i.large | db.t4g.small | bot machines | $0.49 | $357 |
| 26 | 6 | c6i.large | db.t4g.small | bot machines | $0.66 | $481 |
| 50 | 10 | c6i.xlarge | db.t4g.small | speech-to-text CPU | $1.08 | $791 |
| 100 | 20 | c6i.2xlarge | db.t4g.medium | database connections | $2.17 | $1,584 |

Cost is the machines above only (on-demand, us-east-1); LiveKit Cloud, OpenAI and ElevenLabs
are billed by use. Beyond 100 rooms nothing was measured, and the model refuses.

**The rules behind it** (each measured; keep every part at or under 80% so replies stay
within 2 s at p95):

- **Bots:** 5 per c6i.large (right-sizing sweep, run C).
- **Speech-to-text:** about 3% of a c6i.large per room, so one c6i.large holds 26 rooms,
  a c6i.xlarge 53, a c6i.2xlarge about 106.
- **Database:** about 1.6 connections per room (165 at 102 rooms); db.t4g.small allows ~180.

**What to change, and how:**

| Study size | Change | Where |
|---|---|---|
| up to 5 rooms | nothing: production's one warm bot machine holds 5 | — |
| up to 26 rooms | press *Prepare for study* about 10 minutes before; it adds bot machines | the console |
| 27–53 rooms | also `stt_cpu_instance_type = "c6i.xlarge"` | `infra/v2/stack/profiles.tf`, `prod`: PR, merge, release |
| 54–89 rooms | `stt_cpu_instance_type = "c6i.2xlarge"` | same |
| 90–100 rooms | also `db_instance_class = "db.t4g.medium"` | same |
| more than 100 | measure first (a load test), then extend the model | — |

Lower a size again after the study, the same way. Don't release during a study.

## Decision: speech-to-text on CPU, not GPU

Production runs Parakeet (int8, `stt-cpu/`) on a CPU machine. The same model also runs as an
NVIDIA NIM on a GPU (g6.xlarge), behind the same endpoint, so the bots work with either.

| | CPU (c6i.large → 2xlarge) | GPU (g6.xlarge, NIM) |
|---|---|---|
| Reply speed | about 15 ms slower at p95: no one can hear it in a ~1.5 s reply | about 15 ms faster at p95 |
| Accuracy | same (word error rate 2.2–4.0% in both) | same |
| Machine cost | $0.085–0.34 / hour, up to 100 rooms | $0.805 / hour |
| Licence | none (open model) | NVIDIA AI Enterprise in production, about $4,500 per GPU a year (free only for development and testing) |
| Audio leaves our AWS | no | no |

Measured in the right-sizing sweep, 2026-10-06 (runs A and B). The GPU is never chosen up to
the tested 100 rooms: a bigger CPU machine is the cheaper step.

**Switch to the GPU only when:** a study needs more than about 100 rooms at once and a load test
shows the largest CPU machine misses 2 s at p95; or a model the CPU can't run is needed (larger
or multilingual); or the licence becomes free for our use. **How:** in the `prod` profile set
`stt_nim_enabled = true` and `stt_cpu_enabled = false`; PR, merge, release. The NVIDIA key is in
SSM (`/meetlab-v2/prod/ngc`). The GPU builds its model on first start (about 20–30 minutes),
so switch a day before the study.

## Decision: LiveKit Cloud, not self-hosted

Production's media (audio and video) and video recording run on **LiveKit Cloud, Ship plan**
($50 a month, 100 recordings at once, $0.02 a minute past the included 600 recording minutes).
`livekit_self_hosted = false` in the `prod` profile. The local stack runs its own LiveKit
server and recorder (`transport-server`, `egress`), the same software.

| | LiveKit Cloud (Ship) | Self-hosted (built and load-tested on staging) |
|---|---|---|
| Always-on cost | $50 / month | about $85 / month: a c6i.large, a public IP, the TURN load balancer |
| Recording | per minute, 100 at once | about 1.6 CPU per recorded room: a c6i.2xlarge ($0.34 / hour) records 3 |
| To run | nothing | public UDP, TURN on port 443, Redis past one server, upgrades |
| Measured | in production since 2026-10-14 | 84 rooms inside every rule; 100 at once |

Cloud is cheaper until about **55–60 recorded room-hours a month**; v1's busiest month was
31 session-hours. (The pilot's failed recordings were the free tier's limit of 2 at once,
not Cloud itself.)

**Switch to self-hosted when:** recorded room-hours stay above about 55 a month, a study needs
more than 100 recordings at once, or audio and video must not leave our AWS. **How:**
`livekit_self_hosted = true` and `egress_count` in the `prod` profile (Terraform and the
recorder are already written: `livekit.tf`, `egress.tf`), then PR, merge, release, and move
rooms when no study runs. Size the recorders from the measurement above.

## How these numbers were measured (staging, October 2026)

Staging (removed 2026-10-14) was the same stack at test sizes. It ran our own models (Parakeet
NIM, Qwen 2.5 7B, Kokoro) and self-hosted LiveKit. These tables are kept as the record behind
the rules above.

### Configurations tested

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

### Staging's parts (now removed)

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

### What each part measured

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

### Growing past 84 rooms

In order of what gives first:

1. **Reply time**: a third voice GPU, and speaking at the first clause instead of the first
   full sentence (the latency work, B4). Both shorten the queues that set the 2 s limit.
2. **meet**: its own, larger machine (it shares a t3.medium with the runner today).
3. **Database connections**: a connection pooler, or a larger database.
4. **LiveKit**: a larger machine; beyond one machine, a second server needs Redis.

Every size above is a single setting in `infra/v2/stack/profiles.tf`; production's are in the
decision matrix at the top of this page.

### Video recording

Measured 2026-10-06 (`infra/v2/LEDGER.md`, Measurements): on our own recorders a recorded room
uses about 1.6 CPU (a `c6i.2xlarge`: 36% at 2 rooms, 64% at 3, saturated at 5); booking 2 CPU per
recording makes a full recorder refuse rather than degrade. Production records on LiveKit Cloud
(Ship: 100 at once, $0.02 a minute after 600) instead: at our use that costs far less than
recorders of our own (decided 2026-10-06). Staging keeps the self-hosted recorder, off between tests.
When no recorder is free, auto-record waits up to 10 minutes, then marks the session's recording
`failed`, so the console shows it has no video.

### Caveats

- **One run per configuration.** Repeat a run before a decision rests on a small difference.
- **Synthetic speech.** Computer voices following scripts; real people pause, interrupt and
  talk over each other.
- **One AWS zone.** The database lives in one zone; a size change can only use that zone's
  spare capacity (db.t4g.medium had none on 2026-10-04, so the big study used db.t3.medium).
