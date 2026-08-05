# Distillation audit — what's real, what's dead, what's untested

**Status: in progress.** A running record, not a plan of record. Findings and
recommendations accumulate here per iteration; **cut/keep decisions get made
together at the end**, once the whole picture is visible.

Method: for each component, three questions — *does it actually do anything on
the production configuration?*, *is it exposed to anyone?*, and *does a test
prove its effect (not just its storage)?*

| Iteration | Scope | Status |
|-----------|-------|--------|
| 1 | `bot_config` surface — every knob | ✅ complete |
| 2 | agent-runner — endpoints and modules | ◐ endpoints done, modules partial |
| 2b | dev/prod parity | ✅ complete |
| 3 | meet — routes, features, auth allow-list | ✅ complete — **found a live data exposure** |
| 4 | Tests vs. real use cases | ✅ complete — real 7/30 production data is now the regression fixture |

> **⚠️ Iteration 3 found unauthenticated public access to pilot participants'
> audio and video recordings in production.** Patched on `main` in this pass;
> needs an expedited merge. Details below.

---

## Iteration 1 — the config surface

13 knobs. Behaviour below is **on the model production actually runs**
(`parakeet-tdt-0.6b-v2`), which is the only column that matters.

| Knob | Works on prod? | In console? | Effect test? |
|------|----------------|-------------|--------------|
| `stt_endpointing_ms` | ✅ **the real turn-taking control** | ❌ **hidden** | ✅ |
| `tts_aggregation_mode` | ✅ | ❌ **hidden** | ❌ |
| `stt_model` | ✅ | ✅ | ✅ dispatch tested |
| `system_prompt` | ✅ | ✅ | ❌ round-trip only |
| `greeting` | ✅ | ✅ | ❌ round-trip only |
| `llm_model` | ✅ | ✅ | ❌ round-trip only |
| `tts_voice` | ✅ | ✅ | ❌ round-trip only |
| `tts_provider` | ✅ | ✅ | ❌ round-trip only |
| `auto_record` | ✅ | ✅ | partial |
| `session_limit_minutes` | ✅ (meet-side; the bot never reads it) | ✅ | ✅ `meet/lib` |
| `vad_stop_secs` | ❌ **dead on every path** | ✅ | ✅ proven dead |
| `stt_vad_mode` | ❌ **inert** — only read on the `gpt-*` branch | ✅ | ❌ |
| `stt_delay` | ❌ **inert** — only read on the `gpt-*` branch | ✅ | ❌ |

### The finding

**The console exposes three controls that do nothing on the production STT
model, and hides the two that actually govern turn-taking and speech chunking.**

An operator opening that page to fix responsiveness has no working lever in front
of them. That is precisely how `vad_stop_secs=0.1` ended up written to all 42
config rows while the setting that mattered — `stt_endpointing_ms` — sat at its
100 ms default, invisible and untouched. The config UI didn't just fail to help;
it actively absorbed the effort.

Note the distinction between the three broken knobs: `vad_stop_secs` is genuinely
dead code (read once, into a log string). `stt_vad_mode` and `stt_delay` are
correctly implemented — wired to a code path we don't use.

### Test-coverage nuance

`test_bot.py` is stronger than the raw numbers suggest: it has real behavioural
tests for STT dispatch and for endpointing on both the whisper and parakeet
chains. The weakness is elsewhere — the `/config` tests in `test_runner_start.py`
are all **round-trip** ("PUT accepts it, GET echoes it back"). That is storage
coverage wearing feature coverage's clothes: every one of those tests passes
today for `vad_stop_secs`, a field that does nothing.

### Recommendations (not yet decided)

1. Cut `vad_stop_secs` end-to-end — column, API validation, console slider.
2. Surface `stt_endpointing_ms` in the console in its place.
3. Add effect-tests for the five round-trip-only knobs, so "configurable" means
   "verified to change behaviour."
4. Consider surfacing `tts_aggregation_mode` or dropping it to a constant.

---

## Iteration 2 — agent-runner

### Endpoints: 15 total, measured against 30 days of production traffic

| Endpoint | 30-day hits | Verdict |
|----------|-------------|---------|
| `GET /health` | 353,427 | ALB check — the only public endpoint, correctly so |
| `GET /conversations` | 7,784 | console polling |
| `GET /config` | 141 | live |
| `PUT /config` | 61 | live |
| `POST /start` | 51 | live — 36 of these were the pilot |
| `POST /events` | 35 | live |
| `GET /configs` | 30 | live |
| `POST /recordings/reconcile` | 18 | live (the backstop that's doing all the work) |
| `GET /media-files/{id}/download` | ~12 | lightly used |
| `GET /conversations/{id}/audio-tracks/download` | ~4 | lightly used |
| `POST /conversations/{id}/transcript` | ~4 | lightly used |
| `POST /recordings/start` | 2 | manual path; auto-record calls it in-process |
| `POST /recordings/stop` | **0** | dead surface |
| `POST /conversations/reconcile` | **0** | dead surface |
| `PATCH /media-files/{file_id}` | **0** | dead — *see below* |

`PATCH /media-files/{file_id}` is documented as "called by the meet webhook on
egress_ended". It has zero hits because **that webhook has never fired in
production** (postmortem RC4). The dead endpoint and the broken recording status
are the same fact seen from two directions — good corroboration that the traffic
data and the log data agree.

### Security posture — checked, and clean

The runner is directly internet-facing and under constant opportunistic scanning:
727 hits on `/.env`, 231 on `/.git/config`, 133 on `/api/admin/token`, and
hundreds of phpunit RCE probe paths. **All of them 404.** Auth is real:
`/config` shows 191×200 alongside 11×401, so unauthorised callers are being
rejected.

**14 of 15 endpoints require `verify_api_key`; only `/health` is public.** That
is the correct configuration.

*(An earlier pass in this audit mis-parsed multi-line function signatures and
flagged four endpoints as public. That was wrong — the finding above is the
verified one.)*

### Modules — partial

| Module | LOC | Dedicated tests |
|--------|-----|-----------------|
| `runner.py` | 1304 | 5 files |
| `bot.py` | 1028 | 4 files |
| `multi_speaker_stt.py` | 307 | **1 file** — thin for a core component |
| `audio_tracks.py` | 256 | 2 files |
| `storage.py` | 151 | 1 file |
| `transcript.py` | 130 | 1 file |
| `nemotron_stt.py` | 82 | 2 files |
| `interruption.py` | 76 | 2 files |
| `metrics.py` | 71 | **0** |
| `mock_services.py` | 51 | 1 file |
| `config.py` | 49 | 2 files |
| `runner_types.py` | 14 | 0 (types only — fine) |

Flagged for iteration 4: `multi_speaker_stt.py` is 307 lines of per-participant
routing and queueing that all speech flows through, with one test file.
`metrics.py` has no tests, which is how a metric nobody could act on (RC3's
talk-over gauge) survived so long.

---

## Iteration 2b — dev/prod parity

Principle set 2026-08-05: **dev and prod should be the same environment.** Applied
honestly, STT is one of six divergences.

| # | Thing | Dev | Prod | Consequence |
|---|-------|-----|------|-------------|
| P1 | **STT** | `whisper-base` in-process | Parakeet NIM | Benchmarks describe a system we don't run — see below |
| P2 | **Egress** | `livekit/egress:v1.13.0` container, unlimited | LiveKit Cloud, ~3 concurrent | **RC4 cannot reproduce locally, by construction** |
| P3 | **LiveKit** | self-hosted `--dev` (devkey/secret, security checks off) | LiveKit Cloud *test* tier | Auth, quotas and limits untested until prod |
| P4 | **Runner auth** | `BOT_RUNNER_SECRET=` (empty → header omitted) | secret enforced | The auth path is never exercised in dev |
| P5 | **Storage** | local filesystem | `s3` | S3 failure modes only appear in prod |
| P6 | **Machine size** | 12 vCPU / 8 GB Docker VM | **2 vCPU / 4 GB** t3.medium | Dev is ~6× prod's CPU; local load tests systematically flatter us |

### What the divergence has already cost

**P1 wrote a false record.** `docs/performance-tests.md` still presents a
Deepgram `nova-3-general` configuration as our "current best result (2026-06-10)"
— a configuration production has never run. Months of latency work was tuned
against the wrong stack.

**P2 made RC4 unreproducible.** The local egress container has no concurrency
quota, so the `concurrent egress sessions limit exceeded` failure that cost us
half the pilot's video *literally cannot happen* on a developer machine. No
amount of local testing would have found it.

**P6 inverts the usual safety margin.** Dev is more powerful than production, so
anything that passes locally may still saturate prod — which is exactly what
happened at 7 concurrent sessions and 85% CPU.

### Path to parity for P1 (the mechanism already exists)

`.devcontainer/docker-compose.yml:89` already accepts `NEMOTRON_STT_URL`, and
line 86 allows clearing `STT_MODEL_OVERRIDE`. So this is configuration, not code:

1. **Stand up a dedicated dev/staging NIM.** `infra/stt-nim` is already
   parameterised (`instance_type`, `subnet_id`, `nim_image`), so a second
   environment is a var file plus a separate state key.
2. **Access it over SSM port-forwarding** — the NIM instance already carries
   `AmazonSSMManagedInstanceCore` and is SSM-Online today, so no VPN is needed.
3. **Do not point dev at the production NIM.** It is a single g6.xlarge with no
   ASG (scale plan C7); dev traffic would contend with live sessions on a known
   SPOF.
4. **Cost:** roughly $0.80/hr on-demand for a g6.xlarge, so ~$580/month
   always-on *(estimate — confirm current pricing)*. Schedule it off outside
   working hours, or start it on demand, to cut that substantially.

This same instance is what the scale plan's Phase 3 load test needs, so it is not
purely a developer-convenience cost.

Running the NIM on a developer laptop is not an option — the container is CUDA
x86, and the team is on Apple Silicon.

## Iteration 3 — meet

### 🔴 CRITICAL, LIVE: unauthenticated access to participant recordings

**Verified against production 2026-08-05.** No credentials, no cookie, plain HTTP:

```
GET /api/meetings                                   → 200   all 167 conversations
GET /api/meetings/{id}/audio-tracks/download        → 200   141 KB application/zip
GET /api/meetings/{id}/files/{fileId}/download      → 302   presigned S3 URL (video)
```

The first call enumerates every session with its room name, bot identity,
timestamps and media-file IDs. The next two turn any of those IDs into the actual
per-speaker audio and the composite video of real pilot participants. The whole
chain is walkable by anyone who finds the hostname.

`/config` correctly redirects to `/login`, so the console *appears* protected.
`/meetings` and everything under `/api/meetings` were simply never added to the
middleware matcher.

**The fix already existed and was never merged.** `feat/pipeline-event-log`
contains the exact matcher additions (`/meetings`, `/api/meetings/:path*`). It was
found and patched on that branch, and production has been exposed the entire time
— including throughout the pilot, whose participants are the people in those
recordings.

Patched on `main` in this pass, plus `meet/middleware.test.ts` ported so the
allow-list is a derived fact rather than something to remember. **Needs an
expedited merge.**

### 🟠 HIGH: `/api/record/*` has no auth of any kind

`GET /api/record/start?roomName=<anything>` takes a room name from a query string
and starts an egress. No session, no token, no room scoping. Anyone can start or
stop recording on any room — and each call consumes one of the ~3 available
concurrent egress slots, so it doubles as a denial-of-recording vector.

This one is *knowingly* public — a participant's browser calls it during a call,
so console auth would break the in-call record button. CLAUDE.md already flags
that it "still needs room-scoped auth". It still doesn't have any. Not patched
here: it needs a real scheme (signed room token), not an allow-list entry.

### Why the allow-list model keeps failing

`middleware.ts` protects an explicit list of paths, so **everything not listed is
public by default**. Auth is opt-in, and the failure mode is silent — a new page
ships world-readable and nothing complains. `/meetings` is the second time this
has bitten (the first was caught on a branch that never merged).

The ported test converts it from a thing-to-remember into a thing-that-fails-CI:
it enumerates `app/(shell)` from disk and asserts every page is covered. Longer
term the model should invert — protect everything, allow-list the genuinely
public surface (`/rooms/*`, `/start/*`, `/api/connection-details`,
`/api/start-link`, `/api/console/login`, `/api/health`).

### Route inventory

| Route | Auth | Verdict |
|-------|------|---------|
| `/`, `/config`, `/start-links`, `/db/*` | console session | correct |
| `/meetings`, `/api/meetings/*` | **none → now console session** | **was exposed; patched** |
| `/api/concierge/*` | console session | correct |
| `/api/concierge/webhooks/livekit` | LiveKit JWT | correct — deliberately bypasses console auth |
| `/api/console/login` | none | correct |
| `/api/health` | none | correct |
| `/api/connection-details` | none | by design (participant browser) |
| `/api/start-link` | signed token | correct — verifies via `verifyStartLink` |
| `/api/record/start`, `/stop` | **none** | **needs room-scoped auth** |
| `/rooms/[roomName]`, `/start/[token]` | none | by design |
| `/custom`, `/desk` | none | pages outside `(shell)` — see below |

`app/desk/page.tsx` and `app/custom/page.tsx` sit outside the `(shell)` group, so
the ported test does not enumerate them. `/desk` is the older console UI. Worth
deciding whether it still exists at all — a cut candidate.

### `pnpm lint` has been broken since the Next 16 upgrade

`"lint": "next lint"` — removed in Next 16, so it now reads `lint` as a directory
and exits 1. CLAUDE.md states `make test-unit` runs "meet lint"; the Makefile
actually runs `pnpm test` only, and CI runs `make test`. **No linting has run in
CI for some time**, and nothing surfaced that because the broken script is never
invoked.

## Iteration 4 — tests vs. real use cases

The suites are large (286 agent-runner, 199 meet) but were uneven in one specific
way: **nothing tested whether a feature was still connected to anything.** Every
`bot_config` round-trip test passed for `vad_stop_secs` throughout the months it
did nothing, because storage was never the broken part.

Three additions, all built around that failure mode.

### `test_config_contract.py` — a knob must change behaviour

One generic test rather than per-field assertions: every `bot_config` column must
be read in `bot.py` somewhere **other than inside an f-string**. A field read only
into a log message is dead.

Verified to have teeth — unmarking `vad_stop_secs` produces:

```
AssertionError: ['vad_stop_secs: read 1× but only inside f-strings (log-only)'] != []
```

New knobs are covered the day they are added, and a knob that quietly loses its
last real reader fails immediately. Two escape hatches, both requiring a written
reason: `NOT_PIPELINE_FIELDS` (e.g. `session_limit_minutes`, which is genuinely
meet-side) and `KNOWN_DEAD_FIELDS` (tracked cleanup, asserted to *stay* dead so
resurrection is noticed).

Worth noting what it does **not** flag: `stt_vad_mode` and `stt_delay` are read
outside f-strings, so they pass. They are not dead code — they are correctly
wired to the `gpt-*` branch we do not run. Different problem, fixed by D1, and the
test is honest about the distinction rather than lumping them together.

### `test_pilot_regression_dataset.py` — real production data as the fixture

`fixtures/pilot_2026_07_30.json`: 72 measured turns across 13 sessions,
2026-07-30 15:05–16:30 UTC, pulled from `utterances.meta` in production. Not
synthetic — this is what participants actually experienced on the worst day.

**De-identified: structural features only** — fragment counts, character lengths,
per-stage timings, spike flags, queue depth. No transcript text, room names,
participant names or real conversation ids, and a test enforces that. These are
real people whose recordings were publicly downloadable until today; their words
do not belong in the repo.

It does two jobs:

- **Pins the failure.** p50 2876 ms, p90 7026 ms, max 11097 ms, 48.6% of turns
  over 3s. The postmortem's numbers are now a committed artefact, not a claim.
- **Defines the bar.** `ACCEPTANCE` (p50 ≤ 1500 ms, p90 ≤ 2500 ms, ≤10% over 3s)
  is what a fixed system must achieve on the same workload, and
  `test_the_acceptance_bar_is_not_already_met` asserts today's data misses all
  three thresholds — so the gate provably has teeth.

The RC1 diagnosis is now asserted against real data rather than argued:

| Test | What it pins |
|------|--------------|
| `test_spikes_cluster_at_the_5s_timeout_rather_than_forming_a_tail` | every spike sits in a tight band just above 5.0s (σ < 400 ms). A slow provider makes a smooth tail; a fixed timeout makes a band. |
| `test_spikes_are_not_explained_by_queue_backlog` | queue depth ≤ 1 during every spike — rules out the cause we originally suspected |
| `test_latency_scales_with_how_much_the_participant_actually_said` | 1-fragment turns p50 < 1s, 5+-fragment turns p50 > 4s, ratio > 4× |
| `test_the_model_and_the_voice_were_never_the_problem` | LLM p90 < 2.5s, TTS p90 < 800 ms, and STT is >40% of the total on all ten worst turns |

That last one earns its place defensively: if a future regression *is* in the LLM
or TTS, it stops us reflexively blaming the turn timeout again.

### `scripts/verify-prod-auth.sh` — check the deploy actually closed the hole

`middleware.test.ts` guards the allow-list in CI; this checks what production
returns. Run it after any deploy touching `meet/middleware.ts`.

**404 and 405 deliberately count as failures.** Auth must reject before the
handler looks anything up, so a 404 for a non-existent id proves nothing about
what a real id returns — and treating it as success is how you write a check that
passes against an exposed system. The first draft of this script made exactly that
mistake and reported ✅ on the download chain.

Current baseline against production, fix not yet deployed:

```
✅ /                          307      ❌ /meetings                     200  EXPOSED
✅ /config                    307      ❌ /api/meetings                 200  EXPOSED
✅ /start-links               307      ❌ /api/meetings/reconcile       405
✅ /api/concierge/rooms       401      ❌ …/audio-tracks/download       404
✅ /api/console/config        401      ❌ …/files/{id}/download         404
                                       ❌ …/transcript                  405
```

Every line must read ✅ after the deploy.

### Remaining gaps (not yet filled)

- `metrics.py` still has no tests. The metric *names* are a contract —
  `scripts/grafana-prom.sh` and any dashboard break silently on a rename.
- `multi_speaker_stt.py`: 307 lines that all speech flows through, one test file.
- `/api/record/*` has no auth to test yet; the test documents the gap rather than
  asserting the fix.

## Decision log

| # | Decision | Status | Notes |
|---|----------|--------|-------|
| D1 | **STT: parakeet only, everywhere.** Cut the `gpt-*`, Deepgram **and** whisper paths; `stt_vad_mode` and `stt_delay` die with the `gpt-*` branch. | **Decided 2026-08-05** | I proposed keeping `whisper-base` as a dev shim; rejected — dev and prod must be the same environment. Requires a dev/staging NIM before the whisper path can be removed (see Iteration 2b). Sequence matters: **stand up the dev NIM first, then delete the paths**, or the dev loop breaks in between. |
| D2 | Process: record findings per iteration, make cut/keep calls together at the end. | Agreed 2026-08-05 | This document is that record. |
| D3 | **Dev/prod parity is a standing principle**, not just an STT decision. | **Decided 2026-08-05** | Six divergences catalogued in Iteration 2b. P2 (egress) is the one that let RC4 through; P6 (dev is 6× prod's CPU) is the one that will keep biting during scale-up. |
| D4 | **Egress: move production to a paid LiveKit plan.** | **Owned by Abby, deferred** | Not being actioned now. Until it lands, production keeps the ~3-concurrent-egress ceiling on the `test-meetlab-*` project, so **video-on-all-sessions is not achievable at 50 concurrent** regardless of anything else we fix. The RC4 retry + visible-failure work is still worth doing independently — it turns silent loss into a recorded failure. Parity gap P2 (local egress has no quota) also can't close until the prod side is settled. |
| D5 | **Benchmark only the production stack.** | **Decided 2026-08-05** | `run_benchmark_matrix.py` STT matrix cut to `parakeet-tdt-0.6b-v2` only. Now requires a reachable `NEMOTRON_STT_URL` — no local-only fallback, by design. LLM/TTS axes kept as genuine experiment dimensions; both endpointing values (100/200) kept so the RC2 fix can be measured against the pilot's actual shape. |
| D6 | **Scale production capacity**, as code via `.ebextensions`. | **Written, awaiting merge** | `agent-runner`: t3.medium → c6i.xlarge, Min 2 / Max 6, CPU-triggered. `meet`: t3.medium → c6i.large, **pinned Min 1 / Max 1** with the reason written into the file. Deploys through the normal PR → CI → CD path; no CLI changes to the environments. |

### Consequences of D1 to work through

- Deleting the Deepgram path also deletes the documented best-latency baseline
  (`nova-3-general`, 2026-06-10) and the benchmark matrix's comparison axis.
- It removes the only managed fallback when the single g6.xlarge NIM is down.
  See `docs/scale-readiness-plan.md` C7 — that instance is already a SPOF with no
  ASG, and this makes it a harder one.
- `stt_model` stops being a free-text provider selector and becomes effectively a
  constant. That is a simplification worth having, but it is an API change.

---

## Running list of cut candidates

Nothing here is removed yet.

- `bot_config.vad_stop_secs` — column, API validation, console slider *(dead)*
- `bot_config.stt_vad_mode`, `bot_config.stt_delay` — with the `gpt-*` path *(D1)*
- `POST /recordings/stop`, `POST /conversations/reconcile` — zero traffic in 30 days
- `PATCH /media-files/{file_id}` — decide *after* fixing the egress webhook; it is
  dead only because its caller is broken
- `agent-runner/soak-results-*.json` — 3 committed result artifacts
- `CLAUDE.md:155` — claims `web-client/` is retained; the directory is gone
- CLAUDE.md's "Token TTL is 15 minutes" — it is 5 minutes
  (`connection-details/route.ts:168`)
