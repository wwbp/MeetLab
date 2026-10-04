# v2 ledger — decisions and costs

A running record, updated in the PR that makes each change. Newest first within each
section. Costs are on-demand us-east-1 list prices per month (730 h), before data
transfer. Budget rule (2026-09-30): staging may cost up to v1 production's average.

## Where we left off (2026-10-03, end of day)

**Done and live on staging (all merged to `v2`, acceptance 12/12):**
- L1 capacity guards, L2 self-hosted LiveKit, L3 our own models (NIM + Qwen2.5-7B **FP8** on vLLM + Kokoro), chosen per room in Bot Config.
- L5 load harness: *Load test v2* workflow, six standard shapes × stack profiles, fixed SLOs, Fargate load generator; report with participant view, inside-staging view (stage timings, CPU/memory, database).
- L4 quality, in every load test: hearing (word error rate, fragmented/missed sentences), reply length, answers judged 1–5 (`judge.py`), voice (intelligibility heard back, UTMOS naturalness).
- One-machine GPU/LiveKit services: stop-first deploys, circuit breaker, zone rebalancing off, model caches on the machine (NIM ready in 0 s after its one build; was ~20 min every restart).

**Unblocked by going public (Manual actions):** GitHub Actions had stopped starting jobs (*"recent account payments have failed or your spending limit needs to be increased"*). The repo is public for now; to return to private, a self-hosted runner or the org's spending limit.

**Running, costing money:** the three GPU services (NIM, Qwen, Kokoro) are on: ~$2.50/hour (~$60/day). Switching them off is a PR (`model_services = []`, `stt_nim_enabled = false`), which needs Actions.

**Next, in order:**
1. The first like-for-like comparison: *Load test v2* `load`, target 3, `hold_s=180`, profile `ours`, then `v1` (both runs failed to start on the billing block).
2. Decide reply length: replies run 83–105 words and the judge scores *suits speech* 1.7–2.5 (local rehearsal). A shorter-replies instruction is a product decision (the console config is shared with v1).
3. Follow-ups found today (table below): first-turn speaker-label race; fragmentation of paused sentences.
4. L6: ramp to 50–100 rooms (raise `bot_pool_max`), the report.

## Plan after L6 (2026-10-04, user's order)

1. ✅ **A1** recording endpoints get room-scoped auth (F10; exposed now the repo is public). Done as **console-only** (user's choice, #159): a room token proves nothing while anyone who names a room can get one.
1b. 📌 **Signed join links** (F10's other half: today anyone who names a room can join it and get its token). Pinned by the user: today's start links (one participant, one bot) need rethinking first.
2. ✅ **B2** spike: a study launching at once (50 rooms in 30 s passed, #160). 3. 🟡 **B3** target 100 rooms: valid to 72; the load generator tops out at ~75, so 100 needs it split in two.
4. **C2** every bot setting (STT, TTS, LLM, VAD or smart turn, prompts) chosen in Bot Config, piped through and tested: ✅ C2a audit (#164: one field was missing from the form; CI parity check); next C2b smart turn, C2c its effect on fragmentation. ✅ **C3** the first speaker's name (#163; it also fixed the lost Prolific ID).
5. **D1** TURN server. 6. **D2** study-flow live tests. 7. **D3** record the infra configuration with its capacity and latency numbers.

Not now: **B1** soak (sessions last 5–20 min, the breakpoint run already kept rooms busy for over an hour). Pinned for the end: **A2** back to a private repo; **B4** bot join latency and other latency tweaks. The user's: **C1** the short-replies prompt line, per study; **C4** a human-rated sample of the quality scores.

## Cost

**v1 production, estimated:** about **$1,110/month**. v1 resources have no cost tags, and
Cost Explorer only shows the whole shared account ($5.4k in July, $6.1k in August,
$7.1k in September, all lab projects). So this is priced from what runs:

| v1 resource | $/month |
|---|---|
| NIM `g6.xlarge` + 250 GB disk | 608 |
| agent-runner: 2 × `c6i.xlarge` | 248 |
| RDS `meetlab` `db.m5.large` + 200 GB | 148 |
| 2 load balancers | 40 |
| NAT (vivaprox-vpc) | 33 |
| meet: `t3.medium` | 30 |

**v2 staging, running total:** **~$180/month** always on, plus per hour while testing: $0.085 per bot machine, $0.83 per GPU service (NIM, LLM, TTS), ~$0.20 for the load generator

| Added | PR | Resource | $/month | Notes |
|---|---|---|---|---|
| 2026-10-02 | #134, on in #135 | Self-hosted LiveKit: `c6i.large` with a public IP, always on (`livekit_self_hosted`) | 66 | $0.085/hour + $3.65 public IPv4; missing from this table until 2026-10-03 |
| 2026-10-02 | L3 PR | Our models (`models.tf`, `model_services`): `llm` (Qwen2.5-7B-Instruct on vLLM) and `tts` (Kokoro), each a `g6.xlarge` on-demand, 0 to 1, + internal NLB and 100 GB disk while on | 0 off | $0.805/hour + ~$0.03/hour NLB each while on; with the NIM, a fully-ours hour ≈ $2.50 |
| 2026-10-03 | load harness PR | Load generator (`loadgen.tf`): one-off Fargate task per load test, 4–16 vCPU | 0 idle | ~$0.20/hour at 4 vCPU, ~$0.80 at 16, only while a run lasts |
| 2026-10-02 | #126, then reverted | One bot machine always warm (`bot_pool_min = 1`, `c6i.large`) | ~~62~~ 0 | Set back to 0 the same day (user's choice: save money until production); the live tests press Prepare for study instead (~$0.10 a run) |
| 2026-10-02 | #114, on-demand PR | Staging Parakeet NIM: `g6.xlarge` on-demand, 0 to 1, + internal NLB and 100 GB disk while on | 0 off | $0.805/hour + ~$0.03/hour NLB while on (an hour-long test ≈ $0.85) |
| 2026-09-30 | 4c PR 1 | Bot pool: c6i.large, 0 to 2 instances | 0 idle | $0.085/hour each while bots run |
| 2026-09-30 | #89 | agent-runner service + Service Connect namespace | ~0 | Shares the t3.medium; a rolling deploy may briefly add a second |
| 2026-09-30 | #88 | ECS instance `t3.medium` + 30 GB disk | 33 | Services only; bots get their own group |
| 2026-09-30 | #88 | Application load balancer | 18 | $16.40 base + usage |
| 2026-09-30 | #87 | 2 ECR repositories, last 30 images each | ~1 | $0.10/GB-month |
| 2026-09-30 | #86 | RDS `db.t4g.small`, 20 GB gp3, single-AZ | 26 | Backups up to 20 GB are free |
| 2026-09-30 | #86 | S3 media bucket | ~0 | $0.023/GB-month once recordings land |
| 2026-09-30 | #85 | NAT gateway + elastic IP | 36 | $0.045/GB processed on top |
| 2026-09-30 | #84 | Terraform pipeline, IAM roles | 0 | |

## On hold

| Since | Item | Why | Resume when |
|---|---|---|---|
| 2026-10-01 | **Self-hosting LiveKit (server + egress)** | Would remove the egress key (egress uploads with its own task role) and give staging its own media quota, but needs public UDP, TURN on 443, Redis, and about 3 CPUs per recorded room: ~$200/month on staging, ~$4.7k/month for 50 always-on recorders. Staying on LiveKit Cloud (Ship) for now | Load tests show Cloud's per-minute egress costs more than our own recorders, or we need to leave the vendor. Plan: L1 server + Redis, L2 egress, L3 TURN/TLS |
| 2026-09-30 | **Load testing (50 / 100 sessions)** | Staging uses v1's vendor keys (OpenAI, ElevenLabs, Deepgram, LiveKit), which share v1's quotas and bill; an exhausted ElevenLabs quota makes bots silent with no error | Staging has its own keys, or free drop-in models for STT/TTS/LLM, so scale tests measure our infrastructure without spending vendor quota |

## Follow-ups found along the way

| Found | Item | Where | Why it matters |
|---|---|---|---|
| 2026-10-04 | **Our synthetic test speech can't judge smart turn.** Smart Turn v3.2 on the library's Kokoro audio judged only 2 of 9 mid-sentence pauses unfinished (and 4 of 16 whole lines unfinished): Kokoro ends every phrase sounding finished, unlike a person pausing mid-thought. A local A/B (whisper-base, one room, 4 min each) showed no gain (11/14 fragmented vs 9/13) | `conversation_library` voices, `smart_turn.py` | Judge smart turn, and read the fragmentation numbers, with real human recordings that pause naturally |
| 2026-10-04 | One 16-vCPU Fargate load generator tops out at ~75 rooms (~128 synthetic people): its lag monitor flagged the 78-room step and its stuttering microphones showed as 9.9% speech-to-text errors | `tests/load_run.py`, `loadgen.tf` | To measure 100+ rooms, split the rooms over two generators (shard i of n) |
| 2026-10-04 | **meet reached 63% CPU** during the 50-room launch spike (under 15% while climbing gradually): room creation, tokens and bot starts land on meet at once, and meet shares one t3.medium with the runner | `meet` service sizing | A bigger launch may need meet on its own machine or two tasks (D3) |
| 2026-10-04 | ~~Acceptance ran while LiveKit's machine was being swapped~~ (fixed, #162): stop-first takes LiveKit down for minutes; every scenario failed with a 500 though nothing was broken. Scenarios now wait until LiveKit, meet and the runner finish deploying (`still_deploying`); seen live: "waited 47 s" | `acceptance_staging.py` | A size change no longer looks like an outage |
| 2026-10-04 | ~~`tts_aggregation_mode` missing from the console form~~ (fixed, #164): in the database, the API and the bot, but researchers couldn't see or set it. Added as *Start speaking*; `make test-config-parity` in CI compares the three | console Bot Config | The three-places rule can't be checked by either service's own tests |
| 2026-10-04 | The v1 integration load test `concierge-load.test.mjs` is **flaky**: it counts LiveKit webhook events within a fixed time and once saw 21 of 25 (passed on rerun) | `meet/tests/concierge-load.test.mjs` | A red check that isn't a regression slows every merge; wait for the events instead of a fixed time |
| 2026-10-03 | ~~Long conversations went silent~~ (fixed, context-summary PR): the context grew without bound; past Qwen's 8,192 tokens (rooms ~40 min old) vLLM answered 400, Pipecat marked the LLM unusable and, by its default policy, kept the bot in the room **mute**. Now Pipecat's own summariser compresses older turns (at 6,000 tokens or 20 messages; verified locally: 17 messages → one summary in 1.7 s) and a bot whose LLM breaks ends its session (`ProcessorUnusablePolicy.END`) | `bot.py` context aggregator, `PipelineWorker` | It confounded the first L6 capacity number (20 rooms) and would have silenced long study sessions on our stack (v1's larger-context model only delays it) |
| 2026-10-03 | ~~People's stored words began with their speaker label~~ (fixed, L4 PR: `_spoken_text`): `SpeakerLabelInjector` prefixes "Name: " for the LLM, and the bot stored that labelled text. Researchers' transcripts read "**load_000**: load_000: Why…", and the hearing score counted the label as misheard words | `multi_speaker_stt.py` → `bot.py` user turn | Every v1 and v2 transcript with a labelled turn carries it; the speaker is its own column |
| 2026-10-04 | ~~People in the room before the bot were never registered~~ (fixed, piece 2): `on_participant_connected` only fires for later arrivals, so their first sentence reached the LLM unnamed **and their Prolific ID was never stored** (the late fallback kept only the name). Now the bot registers everyone present at join, one `speaker_meta` for every path; verified locally: named from the first sentence, Prolific ID stored | `bot.py` `_register`, `study_support.speaker_meta` | Paid studies are matched on the Prolific ID; participants usually join before the bot |
| 2026-10-03 | ~~A room's **first** user turn reaches the LLM without a speaker label~~ (fixed 2026-10-04, row above): the bot learns the participant after their first sentence ("Recovered identity … (connect callback missed it)"), so the labeller had no name for it yet | `on_participant_connected` race, `SpeakerLabelInjector` | In a multi-person room the LLM can't tell who spoke first; fix by learning the roster before the first transcript |
| 2026-10-03 | Replies run long: 83–105 words typical in the local rehearsal (stored config); spoken, that's 30–40 s per answer | the bot's system prompt / model | Long answers feel slow and invite interruption; the quality table now reports it per profile |
| 2026-10-02 | After every deploy, the first bot on each machine pulls the new image (~30 s; 64 s from task created to running); later bots start in 1–2 s | Prepare for study warms machines, not the image on them | A participant can still wait ~30 s after a deploy; pull the image when preparing, measure in the ramp |
| 2026-10-02 | ~~The spoken greeting isn't stored as a bot turn~~ (fixed: the bot stores fixed lines itself, `say()`): Pipecat closes a `TTSSpeakFrame` turn before ElevenLabs' words arrive with the audio (1.12 stores nothing; 1.4 stored only "Hello!"). Empty bot turns are now skipped | Pipecat assistant aggregator + ElevenLabs word timestamps | Transcripts lack the greeting line; the text is the bot config's `greeting`, so it is recoverable |
| 2026-10-02 | ~~**Overlapping speakers lose their words and their names (diagnosis F11, reproduced)**~~ (fixed: one resampler per participant, `livekit_input.py`; sim-attribution 3/3 for 2 and 3 speakers): `make sim-attribution`, three different sentences 0.15 s apart, real speech recognition (Deepgram). One speaker alone: stored correctly. Two at once: the LLM is told one speaker said the other's sentence, or a splice of both (3 runs, 3 different garbles). Three at once: nothing stored (2 of 2 runs) | `multi_speaker_stt.py`, `SpeakerLabelInjector`, the single user aggregator in `bot.py` | The pilot's misattribution, now a failing test. It justifies the planned port to Pipecat's per-participant workers (plan: "Port the pipeline") |
| 2026-10-02 | Overlapping end rules: the room-gone sweep acts on one LiveKit room-list snapshot and can end a session whose bot is still starting; Pipecat's idle timeout (300 s) ends an empty-room bot before presence.py's 15 min arrival grace; v2 never sets LiveKit's room timeouts (defaults: 300 s empty, 20 s after the last person leaves) | `runner.reconcile_stale_conversations`, `bot.py` PipelineParams, meet `createRoom` | Rare with a warm bot machine; one owner per decision would remove them. Explainer: claude.ai artifact "Bot Lifecycle, First Principles" |
| 2026-10-02 | ~~**The bot pool had not scaled to 0 since 2026-10-01 04:53**~~ (unstuck; now detected): one machine stopped by hand sat in `Terminating:Wait` for 27 h, and the pending termination blocked scale-in. Two idle `c6i.large` ran the whole time, ~$4.60 | Bot ASG; ECS managed-draining hook | The live tests passed faster on the warm pool, so nothing failed. Now: capacity status counts unhealthy machines, the console warns, acceptance `prewarm` fails on any |
| 2026-10-02 | The apply role still may launch spot instances (`spot-instances-request/*`), unused since the NIM went on-demand | `infra/v2/bootstrap/compute.tf` | Least privilege: drop it at the next bootstrap change |
| 2026-10-01 | **v1's egress key is far broader than egress needs**: IAM user `meetlab-egress-writer` has `s3:*` on the media bucket plus account-wide `s3:PutAccountPublicAccessBlock` and `s3:CreateJob`, and the key sits with LiveKit | v1 IAM (not Terraform) | Anyone holding that key can read and delete every study recording, or turn off the account's public-access block. Not changed: v1 is frozen. Rotate or narrow it before v1 carries another study |
| 2026-10-02 | ~~Stop 0.5 s after start: the bot still joined~~ (fixed: a bot whose session is no longer running exits before joining) | ECS ListTasks did not yet show the just-started task, so the runner's /stop found nothing to stop; caught by acceptance `stop_early` after #107 | Timing-dependent; it had passed twice before |
| 2026-10-01 | ~~Since 4c, console Record started video egress but never per-speaker audio capture~~ (fixed: the runner sets `recording_requested`, the bot reads it on its heartbeat) | `runner.start_recording_for_room` switched on a sink from its own in-process registry, empty once bots run as tasks | Up to 10 s of audio after Record is missed (one heartbeat) |
| 2026-10-01 | The socket proxy passed a request to create an exec (400 from Docker, not 403 from the proxy); starting one is governed by `EXEC=0`, now set explicitly | `.devcontainer/docker-compose.yml` | Local laptop only; confirm exec start is refused before relying on it |
| 2026-10-01 | A bot alone in a room never leaves, so tests that start bots in empty rooms leave containers running | `bot.py` (design iteration 4, `should_leave`); `make test-unit` cleans them up | Same leak existed in-process, just invisible |
| 2026-10-01 | ~~Deleting a room left its session 'running' for a moment; a room recreated at once was handed the old bot~~ (fixed in #105: delete-room calls `/stop`) | Race introduced by one-session-per-room (#104); caught by the CI integration test `room delete clears bot claim…` | Without it, delete-and-recreate in the console could show a stale bot |
| 2026-10-01 | Running `pnpm test:api` several times within a minute trips the start-link rate limit (5/min/IP) and fails tests 10 and 12 with 429 | `meet/app/api/start-link/route.ts` | Not a bug; wait a minute between local runs |
| 2026-10-01 | ~~#99 dropped the reconcile loop's startup registration~~ (fixed in #100) | Its test called the loop directly; now it goes through app startup | Test through the real entry point, not the function |
| 2026-10-01 | ~~**After every rolling deploy, nothing reconciles**~~ (fixed: every process reconciles) | `runner.py` advisory-lock election ran once at startup; the old task held the lock while the new one started, so every new process stood down for good (diagnosis F6). Found when a `kill -9`'d bot was never failed | In v1 too: stale sessions and silent bots are never cleaned up after a deploy until the next restart |
| 2026-10-01 | Live acceptance tests from a laptop are unreliable | The Mac sleeps (6–10 min gaps): AWS signatures expire, LiveKit sockets drop, timeouts fire | Run live acceptance tests from inside AWS (a CI job or a one-off ECS task) |
| 2026-10-01 | One intermittent failure in `test_recordings_transcript.test_fresh_session_always_returns_200` | Seen once during a laptop-sleep window; 3 full runs since pass | Watch for a recurrence |
| 2026-10-01 | Cold bot start is 132 s from an empty pool | Instance boot (93 s) + image pull (35 s) | A participant must never wait that long: pre-scale the bot pool before a study; slimming the 1 GB image cuts the pull |
| 2026-10-01 | ~~Bot dies on SIGTERM without leaving the room or writing `ended`~~ | `bot.py`, no signal handler (exit 143) | Fixed in 4c PR 4: bot tasks run Pipecat's runner with `handle_sigterm` |
| 2026-10-01 | Managed scaling launched 2 instances for 1 pending task | `aws_ecs_capacity_provider.bots` | Doubles cold-start cost; check `maximum_scaling_step_size` when sizing |
| 2026-09-30 | ~~Bot goes silent instead of failing when its STT backend is missing~~ (fixed in 4c PR 4: fails at setup, session `error`) | `bot.py` / `nemotron_stt.py`: `parakeet-*` with no `NEMOTRON_STT_URL` posts to a bare `/v1/audio/transcriptions`; every turn errors, the session stays "running" | Same failure would hit prod if the NIM URL were lost. Fail the session at start with a clear reason (design plan iteration 5) |
| 2026-09-30 | Flaky integration test: `room delete clears bot claim…` | `meet/tests/concierge-api.test.mjs:378`, 30 s wait for local LiveKit to drop the room | Failed once on #91 (Terraform-only), passed on re-run |
| 2026-09-30 | Harness logs `KeyError` on LiveKit reconnect | `livekit.rtc` `local_track_published` after a signal resume | Noise, but hides real errors in sanity output |

## Measurements

**B2 spike and B3 climb, 2026-10-04** (profile `ours-short`, conversation library; session headroom: LiveKit c6i.xlarge, database db.t4g.medium, 2 voice GPUs; runs 37173343495, 37173982533):

| Test | Rooms | Answered | p50 / p95 | Notes |
|---|---|---|---|---|
| **Spike** (all start within 30 s) | 50 | 100% | 1.46 / 1.90 s | **PASS.** Bot join p95 44 s (17 fresh machines pulling the image at once); **meet at 63% CPU** at the burst (t3.medium shared with the runner): a candidate limit for bigger launches. Voice 1.0% heard back, naturalness 4.32 |
| Climb, +6 every 3 min | 72 | 100% | 1.52 / 1.97 s | **Largest valid step.** LiveKit ~40%, database 120 connections, voice first audio p95 176 ms (2 GPUs; one GPU gave 260 ms at 60) |
| Climb | 78 | 95% | 1.50 / 2.03 s | **Not valid: the load generator lagged 729 ms** (one 16-vCPU Fargate task tops out at ~75 rooms, ~128 people); its stuttering microphones show as speech-to-text 9.9% wrong, 26 sentences missed |

Staging's limit is above 72 rooms; measuring 100 needs two load generators (half the rooms each).

**L6 breakpoint, 2026-10-04** (`breakpoint`, target 20: 4 → 60 rooms, +4 every 5 min; profile `ours-short`: NIM + Qwen2.5-7B FP8 + Kokoro + the short-replies line; the conversation library, rooms of 1–3 people, ~1.7 per room; run 37163560523). **Every step passed up to the test's ceiling: capacity ≥ 60 rooms (~100 people)**, 100% answered, no disconnects, every session closed. An earlier run (37156351181) "failed" at 24 rooms: long conversations ran out of LLM context and went silent (fixed, #155), not load.

| Rooms | Turns | p50 / p95 | Qwen first token p95 | Kokoro first audio p95 | LiveKit CPU | Bot machines CPU | DB connections | Judged overall | Word error rate |
|---|---|---|---|---|---|---|---|---|---|
| 4 | 103 | 1.27 / 1.62 s | — | — | — | — | — | — | — |
| 24 | 731 | 1.42 / 1.77 s | 141 ms | 116 ms | 30% | 46% | 35 | 4.0 | 2.8% |
| 40 | 1,220 | 1.48 / 1.88 s | 161 ms | 167 ms | 43% | 46% | 62 | 4.2 | 2.7% |
| 60 | 1,823 | 1.54 / **1.99 s** | 176 ms | 260 ms | **57%** | 61% | **101** | 4.0 | 2.6% |

Next limits, by the trend: p95 crosses 2 s just past 60 rooms (Kokoro queueing is the steepest stage); LiveKit's c6i.large nears saturation around 100 rooms; database connections (~1.7 per room) near db.t4g.small's ~180 around 100 rooms. Quality held under load. ~19% of sentences were stored as 2+ turns (fragmentation), and the bot answers fragments: a target. Bot joins: 5–8 s, ~40 s when a machine takes its first bot (image pull).

**First ours-vs-v1 comparison, 2026-10-03** (`load`, target 3, `hold_s=180`; same speech, same STT: our NIM; runs 37142384043, 37143343566; reports scored with #150):

| | ours (Qwen2.5-7B FP8 + Kokoro) | v1 (gpt-5.4-nano + ElevenLabs) |
|---|---|---|
| Verdict | PASS every step | FAIL at 2 rooms (p95 2,019 ms) |
| Answered | 100% | 100% |
| End of speech → first audio, p95 | 1.5–1.8 s | 1.7–2.0 s |
| LLM first token, p95 | 58–104 ms | 662–814 ms |
| TTS first audio, p95 | 90–227 ms | 120–154 ms |
| STT word error rate | 0.7–4.3% | 0.5–3.5% |
| Judged answers / context / suits speech / overall (1–5) | 4.0–4.2 / 4.3–4.7 / 2.4–3.1 / 3.4–3.9 | 4.2–4.8 / 4.4–5.0 / 2.3–2.7 / 3.2–4.0 |
| Reply length, typical | 46–61 words | 74–89 words |
| Voice: heard back / naturalness (UTMOS) | 5.9% / 4.46 | 4.7% / 3.90 |
| Peak CPU anywhere | < 30% | < 30% |

Ours is faster (the LLM starts ~8× sooner) at about the same quality; both talk too long (*suits speech* lowest). 3 rooms: first readings, not capacity.

**First load test on our own stack, 2026-10-03** (`load`, target 5, profile `ours`: NIM + Qwen2.5-7B 16-bit on vLLM + Kokoro, self-hosted LiveKit; run 37090373932). The 15-minute hold at 5 rooms, 161 turns:

| Measure | Value | Rule |
|---|---|---|
| Turns answered | 100% | ≥ 95% ✅ |
| End of speech → first bot audio, p50 / p95 / p99 | 1,544 / 2,474 / 3,205 ms | p95 ≤ 2,000 ❌ |
| Bot join p95; start errors; disconnects; sessions left open | 5 s; 0; 0; 0 | ✅ |
| Peak CPU: bot machines / meet / runner / LiveKit / database | 32% / 7% / 5% / 7% / 6% (9 connections) | not the limit |
| Bot stages p50 / p95: speech-to-text; Kokoro first audio; **waiting for Qwen's whole first sentence** | 347 / 351; 71 / 125; **653 / 1,262 ms** | |

The LLM's generation speed is the whole problem: memory-bound at ~20 tokens/s for 16-bit 7B on an L4 (300 GB/s), and TTS waits for a full sentence. (Kokoro's time was logged as "LLM first token" until #141.)

**Staging STT NIM cold start, 2026-10-02** (#117, on-demand `g6.xlarge`, NIM log timestamps):

| Step | Time (UTC) | Elapsed |
|---|---|---|
| Instance launched | 05:51 | 0 |
| Image pulled, model manifest cached | 05:58 | 7 min |
| Model downloaded, TensorRT engine build starts | 05:59 | 8 min |
| Engine built (one tactic hit out-of-memory at 16.2 GB and was skipped) | 06:11 | 20 min |
| Healthy behind the NLB; first bot turn transcribed | ~06:14 | ~23 min |

A warm NIM then transcribed an acceptance turn in a 21 s scenario, with no NIM errors. Cost of the test: about 50 min on, ~$0.70.

**4c spike, 2026-10-01** (`agent-runner/tests/spike_dispatch.py`, bots started through staging meet, `c6i.large` pool):

| Path | Start request → bot in LiveKit room | Where the time goes |
|---|---|---|
| Cold: pool at 0 instances | 132 s | 93 s instance boot and ECS registration, 35 s image pull (1 GB), 3.5 s process start, 0.3 s join |
| Fresh instance, image not yet pulled | 41 s | 33 s image pull |
| Warm: instance up, image cached | 5 s | 1.9 s to task running, 3 s to join |
| Retried RunTask, same session ID | Same task returned (2 of 2) | `clientToken` idempotency holds |
| StopTask → STOPPED | 1–2 s | Exit code 143: the bot has no SIGTERM handler, dies at once, and stays listed in the room; no `ended` write (4c PR 4) |

Also seen: managed scaling launched **two** instances for one pending task.

## Upgrade later (load testing)

| Item | Staging now | At load testing |
|---|---|---|
| STT | Deepgram (`STT_MODEL_OVERRIDE`) unless the NIM is switched on: `g6.xlarge` on-demand, 0 instances unless a test needs it ($0.805/hour on) | On-demand or reserved NIM capacity, sized from the test |
| LLM, TTS | One `g6.xlarge` each; weights kept on the machine across restarts, downloaded when a machine is new | Size from the load test (rooms per GPU); cache weights (EFS or warm instance); maybe both on one bigger GPU |
| Bot pool | `c6i.large`, 0 to 2 | Instance type, floor and ceiling from measured per-session load |
| Services instance | one `t3.medium` | Sized from the test |
| Database | `db.t4g.small`, single-AZ | Sized up, multi-AZ before a study |
| NAT | one | One per AZ before production |

## Manual actions (outside the pipeline)

Everything else is applied by GitHub Actions. These were done by a person on purpose,
because the pipeline is not allowed to; log each one here when it happens, so the
next person knows what exists that no PR created. Never record secret values.

| When (UTC) | Who | What | Why it was manual | Undo / rotate |
|---|---|---|---|---|
| 2026-10-04 ~05:00 | Claude, on the user's ask | Repo setting **"Automatically delete head branches" on**; deleted 11 merged remote branches and 74 local ones, each checked to be contained in `v2` or `main` (48 old local v1 branches kept: not provably merged) | Housekeeping (user: "delete branches after pr merge"); a repository setting, not infrastructure | Turn the setting off in Settings → General |
| 2026-10-03 ~20:10 | Claude | Uploaded the conversation library (796 files, 120 MB: 795 WAVs + `library.json`) to `s3://meetlab-v2-staging-media-…/loadtests/library/v1/`, built locally by `tests/build_conversation_library.py` | It is data the load generator reads, made once on a laptop (Kokoro on CPU); the pipeline deploys code, not datasets. Kept out of the public repo | Rebuild with the script; delete the prefix |
| 2026-10-03 ~17:40 | the user (repo owner) | **wwbp/MeetLab made public**; Actions → fork pull request workflows: *Require approval for all external contributors*. Before: secrets scan of all 59 branches, 304 commits (gitleaks): no leaks | GitHub Actions stopped starting jobs (monthly free quota used; payment/spending limit needs an org owner, ~3 days on a weekend). Public repos run on GitHub's standard runners for free. Accepted exposure: known open gaps (e.g. `/api/record/*` public, F10) and study docs are now readable | Back to private once unblocked: a self-hosted runner (free for private repos), or the org raises the Actions spending limit |
| 2026-10-03 01:40 | Claude, on the user's yes | `terraform apply` in `infra/v2/bootstrap` (#139): the acceptance role may start the load generator's task (and pass its two roles), find its subnets and group, read `loadtests/` results and CloudWatch metrics; session 1 h → 4 h | CI roles live in bootstrap, which the pipeline cannot change (it would grant itself permissions) | Revert the PR's `acceptance.tf` and apply again |
| 2026-10-02 | AbhaySingh (run by the assistant on approval) | `terraform apply` of `infra/v2/bootstrap`: DNS name `livekit-staging.wwbp.org` for the apply role; the two self-hosted LiveKit parameters for the acceptance role (#134) | Bootstrap holds CI's own permissions | Re-apply bootstrap from `v2` |
| 2026-10-02 22:53 | AbhaySingh | Minted the self-hosted LiveKit API key and secret into SSM `/meetlab-v2/staging/SELFHOSTED_LIVEKIT_API_KEY` and `_SECRET` (random, never displayed) | The server's keys must not be in Terraform state; CI can't write secrets | Rotate: put new values, then force a new deployment of livekit, meet, the runner (bots read them at start) |
| 2026-10-02 13:45 | AbhaySingh (run by the assistant on approval) | `complete-lifecycle-action CONTINUE` for bot machine `i-0d41e79bfb55e5e4a`, stuck in `Terminating:Wait` since 2026-10-01 04:56 | It had been stopped by hand (CloudTrail: `StopInstances` by AbhaySingh, 2026-10-01 04:55; not from the assistant's session), so ECS could never finish draining it, and the stuck termination blocked every scale-in | None needed: Auto Scaling terminated the already-stopped machine |
| 2026-10-01 04:55 | AbhaySingh (console or another session; unknown) | `StopInstances` on bot machine `i-0d41e79bfb55e5e4a`, 2 min after ECS launched it | Unknown | See the row above: caused the stuck pool |
| 2026-10-02 | AbhaySingh (run by the assistant on approval) | `terraform apply` of `infra/v2/bootstrap`: the boundary lets CI-created roles warm `meetlab-v2-*-bots` groups (Prepare for study, #119) | Bootstrap holds CI's own permissions | Re-apply bootstrap from `v2` |
| 2026-10-02 04:16 | AbhaySingh | Created Secrets Manager secret `meetlab-v2/staging/ngc` (`{"username":"$oauthtoken","password":<NGC key>}`), copied from v1's SSM `/meetlab/stt-nim/ngc_api_key` without displaying it | ECS needs it to pull NVIDIA's NIM image and download the model. CI can't read v1's key, Terraform would keep it in state, and the assistant's session can't write secrets | Rotate: update the secret's value in place (same shape), then force a new deployment of the STT NIM service. Same key as v1: rotating one does not rotate the other |
| 2026-10-02 | AbhaySingh (run by the assistant on approval) | `terraform apply` of `infra/v2/bootstrap`: spot launches and meetlab-v2 network load balancers for the apply role; `ecs:DescribeServices` on staging for acceptance (STT NIM PR) | Bootstrap holds CI's own permissions | Re-apply bootstrap from `v2` |
| 2026-10-02 | AbhaySingh (run by the assistant on approval) | `terraform apply` of `infra/v2/bootstrap`: the boundary allows `s3:AbortMultipartUpload` (#112) | Same | Same |
| 2026-10-02 02:51 | AbhaySingh | Minted the access key for `meetlab-v2-staging-egress-writer` straight into SSM `/meetlab-v2/staging/EGRESS_S3_KEY_ID` and `_SECRET` (both version 2; never displayed) | LiveKit Cloud needs a real key to upload video. CI is explicitly denied `iam:CreateAccessKey` so a PR can never mint credentials, and Terraform would keep the secret in its state. The AI assistant's session is also blocked from writing secrets | Rotate: [docs/v2-deployment.md](../../docs/v2-deployment.md) step 3. Deleting the user requires deleting this key first |
| 2026-10-01 | AbhaySingh (run by the assistant on approval) | Placeholder values in the two `EGRESS_S3_KEY_*` parameters before #111 merged | ECS can't start agent-runner if a referenced parameter is missing | Replaced by the real key above |
| 2026-10-01 | AbhaySingh (run by the assistant on approval) | `terraform apply` of `infra/v2/bootstrap` (egress user permissions) | Bootstrap holds CI's own permissions; CI must not be able to widen them | Re-apply bootstrap from `v2` |
| 2026-09-30 | AbhaySingh | `infra/v2/seed-staging-secrets.sh`: vendor keys copied from v1 into SSM | CI can't read v1's settings, by design | Re-run with `--force` |

## Decisions

| Date | Decision | Why | Revisit when |
|---|---|---|---|
| 2026-10-03 | One-machine services (`llm`, `tts`, `stt-nim`, `livekit`) deploy **stop-first** (minimum healthy 0%, maximum 100%) with the **circuit breaker** (rollback); **Qwen's weights and the NIM's built model stay on the machine** (ECS-managed shared Docker volumes); acceptance fails at once on "unable to place a task" (`deploy_state`); **zone rebalancing off** for these four | ECS's default rolling deploy starts the new task first; with one machine per service it can never be placed, so the FP8 deploy retried every 30 min for ever (AWS's fixed PROVISIONING limit) while Terraform reported success. Stop-first alone would turn each change into a cold start (Qwen 15 GB download, NIM ~20 min rebuild; v1 never kept the NIM cache either); the volumes make it a restart from disk. Surge capacity (a second GPU per deploy) rejected for staging: cost, g6 availability, and two LiveKit servers without Redis would split rooms. LiveKit changes only when no test runs (stop-first drops live meetings). #143's first apply was refused: ECS turns zone rebalancing on by default (since 2025-09) and refuses it with maximum 100%; rebalancing also starts before it stops, and one task has nothing to balance (#144). The mock-provider tests and plans can't see API validation rules: only an apply does. The NIM's cache then failed: it runs as a non-root user and a fresh Docker volume is root's ("Permission denied", 3 failed starts; the circuit breaker rolled it back to the uncached NIM, as designed). I had assumed Docker would copy the image's ownership; NVIDIA's docs say to make the cache writable, so an init container now does `chmod 777` first (#146). vLLM runs as root: Qwen's cache worked | Production: ≥2 machines per model (capacity needs it anyway), then rolling with minimum 50% |
| 2026-10-04 | **Smart turn is an add-on switch in Bot Config, with its own wait** (`smart_turn_wait_ms`, 500–5000, default 3000; how long an unfinished-sounding turn stays open); **every Bot Config setting carries a one-line explanation** in the console | User: "smart turn config should be available in bot config, include on/off flag, same as other addons… one liners for config setting to explain what they do". The pilot measurement makes the wait the dial between cutting people off and keeping them waiting | Tune the wait from a real study |
| 2026-10-04 | **Smart turn, per participant, as a Bot Config option** (`turn_detection`: `silence` default, `smart_turn`; `smart_turn.py`): each person's own Pipecat Smart Turn v3.2 (bundled, CPU) sits between their VAD and STT; an unfinished verdict keeps their turn open (closed after 3 s if they say nothing more). Pipecat's documented setup puts the analyser in the user aggregator, which never sees audio in our per-speaker pipeline: there it would have been a setting that does nothing. Works with Parakeet and Whisper; Deepgram and OpenAI fall back to silence, logged. The console's Bot Config is now sectioned: the STT model with its model-only settings (OpenAI's delay and VAD mode, previously not shown at all), Turn-taking (all models), LLM, Voice, Session; `make test-config-parity` now requires an input bound on screen, not just the field's name | User: "everything controlled in bot config… piped correctly"; quality first (fragmentation ~19% on the library) | Real human recordings to judge it (row below in Follow-ups) |
| 2026-10-04 | **Big-test headroom as switches** (`livekit_instance_type`, `db_instance_class`, `tts_replicas`; tests/scale.tftest.hcl): raised by PR for a test session, back after, so the always-on cost is unchanged. LiveKit's group replaces its machine on a type change (instance refresh, protected machines included); the database applies a size change on merge (`apply_immediately`, a few minutes' restart) | The 60-room run's trends put LiveKit (57% CPU), database connections (101 of ~180) and the voice's queueing (first audio 116 → 260 ms) at their limits near 100 rooms | Production sizing (D3) |
| 2026-10-03 | **Load tests speak a conversation library** (L6; `conversation_library.py`): 100 DailyDialog test-split dialogues (6–10 turns, 9 topics in turn), each side in one of 20 Kokoro English voices, ~1 line in 8 with a 0.8 s mid-turn pause; rooms of 1 / 2 / 3 people in 50 / 30 / 20% (user's study mix), each person its own microphone. Audio built once locally (Kokoro ONNX, CPU) and kept in S3 `loadtests/library/v1/`, not in the public repo; the load generator may read only that prefix | User: test with "multiple typical conversations", not our 5 scripts. Researched first: LiveKit's own method replays the agent's speech (no content, no quality) but its ramp advice holds (no bursts; +10–20 sessions per step); Pipecat's benchmark is one 30-turn task script (MIT); DailyDialog is the standard everyday-dialogue corpus, licence approved by the user for research | A long-session script (Pipecat's 30 turns) for the soak; real human speech later |
| 2026-10-03 | **Voice scored** (L4 step 3): the participant records every third reply in the first 10 rooms (≤ 40 clips a run); the report scores intelligibility (open Whisper `base.en` hears it back, word error rate against the stored reply) and naturalness (UTMOS22, `tarepan/SpeechMOS` v1.2.0). Both run on the report's machine with CPU PyTorch, never in the bot's image | Quality first: our voice (Kokoro) must be compared with v1's (ElevenLabs) on clarity and naturalness, not just speed. First local clip (stored config): 2.2% heard back, naturalness 4.01. The rehearsal also found that a room's crash was swallowed silently (`gather(return_exceptions=True)`): now printed and counted as a start error | A human-rated sample to check UTMOS on our voices |
| 2026-10-03 | **Answers judged** (L4 step 2, `judge.py`, rubric `2026-10-03.1`): up to 20 replies per step, each with the conversation before it, scored 1–5 by OpenAI `gpt-5.4` on answers / context (follow-ups only) / suits speech / overall; runs at the end of each load test, inside the load generator (its task gets the stored OpenAI key; no new IAM) | User's yes to a vendor judge: consistent and cheap (cents a run), and only synthetic text is sent. First local check on 6 replies: answers 4.2, context 5.0, **suits speech 2.3**: the 83–105-word replies are too long to follow by ear | A small human-rated sample to calibrate the judge; bump `RUBRIC_VERSION` on any change |
| 2026-10-03 | **Quality scored from the load test's own rooms** (L4 step 1, `quality.py`): word error rate, fragmented and missed sentences, reply length, per step and profile, from the stored turns (new: runner `GET /conversations/{id}/utterances`, console `/api/meetings/{id}/utterances`). Next: a judge rubric for answers (vendor model as an offline judge, synthetic text only), then the bot's audio (intelligibility, naturalness). A speed change stays only if its scores hold: rubric within 0.2/5, word error rate within 1 point | User: quality first, then speed, "transferable across our chosen config"; the same run gives both, so a faster config cannot hide a worse one. Data as JSON, not the Markdown transcript: exact times, who spoke | Real human recordings (e.g. LibriSpeech) for the hearing score; a full spelling normaliser then |
| 2026-10-03 | **Qwen weights in 8 bits** (`--quantization fp8`, same L4) | First load test: the first sentence waited 653 ms p50 / 1,262 p95, making p95 2.47 s against a 2 s rule; generation is memory-bound, so halving the bytes per weight roughly doubles tokens/s at no extra cost (user's choice over a bigger GPU, clause-level TTS, or a smaller model) | L4 quality scores show a loss; or still too slow → g6e (L40S, $1.86/h) |
| 2026-10-03 | **Load tests are standard shapes × stack profiles, judged by fixed SLOs** (`load_plan.py`, `tests/load_run.py`, `docs/load-testing.md`): smoke, load, stress (to 2×), spike, soak (1 h), breakpoint (to 3×, stops at the first failure = capacity); a profile is the bot config every room gets (`load_profiles/`). SLOs per step, for every profile: reply rate ≥ 95%, p95 end-of-turn → first bot audio ≤ 2 s, no start errors, no disconnects; afterwards no session left running. Runs from a Fargate task in AWS, started by the *Load test v2* workflow (dispatched on `v2`; `main` stays frozen) | User: "load/performance/stress tests in a standard way, transferable across our chosen config". The participant listens for the bot's audio (as `conversation_soak.py` taught us: a log line is not a reply); the 24 recorded turns are committed so every run hears the same speech; Prepare for study runs first so cold starts don't pollute load numbers; a lag monitor flags runs where the harness, not staging, was the bottleneck | After the first runs: server-side metrics in the result (CPU/memory per service, vLLM queue/KV cache, database connections); SLO thresholds against study needs |
| 2026-10-02 | **Our own LLM and voice** (load-test readiness L3; `models.tf`, `model_services`, off by default): Qwen2.5-7B-Instruct on `vllm/vllm-openai:v0.30.0` and Kokoro on `ghcr.io/remsky/kokoro-fastapi-gpu:v0.9.0`, each its own GPU service behind an internal NLB. A room picks them in its config: `llm_model = Qwen/Qwen2.5-7B-Instruct` goes to our vLLM (any other model to OpenAI), `tts_provider = kokoro` to our Kokoro with OpenAI voice names | User's choice: no vendor LLM/TTS costs for load tests; quality first, then speed. Both speak OpenAI's API, so Pipecat's OpenAI services drive them (`base_url`); Pipecat's own Kokoro runs in the bot's process, which would put a model in every bot. Separate services so each switches and scales alone | L4 quality scores against v1's stack; L6 rooms per GPU |
| 2026-10-02 | **Self-hosted LiveKit** (`livekit.tf`, `livekit_self_hosted`, off by default): `livekit-server` v1.12.0 on one c6i.large with a public IP; signalling `wss://livekit-staging.wwbp.org` through the load balancer's TLS; media straight to the machine (7881/TCP, 7882/UDP open; every join needs a signed token). meet, the runner and bots switch together; live tests follow the `livekit` output and skip the video scenarios | User's choice: test (and later serve) on our own media layer without using v1's LiveKit project quota. A private-only server would have forced the whole live suite into AWS; this is the standard LiveKit shape and grows into production by adding TURN and an egress server | Before it serves a study: TURN (strict firewalls), egress (video), more than one node |
| 2026-10-02 | Load-test readiness L1: each bot's database pool is 2 connections, no overflow (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`); the bot pool's ceiling is `bot_pool_max` (default 2); per-task metrics are `container_insights` (default off) | Staging held at most ~6 sessions (2 machines), and 100 bots on SQLAlchemy's default pool (up to 15 each) would exhaust db.t4g.small's ~180 connections. Insights is billed per task, so it is on only for test runs | Load test results: pool size per bot, database size |
| 2026-10-02 | Auto-record starts at the session (`/start`, in the runner), not when the first person joins (in the bot); the bot captures per-speaker audio from its start; the greeting and closing message are stored by the bot when spoken | v2's bot task called the runner's `start_recording_for_room` itself, but only the runner holds the egress key, so **auto-record recorded no video** on v2. And in v1 and v2 the greeting (1 s after someone joins) played before the room recording, the bot's own audio capture and the transcript caught it | — |
| 2026-10-02 | Pipecat 1.4.0 → **1.12.0** (latest) on `PipelineWorker`/`WorkerRunner`; model and system prompt as `OpenAILLMService.Settings`; participants looked up by identity (Pipecat 1.8+); no deprecation warnings at runtime. `openai` stays 2.x (the lock keeps it) | Stay on current Pipecat (user's choice); the replaced APIs are removed in 2.0. Verified on 1.12: unit suite, pilot root-cause tests, tone probe 0% crossed, sim-attribution 1/2/3 speakers, a live reply in character | Pipecat 2.0; `openai` 3 when needed |
| 2026-10-02 | The bot's LiveKit input resamples each participant separately (`livekit_input.py`, a 30-line subclass of Pipecat's transport). The planned per-speaker turn restructuring and the bus are **not needed**: with clean audio, 2 and 3 overlapping speakers are attributed correctly | Root cause of F11, found layer by layer: Pipecat's LiveKit input (1.4 and 1.12) shares one stream resampler across participants, so 60-69% of each speaker's audio carried another's voice when they overlapped (0% alone; `tests/probe_transport_attribution.py`). Our routing was correct (content-level unit test) | Pipecat merges and releases the fix we offered, [pipecat-ai/pipecat#6033](https://github.com/pipecat-ai/pipecat/pull/6033) (then delete the subclass); or a live test shows turn-level misattribution with clean audio |
| 2026-10-02 | **No bot machine kept warm on staging** (`bot_pool_min = 0`; user's choice, to save $62/month). The live tests press Prepare for study for 1 session before their scenarios and Stop preparing after; researchers prepare before every study. The baseline mechanism stays: Prepare and its end never go below `bot_pool_min`, and Terraform owns the minimum, so a merge mid-study resets a prepared pool to it (machines running bots are never taken) | A cold bot takes 2–3 min to join (174–180 s measured); before 2026-10-02 nothing pressed Prepare, so every test run and the 18:06 failure started cold | **When staging becomes production:** decide an always-warm baseline for real use |
| 2026-10-02 | Chat from the browser goes through a pure decoder (`chat.py`): only a non-empty LiveKit chat packet becomes a turn, with an ISO time. RTVI (plan iteration 10) deferred: nothing in the browser speaks it | Diagnosis F13: non-object JSON crashed the handler, an empty message became an empty turn, and the numeric timestamp left stored chat turns without a time | A browser feature needs a bot-ready signal |
| 2026-10-02 | Meet keeps no claim, lock or request history (design iteration 9): a room's bot is the runner's running session (`GET /rooms/{room}/session`); a repeated start is the runner's `already_running`, answered 409. Webhooks no longer decide a room's bot | Meet's in-memory stores disagreed with the database after a restart or a missed webhook, and blocked running more than one meet. Postgres already enforces one session per room | Track-subscription and presence stores (display only) move out when meet runs as more than one task |
| 2026-10-02 | Every session end goes through `sessions.end()`: a pure `transition(status, event)` (running → completed / error / ended; ended is final) applied as one compare-and-set UPDATE (design iterations 1, 3). States stay as they are; the plan's `starting`/`ready` wait for a reader | Five writers end sessions; the bot's finalizer and the room-gone sweep wrote unconditionally, so a late writer overwrote an earlier verdict (both reproduced in tests) | Iteration 9 (meet reads the session row): add `ready` when the console shows it |
| 2026-10-02 | A bot leaves when LiveKit's live roster has had no human for a grace: 15 min before anyone has come (`BOT_ARRIVAL_GRACE_SECONDS`), 60 s after the last human left (`BOT_REJOIN_GRACE_SECONDS`). Pure rule in `presence.py` (design iteration 4) | Diagnosis F1: the bot read its own cached roster, so it could leave while a human remained; a refresh ended the session; a bot nobody joined never left | Study data: how late participants arrive, how long refreshes take |
| 2026-10-02 | "Prepare for study" is a console control (user's choice): the runner raises the bot pool's minimum and puts an AWS scheduled action on the group that sets it back to 0 at the end time (≤ 24 h). Terraform ignores the group's `min_size` | Researchers prepare without an engineer or a PR; AWS does the expiry, so no timer or table of ours; a deploy mid-study can't reset a warm pool. The runner may change the bot group only (contract denies services and GPU groups) | Load tests: `BOTS_PER_INSTANCE` (3, a guess from 1 GB per bot on 4 GB) |
| 2026-10-02 | **STT NIM on on-demand `g6.xlarge`, not spot** (user's choice) | Spot never started: AWS's placement score for one spot g6.xlarge was 1/10 in every us-east-1 zone, and spot cost $0.56–0.68/hour against $0.805 on-demand, so it saved ~25% at best. Off by default, so on-demand costs only during tests | Before a study: reserved or savings-plan capacity if it runs for hours a day |
| 2026-10-02 | `g6.xlarge` is the smallest instance that runs the Parakeet NIM | NVIDIA's ASR NIM support matrix: Parakeet 0.6b TDT offline needs 13.75 GB GPU memory, and the ASR NIM needs compute capability 8.0+ and 16 GB VRAM. Fractional L4s (g6f) top out at 11.4 GB, and NVIDIA doesn't document fractional GPU support; T4 (g4dn) is compute capability 7.5. g5.xlarge (A10G) also fits but costs $1.006/hour | NVIDIA ships a smaller profile, or load tests show several sessions per GPU |
| 2026-10-02 | First live test passed 2026-10-02 (bots transcribed by it, no NIM errors); switched off again. Staging's Parakeet NIM is an ECS service on an on-demand `g6.xlarge` group (0 to 1), off unless `stt_nim_enabled`; then bots use it, else Deepgram | Same server and GPU as v1, so measurements carry over; ~$0 idle | Load tests: size it; before a study, on-demand or a warm instance |
| 2026-10-02 | Bots reach the NIM through an internal network load balancer, created only while it runs | Bot tasks are one-off RunTask tasks and can't use Service Connect; an NLB is a stable address with health checks, and already within CI's load-balancer permissions | — |
| 2026-10-02 | The NGC key is a Secrets Manager secret `meetlab-v2/staging/ngc` (`{"username":"$oauthtoken","password":...}`), stored by a person | ECS private-registry pulls need that shape; never in Terraform state | — |
| 2026-10-02 | The model cache lives on the instance disk; each cold start rebuilds it (~20 min) | Simplest; staging runs it only for tests | Before a study: EFS cache or a warm instance |
| 2026-10-02 | No in-process bots (4c PR 6, completes 4c; mislabelled "iteration 9" in #113, which is a stateless meet): `/start` refuses with 500 unless `BOT_DISPATCHER` is `ecs` or `docker`, before any session row exists. The runner no longer mints bot tokens or holds per-speaker sinks | One way to run a bot everywhere (ECS on staging, a container locally), so tests exercise the path production uses; a misconfigured runner can't leave a 'running' session no bot will join | — |
| 2026-10-01 | Video egress uploads with a Terraform-made IAM user `meetlab-v2-staging-egress-writer` (PutObject + AbortMultipartUpload on `recordings/*` only, under the boundary); its access key is minted by a person into SSM and pasted nowhere else | LiveKit Cloud uploads from its own servers, so it needs a key; `assume_role_arn` is Enterprise-only and still needs a base key. Terraform never creates the key, so it is never in state, and CI can't mint one | LiveKit plan with assume-role; then drop the key |
| 2026-10-01 | Only the runner gets the egress key (`EGRESS_S3_KEY_*`); bots and the runner otherwise use their task roles | The runner is what calls LiveKit egress; a bot never needs it | — |
| 2026-10-01 | First-time deployment steps live in `docs/v2-deployment.md` | Steps a person does outside the pipeline (bootstrap, secrets, the egress key) were only in PR threads | — |
| 2026-10-01 | Local bots run as Docker containers (`BOT_DISPATCHER=docker`, the default in compose): each copies the runner's own container with the `bot_task` command, through a socket proxy allowing only container create, start, stop and inspect | Mirrors one ECS task per meeting (D5); a local code edit reaches local bots through the shared mount | — |
| 2026-10-01 | Docker stop is sent without waiting for the container to exit | Docker's stop blocks up to the 120 s grace, ECS StopTask returns at once; meet gives /stop 10 s | — |
| 2026-10-01 | Console Stop goes through the runner (`/stop`: StopTask + close the session) before removing the participant | Removal alone did nothing before the bot joined, and the bot joined anyway (acceptance `stop_early`); removal also threw (500) when the bot wasn't in the room yet | — |
| 2026-10-01 | One running session per room (Postgres partial unique index); a repeated start returns the running session. `request_key` dropped | No caller retries the same request; the real duplicates are two starts for one room (double click, retry after meet's 10 s timeout), which this covers | If a caller ever needs to retry one request across rooms |
| 2026-10-01 | A permission contract (`agent-runner/tests/permission_contract.py`) lists every AWS call the code makes and calls each role must never make; CI simulates it against the deployed roles before the live scenarios | Runtime permission gaps (`ListTasks`) surfaced only in live bots; simulation finds them in seconds. Adding an AWS call to the code means adding it to the contract | — |
| 2026-10-01 | Instances use the **latest** ECS AMI; it is not pinned. Every merge to a deployed branch is a deployment that may replace instances (downtime); during studies, merges are timed around sessions | Pinning means hand-maintaining image IDs; a new AMI can arrive between a PR's plan and its apply, and that is accepted | If an unexpected instance replacement ever hurts a study |
| 2026-10-01 | Live acceptance tests (`acceptance_staging.py`) run after every deploy in CI and from a laptop, same script | Catch failures at every stage; laptop runs alone are unreliable (the Mac sleeps) | — |
| 2026-10-01 | CI acceptance uses its own role `meetlab-v2-acceptance`: 4 named secrets, staging tasks only (list, describe, stop, exec), staging logs only | Least privilege; the apply role doesn't read secrets | — |
| 2026-09-30 | Staging = target architecture on the smallest machines that run it | User's rule: stay close to the vision; right-size at load testing | Load testing |
| 2026-09-30 | Bots run as ECS tasks on staging from 4c PR 1 (`BOT_DISPATCHER=ecs`) | The spike measures the real path through meet; reverting is one env var | — |
| 2026-09-30 | The bot task gets only the session ID; it reads its row and mints its own LiveKit token | No token in RunTask overrides, which anyone with ecs:DescribeTasks can read | — |
| 2026-09-30 | The runner's task role may RunTask only the bot family in this cluster, and Stop/Describe only this cluster's tasks | Least privilege for the one thing it launches | — |
| 2026-09-30 | Local dispatcher: Docker via a socket proxy (create/start/stop/inspect only) | Mirrors ECS; limits what agent-runner can do to the laptop | — |
| 2026-09-30 | 4c takes iterations 2, 5, 6, 7, 8; iterations 1, 3, 4, 9, 10 come after 4c | Only what a detached bot needs to be safe | After 4c PR 6 |
| 2026-09-30 | Staging bots use Deepgram (`STT_MODEL_OVERRIDE=nova-3-general`) | No staging NIM yet; the DB default (Parakeet NIM) left the sanity bot silent | Staging NIM lands (bot-pool PR): remove the override, set `NEMOTRON_STT_URL` |
| 2026-09-30 | App tasks accept each other on host ports (security-group self rule) | In bridge mode, Service Connect proxies talk across instances on host ports; without it meet → agent-runner hung whenever the two landed on different hosts | awsvpc + ENI trunking would allow per-service groups |
| 2026-09-30 | Task definitions are never deregistered (`skip_destroy`) | `ecs:DeregisterTaskDefinition` can't be scoped to a resource; granting it would let CI deregister any project's task definitions | — |
| 2026-09-30 | agent-runner is private; meet reaches it as `http://agent-runner:7860` via ECS Service Connect | meet already proxies the console, SQLAdmin and bot API; no second load balancer | — |
| 2026-09-30 | Bots still run inside agent-runner on staging, for now | Parity first: v1 behaviour on v2 infra gives a baseline before per-session bot tasks | Bot-pool PR (design iterations 7–8) |
| 2026-09-30 | Staging recordings stay on the container disk | Tasks have no S3 role yet | Bot-pool PR |
| 2026-09-30 | Staging reuses v1's vendor keys and LiveKit project, for sanity runs only | Fastest path to a working call; the user chose it | Before load tests: own keys. Room names share v1's LiveKit project, so staging rooms must not reuse study room names |
| 2026-09-30 | Secrets copied EB → SSM by `infra/v2/seed-staging-secrets.sh`, run by a person | Values never printed or in Terraform state; CI roles can't read v1's EB settings | — |
| 2026-09-30 | `BOT_RUNNER_SECRET` generated for staging, not copied | Only meet and agent-runner use it; no reason to share it with v1 | — |
| 2026-09-30 | Staging URL `meet-staging.wwbp.org`, reusing the `*.wwbp.org` certificate | One level under the zone, so no new certificate; `staging.wwbp.org` already belongs to another project. CI may change only this one record in the shared zone | — |
| 2026-09-30 | Every CI-created role is `meetlab-v2-staging-*` and carries `meetlab-v2-boundary` | Otherwise the apply role could create a role more powerful than itself. CI can't edit the boundary, its own roles, or remove a boundary | — |
| 2026-09-30 | Bridge networking on EC2, not awsvpc | awsvpc gives each task a network interface; a `c6i.xlarge` has 4, which would cap bots at 3 per instance | If per-task security groups are needed (then ENI trunking) |
| 2026-09-30 | Separate capacity providers for services and bots | Bots scale 0..N per study; meet and the control API stay up | — |
| 2026-09-30 | Pipeline order: images → apply; apply waits for healthy services, and a failing deploy rolls back | The task definition points at the SHA just pushed | — |
| 2026-09-30 | IMDSv2 required on instances | A container can't read the instance role with a plain GET | — |
| 2026-09-30 | CI permissions are inline policies, at 8.3k of the 10.2k-character limit per role (2026-10-01, after the egress user) | Simplest while small | Move to managed policies when the next PR would pass the limit |
| 2026-09-30 | Images tagged with the git SHA, immutable; one image per service for every environment | A task definition's image can never change under it; staging and prod run the same bytes | — |
| 2026-09-30 | The bot task reuses the agent-runner image with another command | One image to build and scan; the bot code already lives there | If the bot's dependencies diverge |
| 2026-09-30 | One pipeline on `v2`: test → apply → images | Chained workflows only run from the default branch, and images need the repositories the apply creates | When `v2` becomes the default branch |
| 2026-09-30 | CI roles can't read or write objects in `meetlab-v2-*` buckets | They hold study recordings, and any PR can assume the plan role | A task that needs objects gets its own task role |
| 2026-09-30 | RDS `db.t4g.small`, single-AZ, deletion protection on | ~10 writes/s at 100 sessions; v1's database has deletion protection off | Size up and go multi-AZ before a study |
| 2026-09-30 | Postgres 17 with the default parameter group | Same major version as v1, so data moves by dump/restore; SSL is already forced by default | — |
| 2026-09-30 | DB master password managed by RDS in Secrets Manager | Never in Terraform state or env vars | — |
| 2026-09-30 | One NAT for staging | Saves $33/month; losing us-east-1a only cuts staging egress | Before staging becomes production: one NAT per AZ |
| 2026-09-30 | VPC `10.20.0.0/16` | Clear of v1's `10.0.0.0/16`, so the two can peer | — |
| 2026-09-30 | Apply gate = merging a PR into `v2` | GitHub's free plan has no required reviewers on private repos; the `staging` environment still accepts deploys from `v2` only | If the org upgrades: add a required reviewer |
| 2026-09-30 | No nightly drift check yet | Scheduled workflows run only from the default branch (`main`, frozen) | When `v2` becomes the default branch |
| 2026-09-30 | Bootstrap is applied by a person | The apply role must not be able to widen its own permissions | Never |
| 2026-09-30 | Permissions grow per PR, scoped by `meetlab-v2` name or `Project=meetlab-v2` tag | Shared lab account; the old Actions role trusts every `wwbp/*` repo | — |
| 2026-09-30 | v2 gets its own NIM, switched on only for tests | Load tests must not touch v1's STT | — |
| 2026-09-30 | v2 gets its own LiveKit project and key | Load tests must not use v1's quota | — |
| 2026-09-30 | ECS on EC2 now, not EB | Per-session bot tasks and scaling as code; no dispatcher to write | — |
| 2026-09-30 | `main` frozen (CD, Capacity, Deploy STT NIM disabled); work on `v2` | No accidental v1 deploys while v2 is built | Cutover |
| 2026-09-30 | Staging is built first; it becomes production once tested | Test the whole stack before it carries a study | Cutover |
