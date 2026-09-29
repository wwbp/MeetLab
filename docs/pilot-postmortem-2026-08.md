# Pilot postmortem — voice bot, 29 Jul / 30 Jul / 5 Aug 2026

**Status:** investigation complete, no fixes applied yet.
**Prod version during pilot:** `a4e24bb` (deployed 2026-07-29 21:38 UTC) — still live.
**Author's note:** every number below is pulled from prod (Grafana Cloud Prometheus, CloudWatch
Logs, and the `meetlab` RDS Postgres). Queries are listed in [Appendix A](#appendix-a-how-to-reproduce)
so anyone can re-run them.

---

## TL;DR

All five reported complaints are real, reproduced in telemetry, and trace back to **four root
causes** — three of which are one-line configuration defaults we never overrode.

| # | User complaint | Verdict | Root cause | Fix size |
|---|---|---|---|---|
| 1 | "Glitching and lagging" | **Confirmed** — 49% of turns >3s on 30 Jul | Pipecat `user_turn_stop_timeout` default **5.0s**, never overridden | 1 line |
| 2 | "Doesn't let the participant finish" | **Confirmed** | Endpointing set to **100 ms** of silence | 1 config value |
| 3 | "Chained answers for the two responses" | **Confirmed** | Same 5s window merges two statements into one commit | same as #1 |
| 4 | "Video recording broken / still shows recording" | **Confirmed** — **50% of real sessions have no video** | LiveKit concurrent-egress 429, **no retry**; plus egress webhook has never once fired | small but real |
| 5 | "Cutting off the participants — interruption handling" | **Confirmed** — bot talks over users for up to **4.7s** | Interruption is **measured but never implemented** | needs real work |

The single highest-leverage fix is #1/#3: they are the same bug, and it is a default we never set.

---

## Pilot scope

Three days of real traffic. 7/29 was almost entirely link smoke-testing; the real user pilot is
**30 Jul** and **5 Aug**.

| Day | Bot sessions | With real human speech | Empty (link tests) | Avg duration |
|-----|--------------|------------------------|--------------------|--------------|
| 2026-07-29 | 9 | 1 | 8 | 283s |
| 2026-07-30 | 19 | 15 | 4 | 235s |
| 2026-08-05 | 8 | 8 | 0 | 329s |
| **Total** | **36** | **24** | 12 | — |

Traffic windows (UTC): 30 Jul 14:30–17:00, 5 Aug 01:35–02:05.
All 36 sessions closed cleanly (`status='completed'`) — **nothing crashed**. Every problem below
happened inside a nominally healthy session, which is exactly why none of it paged anyone.

---

## Finding 1 — Lag is real, and it is the turn-commit timeout (not the network, not the model)

### What users felt

End-to-end response delay (user stops speaking → first bot audio):

| Day | Turns | p50 | p90 | p99 | max | **% over 3s** |
|-----|-------|-----|-----|-----|-----|---------------|
| 2026-07-30 | 72 | 2864 ms | 7007 ms | 8863 ms | **11097 ms** | **48.6%** |
| 2026-08-05 | 95 | 1526 ms | 6403 ms | 7129 ms | 7481 ms | **27.4%** |

On the biggest pilot day, **half of all turns took more than three seconds** and the worst took
**11 seconds**.

### Where the time goes

It is not the LLM and not TTS. Both are healthy:

```
metric              p50      p90      p95      p99
stt_latency_ms      397     6556     7028     7406    ← the entire problem
e2e_latency_ms     1119     1454     1747     2530
llm_ttft_ms         572      788      899     1547
tts_ttfb_ms         215      287      315      433
sentence_agg_ms     213      343      410      476
```

`stt_latency_ms` is p50 **397 ms** but p90 **6556 ms**. That is not a heavy tail — it is **bimodal**.
Something is adding a fixed penalty to a subset of turns.

### The fixed penalty is 5.0 seconds

64 spike warnings were logged across the pilot. They cluster in a suspiciously narrow band:

```
STT latency spike: stt_ms=5205 ... queue_depth=0 content="abhay: I don't have much idea about the candidates. Can you go over their detail"
STT latency spike: stt_ms=5198 ... queue_depth=0 content='Michelle: I Michelle: I would say yes. Michelle: Um Michelle: I currently rank t'
STT latency spike: stt_ms=5251 ... queue_depth=0 content='rahul: Yeah yeah. rahul: So I thought B as a rahul: Much better kind of'
STT latency spike: stt_ms=5534 ... queue_depth=0 content='Alex: Well for me the strongest candidate seems to be candidate B.'
STT latency spike: stt_ms=6041 ... queue_depth=1 content="Alex: So candidate C, I don't think we should vote for him. Alex: Because of his"
STT latency spike: stt_ms=5397 ... queue_depth=0 content='Jesus : Yes.'
```

Two things to notice:

1. **`queue_depth=0` on nearly every spike.** This is *not* a backlog, *not* GPU saturation, and
   *not* the STT provider stalling. Our Phase-1 diagnostics were built to discriminate exactly
   this, and they cleanly rule out the causes we suspected.
2. **Every spike lands between 5.2s and 6.1s.** A constant, not a distribution.

The constant is in Pipecat. From `pipecat/processors/aggregators/llm_response_universal.py`
(v1.4.0, read out of the running prod container):

```python
@dataclass
class LLMUserAggregatorParams:
    audio_idle_timeout: float = 1.0
    user_turn_stop_timeout: float = 5.0     # ← "Time in seconds to wait before
    user_idle_timeout: float = 0            #    considering the user's turn finished"
    vad_analyzer: VADAnalyzer | None = None
```

And in [`agent-runner/bot.py:418-421`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/bot.py#L418-L421) we construct the aggregator
with **no VAD and no timeout override**:

```python
context_aggregator = LLMContextAggregatorPair(
    context,
    user_params=LLMUserAggregatorParams(vad_analyzer=None),
)
```

With `vad_analyzer=None` the aggregator has no voice signal to decide the turn ended, so it falls
back to the 5-second timer. **We never passed `user_turn_stop_timeout`, so it is 5.0s in production.**

### Proof: the system punishes the users who engage most

Correlating each user turn's shape against the latency of the reply it produced (268 turns):

| User turn shape | Turns | Avg chars | p50 STT | **% that hit the 5s timeout** |
|-----------------|-------|-----------|---------|-------------------------------|
| 1 fragment | 150 | 34 | 390 ms | 13.3% |
| 2 fragments | 44 | 55 | 423 ms | 29.5% |
| 3–4 fragments | 42 | 111 | 402 ms | 31.0% |
| **5+ fragments** | **32** | **314** | **5198 ms** | **53.1%** |

A clipped one-word answer gets a reply in **0.4s**. A thoughtful paragraph gets one in **5.2s**.
The bot is fastest with the people who say the least. That is the "lagging" complaint precisely.

---

## Finding 2 — Endpointing is 100 ms, and the knob people were actually turning does nothing

### The real setting

The per-participant VAD is built from `stt_endpointing_ms`
([`bot.py:158-160`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/bot.py#L158-L160)):

```python
endpointing_ms = getattr(bot_config, "stt_endpointing_ms", 200) or 200
return VADProcessor(
    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=endpointing_ms / 1000))
)
```

Every per-meeting config used in the pilot has `stt_endpointing_ms = 100`. **The bot decides you
have finished speaking after 100 ms of silence.** Natural conversational pauses — breathing,
thinking, "um" — are 200–1000 ms. So a single sentence gets shredded:

```
'rahul: Yeah. rahul: And uh rahul: Uh we have not uh rahul: Any rahul: concrete e…'
'karonzo: No karonzo: might be karonzo: I was told about big karonzo: I think kar…'
'Michelle: They see it. Michelle: She Michelle: Um but less Michelle: committee e…'
```

Average 2.1 fragments per turn; **worst single turn was split into 31 fragments**.

### The dead knob

`bot_config.vad_stop_secs` is set to **0.1** on all 42 config rows — someone was clearly trying to
tune responsiveness. It has **no effect whatsoever**. Its only appearance in the pipeline is inside
a log string ([`bot.py:346`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/bot.py#L346)):

```python
f"model={bot_config.llm_model} voice={bot_config.tts_voice} vad={bot_config.vad_stop_secs}s"
```

Yet it is a full-stack feature: DB column → API validation in `runner.py:552` → a slider in the
admin UI at `meet/app/(shell)/config/page.tsx:204`. **Operators have a knob in the console that
prints a number to a log file and changes nothing about the bot's behaviour.**

### Users said so out loud

The pilot transcripts contain participants telling us directly:

```
'Michelle: I can't understand what you're saying, but Michelle: I Michelle: I am …'
"Michelle: I want candidate A. I I Michelle: I don't understand what you're sayin…"
'Michelle: And even Michelle: Sorry?'
'Michelle: that that organization had received. So Michelle: So Michelle: Sorry?'
```

"Sorry?" recurring mid-turn is the signature of the bot cutting in.

---

## Finding 3 — Chained answers

Two distinct mechanisms, both downstream of the 5s window:

1. **Merged commits (dominant).** 33 user turns were 5+ fragments merged into a single context
   commit. When a participant makes two separate points separated by a pause, the 5s window
   swallows both and the LLM answers both in one breath — the "chained answers for the two
   responses" people described.
2. **Literal back-to-back replies.** 13 of 457 bot turns (2.8%) follow another bot turn with no
   human turn between. Median gap **5.36s** — the 5s timer firing twice. Worst case, 1.0s apart:

   ```
   reply 1: "Thanks, I hear that you are voting for A as your"
   reply 2: "final Sure, what do the participants think is the single most important reason to vote"
   ```

   Note reply 1 is also cut mid-sentence — the two are one response torn in half.

**Correction to an earlier read:** a first pass suggested ~28% of turns merged multiple *speakers*.
That was a regex artifact (it captured trailing words as speaker labels). Measured properly,
**cross-speaker merging is 1.4% (6 turns)**. The chaining is within a single speaker, not across
participants.

---

## Finding 4 — Half of all real sessions have no video

Auto-record is on (`auto_record=true` on every config), so **every session should have a recording.**

| Day | Real sessions | Missing video entirely | Coverage |
|-----|---------------|------------------------|----------|
| 2026-07-30 | 15 | **9** | 40% |
| 2026-08-05 | 8 | **3** | 63% |
| **Total** | **24** | **12** | **50%** |

### Cause A — LiveKit concurrent egress limit, with no retry

```
2026-07-30 15:09:24 | ERROR | runner:start_recording_for_room:883 - recording start LiveKit error:
  room=link-ms7nazgl-838a00ae error=TwirpError(code=resource_exhausted,
  message=concurrent egress sessions limit exceeded, status=429)
2026-07-30 15:09:24 | INFO  | bot:_auto_record:836 - auto_record: start_recording_for_room → 502
  {'error': 'LiveKit: TwirpError(code=resource_exhausted, message=concurrent egress sessions limit exceeded, status=429)'}
```

| Day | Rooms that hit 429 | Retry attempts |
|-----|--------------------|----------------|
| 2026-07-30 | 12 | **0** |
| 2026-08-05 | 3 | **0** |

The pilot ran many meetings at once and blew LiveKit's concurrent-egress quota. `_auto_record`
([`bot.py:831-839`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/bot.py#L831-L839)) logs the failure and **gives up permanently** —
one attempt, no backoff, no retry when a slot frees. Nobody is told; the meeting proceeds and the
video is simply gone.

**Per-speaker WAV capture was unaffected** (19/19 audio tracks on 5 Aug). The audio path is fine.
Only composite *video* egress fails — which is exactly "no video available."

### Cause B — the egress webhook has never fired, in the entire history of the service

`_handle_egress_event` ([`runner.py:443-484`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/runner.py#L443-L484)) is supposed to
flip a recording from `pending` → `available` when LiveKit reports egress finished.

**Count of `egress_ended` / `egress_updated` events ever received in prod: `0`.**

Recordings only become available via the periodic reconciler. The 5 Aug recordings finished around
02:00 UTC and did not flip to `available` until **12:49 UTC — nearly eleven hours later**:

```
2026-08-05 12:49:31 | reconcile: media_file=30c3af66… → available (egress=EG_oYwbjDVAtoSx, livekit_status=3)
2026-08-05 12:49:31 | reconcile: media_file=483ed585… → available (egress=EG_T6jt2fwf4V8j, livekit_status=3)
2026-08-05 12:49:34 | reconcile: media_file=fda8eb22… → available (egress=EG_k8PtDXy3iGyg, livekit_status=3)
```

So for the whole pilot and the rest of that night, **even the recordings that worked showed as
"still recording"** to anyone who looked. That is the second half of the complaint.

### Cause C — two recordings stuck "recording" forever

Two 30 Jul egress jobs are permanently wedged in `pending`. LiveKit 404s on them, and the
reconciler retries forever without ever giving up or marking them failed — still firing today:

```
2026-08-04 22:04:08 | reconcile: error checking egress EG_9gPnE9XGravA: TwirpError(code=not_found, message=object cannot be found, status=404)
2026-08-05 13:12:16 | reconcile: error checking egress EG_9gPnE9XGravA: TwirpError(code=not_found, message=object cannot be found, status=404)
2026-08-05 13:12:38 | reconcile: error checking egress EG_yAnc7yc7c6Xg: TwirpError(code=not_found, message=object cannot be found, status=404)
```

These are the links that "still show recording" a week later.

---

## Finding 5 — Interruption handling was never actually built

We ship an `InterruptionTracker` that carefully measures how badly the bot talks over people. It
does not stop the bot doing it.

**52 talk-over events** across the pilot:

| Percentile | Bot kept talking after user started |
|------------|-------------------------------------|
| p50 | 1375 ms |
| p90 | ~4333 ms |
| observed max (logs) | **4743 ms** |

*(p99 from the histogram extrapolates past the top bucket at 5000 ms; the 4743 ms figure is a real
observed value from the logs and is the honest ceiling.)*

```
interruption:bot_stopped:49 - Talk-over: bot kept speaking 4743ms after the user started
interruption:bot_stopped:49 - Talk-over: bot kept speaking 3981ms after the user started
interruption:bot_stopped:49 - Talk-over: bot kept speaking 2866ms after the user started
bot:bot:912 - Bot session a484fef2… ended (completed) — interruptions=6 talkover_ms(max=4743 avg=1602)
bot:bot:912 - Bot session 0b5b2647… ended (completed) — interruptions=6 talkover_ms(max=3981 avg=1469)
```

### Why it never yields

- **`allow_interruptions` is set in exactly zero files** in the repo.
- `LiveKitParams` ([`bot.py:332-336`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/bot.py#L332-L336)) is constructed with **no
  `vad_analyzer`**, so the transport never emits the speech-start signal Pipecat's built-in
  interruption path depends on.
- `InterruptionTracker` is wired to an *observer* — it counts and emits metrics
  ([`interruption.py:36-52`](https://github.com/wwbp/MeetLab/blob/main/agent-runner/interruption.py#L36-L52)) and pushes nothing back into
  the pipeline.

We built the instrument and skipped the mechanism. The dashboard has been faithfully reporting a
problem no code was ever going to fix.

---

## Root causes, consolidated

| # | Root cause | Evidence | Blast radius |
|---|-----------|----------|--------------|
| RC1 | `user_turn_stop_timeout` left at Pipecat's 5.0s default; aggregator built with `vad_analyzer=None` | `bot.py:418-421`; 64 spikes all 5.2–6.1s at `queue_depth=0` | Complaints 1 + 3 |
| RC2 | `stt_endpointing_ms=100` — turn ends after 100 ms of silence | `bot.py:158-160`; 31-fragment turns; "Sorry?" in transcripts | Complaint 2 |
| RC3 | Interruption measured, never implemented — no `allow_interruptions`, no transport VAD | 52 talk-overs, max 4743 ms | Complaint 5 |
| RC4 | Egress 429 with no retry + egress webhook never fires + reconciler never gives up | 15 rooms 429'd; 0 webhooks ever; 2 permanently stuck | Complaint 4 |

---

## Recommended fixes, in order of leverage

1. **Set `user_turn_stop_timeout` explicitly** (RC1). Suggest 0.8–1.2s, and give the aggregator a
   real `vad_analyzer` so it stops relying on a wall-clock timer at all. This alone should collapse
   p90 from ~7s to under ~1.5s and remove most chaining. *One line, highest leverage in the whole list.*
2. **Raise `stt_endpointing_ms` to 400–600 ms** (RC2) and re-measure fragmentation. Trades a little
   snappiness on clipped answers for not shredding real sentences.
3. ~~**Actually implement interruption** (RC3): put a VAD on `LiveKitParams`, enable
   `allow_interruptions`~~ — **done 2026-08-10, but not that way. The recommendation
   above was wrong** and is left visible because the reason is worth keeping:

   - `allow_interruptions` and `interruption_strategies` are **0.0.x-era fields that
     do not exist on `PipelineParams` in Pipecat 1.4.0**. Much of the published
     documentation still describes them. A test now pins their absence so a
     dependency bump forces us to revisit.
   - A transport-level `vad_analyzer` would have been the worse option even if it
     existed: `MultiSpeakerSTT` already runs per-participant VAD, so we have onset
     attributed to a *specific speaker*. A transport analyzer re-runs VAD over the
     mixed stream for an unattributed signal.

   What shipped: `user_onset()` already knew when someone started talking over the
   bot — it just returned nothing. It now returns that as a signal, and
   `make_speech_onset_handler` pushes an `InterruptionFrame`, which every
   `FrameProcessor` handles by cancelling in-flight work
   (`frame_processor.py`: `InterruptionFrame` → `_start_interruption`). Fires once
   per bot-speaking window, so VAD flicker or a second speaker joining in does not
   re-interrupt.

   Note the pre-existing asymmetry this closes: a **typed** message already
   interrupted the bot (`on_data_received` queued an `InterruptionFrame`). Only
   *speech* didn't.
4. **Make recording robust** (RC4):
   - retry egress start with backoff on 429, and surface a visible "recording unavailable" state
     instead of failing silently;
   - find out why LiveKit egress webhooks never arrive (URL/signing/security-group) — the
     reconciler is a backstop that has silently become the only mechanism;
   - cap reconciler retries and mark 404 egress jobs `failed` so nothing shows "recording" forever;
   - check the LiveKit plan's concurrent-egress quota against expected pilot concurrency **before**
     the next run.
5. **Re-run the pilot instrumented** once 1 + 2 are in, and compare against the tables above.

---

## Reproduction tests

`agent-runner/tests/test_pilot_root_causes.py` — 16 tests, one class per root
cause, all passing:

```
docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
    uv run python -m unittest tests.test_pilot_root_causes -v
```

They **pin the defective behaviour** rather than assert the desired behaviour, so
the suite stays green and each fix has an exact assertion to invert. Every
pinning test carries a `FIX:` line naming what to change. Highlights:

| Test | Reproduces |
|------|-----------|
| `test_production_aggregator_waits_5s_...` | builds the exact params bot.py uses; asserts `user_turn_stop_timeout == 5.0` and `vad_analyzer is None` |
| `test_bot_does_not_override_the_turn_stop_timeout` | AST-checks the real call site — no override passed |
| `test_vad_stop_secs_has_no_effect_on_the_pipeline` | builds the VAD with `vad_stop_secs` 0.1 vs 5.0 and asserts the window is **identical** — the dead knob, proven behaviourally |
| `test_tracker_records_a_talkover_but_has_no_way_to_stop_the_bot` | measures a 4200ms talk-over, then asserts the tracker exposes no mechanism that could have prevented it |
| `test_allow_interruptions_is_never_configured_anywhere` | scans all agent-runner sources — zero hits |
| `test_quota_429_is_attempted_exactly_once_and_never_retried` | one attempt, then give up |
| `test_quota_failure_records_nothing_and_leaves_nothing_to_reconcile` | end-to-end against the DB: 429 → 502, **no MediaFile row**, so the loss is invisible and unrecoverable |

The last one reproduces the production log line exactly:

```
ERROR | runner:start_recording_for_room:883 - recording start LiveKit error:
  room=rc4-quota-7639d706 error=TwirpError(code=resource_exhausted,
  message=concurrent egress sessions limit exceeded, status=429)
```

Full suite: **274 tests, all passing** (258 before these were added).

## Performance testing — the harness could never have caught this

Attempting to reproduce the lag on the benchmark harness surfaced three separate
problems. **We currently have no working local latency measurement.**

**1. The fixtures are structurally blind to RC1.** Every fixture in
`generate_benchmark_audio.py` is a single continuous phrase — *"What is the
capital of France?"*. That always produces a **one-fragment** turn, which is the
fast path (p50 390ms in production). The 5s stall only happens on multi-fragment
turns. Months of benchmarks measured the one case that isn't affected, which is
why the pilot was a surprise.

Fixed: `tests/generate_paused_speech_audio.py` + `make benchmark-audio-paused`
generate the same question delivered in five clauses with 400ms thinking pauses —
the shape real participants produce. `make benchmark-full` now also passes
`BENCHMARK_PARALLEL` through, so runs can be serialised on a small VM.

**2. agent-runner leaks roughly 150 MB per bot session.** Measured on the dev
container: **241 MiB fresh → 4.9 GiB after ~30 sessions**, never released. Bots
run as FastAPI `BackgroundTask`s inside the runner process. On the 8 GB dev VM
this OOM-kills the benchmark (`Error 137`) partway through. In production it is a
slow leak on a long-lived instance — worth checking against the EB instance's
memory graph.

**3. The harness records no STT/LLM/E2E data locally.** With a fresh runner,
serial execution and `nova-3-general`, 10/10 samples completed and reported TTS
TTFB (p50 194ms) but `— no data` for STT, LLM TTFB and total — and no
`conversations` rows were persisted at all. Separately, the DB default
(`parakeet-tdt-0.6b-v2`) has no local NIM and silently falls back to in-process
Whisper, which loads a model per participant and OOMs the VM — a failure mode
already documented as Experiment 5 in `run_benchmark_matrix.py`.

**Conclusion: the authoritative latency measurement for this pilot is the
production telemetry above** — 268 real user turns with a clean dose-response
correlation between turn fragmentation and latency. That is stronger evidence
than any synthetic run, and it is what the fix should be judged against. Getting
the local harness working again is its own piece of work.

Not attempted: benchmarking against production. `run_benchmark_matrix.py` writes
a room-scoped row to `bot_config` per config, and the alternative (`make
benchmark`) would inherit the **global** row where `auto_record=true` — spawning
ten composite egress jobs into the same quota that broke the pilot. If prod NIM
numbers are needed, do it as a deliberate one-off with an explicit
`auto_record=false` scoped config that is deleted afterwards.

## Tech debt — things to trim

Filed here rather than acted on: **we will test these and cut them later.** The pilot exposed that
the system is carrying a lot of weight that does not do what we think it does — in two cases the
weight was actively misleading us.

**Config surface that does nothing (delete or wire up):**
- `bot_config.vad_stop_secs` — DB column + API validation + admin-UI slider, **zero pipeline
  effect**. It was set to 0.1 everywhere by someone trying to fix the exact problem it can't fix.
  Either wire it to the aggregator or delete it end-to-end. Do not leave it.
- 42 rows in `bot_config`, mostly per-link scopes auto-created and never revisited, each carrying a
  full copy of every knob. The per-link scope mechanism needs a lifecycle (or a TTL).

**Instrumentation without mechanism:**
- `InterruptionTracker` — measures talk-over, cannot prevent it. Either close the loop or stop
  reporting a metric no code can move.
- Phase-1 STT spike diagnostics did their job (they ruled out queue/echo/phantom cleanly and
  pointed at a fixed constant). Worth keeping — but now that RC1 is known, the spike threshold and
  the `self_echo`/`phantom_segments` counters should be re-evaluated; both were **0** all pilot.

**Observability that isn't on main:**
- The durable event log (`meet/lib/concierge/event-log.ts`, console Errors & Events view) is still
  only on `feat/pipeline-event-log`. During the pilot the console had the old in-memory ring capped
  at 250 entries. Every finding in this document had to come from CloudWatch and direct DB queries
  instead. **Merging this is a prerequisite for the next pilot** — otherwise we do this archaeology again.

**Performance-test capability (new, from the reproduction attempt):**
- agent-runner leaks ~150 MB per bot session and never releases it.
- The local benchmark records no STT/LLM/E2E stages — only TTS.
- The DB default STT has no local NIM and silently falls back to OOM-prone Whisper.
- ~~`test_recordings_transcript.test_start_with_session_but_no_livekit_room_returns_4xx_not_500`
  is flaky~~ — **fixed 2026-08-05.** It enumerated `[404, 409, 502]`, but a real
  `egress` service in the dev stack makes a successful start (200) legitimate.
  Assertion widened; the regression it guards ("must not be 500") is unchanged.
  Verified over three consecutive full-suite runs.

**Stale docs:**
- `CLAUDE.md:155` says `web-client/` is retained. It is gone. Remove the line.
- `agent-runner/soak-results-*.json` — 3 committed result artifacts; move to a bucket or drop.

**Known constraints that bit us or nearly did:**
- All concierge state is in-memory; a `meet` restart strands in-flight sessions.
- `livekit-server:latest` is unpinned.
- Token TTL is 15 min with no refresh; sessions ran up to 329s avg so we were nowhere near it —
  but a 20-minute meeting would silently drop.

---

## Appendix A — how to reproduce

**Prometheus (Grafana Cloud).** Needs a `metrics:read` token at
`~/.config/meetlab/grafana-read-token`.

```bash
scripts/grafana-prom.sh --labels
scripts/grafana-prom.sh 'histogram_quantile(0.9, sum by (le) (increase(meetlab_stt_latency_ms_milliseconds_bucket[40m])))'
scripts/grafana-prom.sh --range '48h' 'sum(increase(meetlab_utterances_total[5m]))'
```

**CloudWatch Logs Insights.** Log group:
`/aws/elasticbeanstalk/agent-runner/var/log/eb-docker/containers/eb-current-app/stdouterr.log`
(90-day retention — survives instance replacement).

```
fields @timestamp, @message | filter @message like /STT latency spike/ | sort @timestamp asc
fields @timestamp, @message | filter @message like /concurrent egress sessions limit exceeded/
  | parse @message /room=(?<room>[a-z0-9-]+)/ | stats count(*), count_distinct(room) by bin(1d)
fields @timestamp, @message | filter @message like /Talk-over|interruptions=/
fields @timestamp, @message | filter @message like /egress_ended|egress_updated/   # returns 0
```

**Prod database.** `meetlab` RDS Postgres is **not** publicly accessible. Reach it by running SQL
inside the agent-runner container (which holds `DATABASE_URL`) over SSM:

```bash
aws ssm send-command --instance-ids i-047f3780ee0d7e17e \
  --document-name AWS-RunShellScript --parameters commands='…'
```

Key queries used (full text in the investigation transcript):

```sql
-- session inventory + video coverage
SELECT c.started_at::date, count(*),
       count(*) FILTER (WHERE ut.n > 0) AS real_sessions,
       count(*) FILTER (WHERE ut.n > 0 AND mf.n = 0) AS missing_video
FROM conversations c
LEFT JOIN LATERAL (SELECT count(*) n FROM utterances u
    WHERE u.conv_id=c.id AND u.speaker_id NOT LIKE 'bot\_%' AND u.text<>'') ut ON true
LEFT JOIN LATERAL (SELECT count(*) n FROM media_files m
    WHERE m.conv_id=c.id AND m.type='recording') mf ON true
GROUP BY 1 ORDER BY 1;

-- latency percentiles per day  (timing lives on the BOT utterance)
SELECT date_trunc('day', c.started_at)::date,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY (u.meta->>'total_latency_ms')::float)
FROM utterances u JOIN conversations c ON c.id=u.conv_id
WHERE u.meta ? 'total_latency_ms' GROUP BY 1;

-- fragmentation vs latency  (join bot reply back to its user turn via reply_to)
SELECT (length(uu.text)-length(replace(uu.text,': ','')))/2 AS fragments,
       (b.meta->'timing'->>'stt_ms')::float AS stt_ms
FROM utterances b JOIN utterances uu ON uu.id=b.reply_to;
```

**Gotchas for whoever picks this up:**
- Per-turn timing is stored on the **bot's** utterance, not the user's. Join via `reply_to`.
- Postgres has no `round(double precision, int)` — cast to `numeric` first.
- Counting speaker labels with a loose regex over-counts badly (see the correction in Finding 3).
  Compare the lead label against the total `': '` count instead.
