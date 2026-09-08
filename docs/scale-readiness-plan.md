# Scale readiness — from tasting menu to dinner rush

**Target:** 50 concurrent sessions, **video recording on every session**, weeks of runway.
**Pilot peak:** 7–8 concurrent sessions — and it already broke.
**Gap:** ~7× on sessions, **~17× on recording**.

Companion to `docs/pilot-postmortem-2026-08.md` (why the pilot went wrong). This
document is about whether the system can carry 7× more of it. Every ceiling below
is measured, not estimated, unless marked *(estimate)*.

---

## The one-paragraph version

Production today is **one 2-vCPU box per service with autoscaling switched off**,
talking to a **test-tier LiveKit project** that allows about **three** concurrent
recordings. At the pilot's peak of 7 concurrent sessions the agent-runner was
already at **85% CPU** and recordings were being rejected with 429s. Nothing here
scales by turning a dial: three of the ceilings need code changes, and the
recording ceiling needs a vendor decision with real lead time. Start that one
this week.

---

## Measured ceilings

| # | Component | Today | Breaks at | Evidence |
|---|-----------|-------|-----------|----------|
| C1 | **LiveKit egress** | `test-meetlab-*.livekit.cloud`, test tier | **~3 concurrent recordings** | Max concurrent successful egress = 3; 15 rooms 429'd across 7/30 + 8/05 |
| C2 | **agent-runner CPU** | 1 × t3.medium (2 vCPU) | **~7–8 sessions** | CloudWatch 7/30 15:10Z: **84.5% avg, 88.9% max** at 7 concurrent |
| C3 | **Autoscaling** | Min=1, **Max=1** on both envs | any spike | EB config |
| C4 | **agent-runner memory** | 3.75 GiB container | ~20–24 sessions *cumulative* | ~150 MB leaked per session (241 MiB → 4.9 GiB over ~30 sessions) |
| C5 | **CPU credits** | t3 burstable, 576 cap, +24/hr | **~7 h sustained** at pilot CPU, then throttles to 20% baseline *(estimate)* | Credit balance held during a 20-min burst; sustained rush is a different regime |
| C6 | **meet horizontal scale** | Min=1, `globalThis` Maps | **2+ instances = incorrect**, not just slow | `bot-room-claim-store.ts` et al. are per-process Maps |
| C7 | **STT NIM** | 1 × g6.xlarge, **no ASG** | single point of failure | No autoscaling group; ~30 req/s serial |
| C8 | **Token TTL** | **5 minutes**, no refresh | any reconnect after 5 min | `connection-details/route.ts:168` (`at.ttl = '5m'`) |
| C9 | **RDS** | db.m5.large, **MultiAZ=false** | no failover | AWS |
| C10 | **Observability** | durable event log not on `main` | blind at scale | Still on `feat/pipeline-event-log` |

Two of these are worse than they look:

- **C6 is a correctness bug, not a capacity limit.** The bot-room claim store, the
  start mutex, and the request history are per-process `Map`s. The moment `meet`
  runs two instances they stop agreeing, and the "one bot per room" guarantee is
  gone — two bots in one room. Max=1 is currently the only thing preventing it.
- **C4 compounds.** Bots run as FastAPI `BackgroundTask`s *inside* the runner
  process, so leaked memory is never reclaimed between sessions. A box that
  survives a 30-minute tasting will not survive a four-hour rush.

### A note on C2 that cuts the wrong way

The 0.24 vCPU/session figure was measured **while RC1 was stalling every
fragmented turn for 5 seconds**. Those sessions spent much of their life idle,
waiting on a timer. Fixing RC1 makes the bot respond ~4× faster, which means
**more turns per minute per session** — so CPU per session will go *up*, not down.
Size for it, and re-measure after the fix rather than trusting the pilot number.

---

## Plan

Ordered by lead time, not by importance. C1 is first because it is the only item
we do not fully control.

### Phase 0 — Decide the recording architecture (start this week)

Video on all 50 sessions against a ~3-concurrent ceiling is the binding
constraint, and it is a procurement decision with external lead time.

1. **Move off the test LiveKit project regardless.** Production is currently
   pointed at `test-meetlab-*`. A production project is needed for its own sake
   (quotas, SLA, support), and it is a credentials/URL change across both
   services — do it early, not during the rush.
2. **Get the real numbers from LiveKit**: concurrent egress limit and cost per
   egress-hour at the tier that supports 50. This is the single most important
   unknown in this document.
3. **Price self-hosted egress as the alternative.** We already run an `egress`
   container in the dev compose, so the path is proven. But each composite egress
   is a headless Chrome plus ffmpeg — call it 1–2 vCPU each, so 50 concurrent is
   a **50–100 vCPU fleet** *(estimate — needs a measured run before anyone
   commits)*. Not obviously cheaper than the managed tier; needs a real
   comparison.
4. **Ship the 429 retry either way** (postmortem RC4). Even with headroom, a
   quota rejection must retry with backoff and leave a visible `failed` record
   instead of silently losing the video.

**Decision needed by end of week 1**, or the rush ships without reliable video.

### Phase 1 — Fix correctness before adding capacity

Scaling a system that mishandles every turn just distributes the disappointment.
All four are small, and `test_pilot_root_causes.py` already pins each one — the
tests tell you exactly which assertion to invert.

1. **RC1** — set `user_turn_stop_timeout` explicitly (0.8–1.2s) and give the
   aggregator a real `vad_analyzer`. Highest leverage change in the codebase.
2. **RC2** — raise `stt_endpointing_ms` to 400–600 ms; re-measure fragmentation.
3. **RC3** — implement interruption: VAD on `LiveKitParams`, enable
   `allow_interruptions`, make the tracker assert rather than observe.
4. **RC4** — egress retry + visible failure state + a give-up path in the
   reconciler.
5. **Delete or wire up `vad_stop_secs`** before anyone else tunes a knob that
   does nothing.

Then **re-measure CPU per session** — Phase 2 sizing depends on the post-fix
number, not the pilot's.

### Phase 2 — Capacity

1. **Get off burstable instances.** t3 → c6i/c7g class for agent-runner. At the
   pilot's 0.24 vCPU/session, 50 sessions ≈ **12 vCPU** before headroom; plan
   **~24 vCPU** across several instances for blast radius, and re-derive after
   Phase 1. *(estimate)*
2. **Turn autoscaling on** (C3): Max > 1 on agent-runner, scaling on CPU.
3. **Fix the memory leak** (C4). If the root cause is not quickly findable,
   mitigate: cap sessions per process and recycle. Do not scale without one or
   the other.

   **Done, in part — participants are now capped.** `MultiSpeakerSTT` refuses a
   new participant once a room holds `MAX_PARTICIPANT_WORKERS` recognition
   streams (default 6, sized from the 19 Aug ramp; override with the env var of
   the same name, which is the fastest lever during an incident and needs no
   deploy). The refusal is a WARNING, so it lands in the event log and the
   `meetlab.participants_refused_total` counter — the thing 19 Aug lacked, when
   the ramp accepted everyone, exhausted recognition throughput and quietly went
   from 261 replies to 23 with nothing saying no. The cap applies to *admission
   only*: someone already in the meeting keeps their stream at the ceiling, and a
   participant leaving frees the slot even if their teardown raises. Policy lives
   in `participant_workers.route_audio` (pure, unit-tested); this is a session
   *recycle* mitigation still outstanding, not a substitute for it.
4. **Move concierge state out of process** (C6) — Redis or Postgres for the three
   stores. This is a **hard prerequisite** for running more than one `meet`
   instance, and therefore for any HA at all.
5. **Fix `/recordings/start` across instances.** `audio_tracks.register_sink()`
   is a per-process registry; once agent-runner has more than one instance, a
   manual recording request routed to the wrong box silently finds no sink.
   (Auto-record is unaffected — it runs in the bot's own process.)
6. **Token TTL** (C8): raise well above expected session length and add a refresh
   path. At 5 minutes, any network blip past the fifth minute is an unrecoverable
   drop — with 50 sessions of mixed mobile networks, that will happen constantly.
7. **NIM HA** (C7): at least two instances behind health checks. 50 sessions is
   likely within throughput *(estimate: ~5 req/s vs ~30 serviceable)*, but today
   one instance failing takes down all speech recognition.
8. **RDS MultiAZ** (C9). Cheap insurance.

### Phase 3 — Prove it, then watch it

1. **Merge the durable event log** (C10) *before* the rush. Every finding in the
   postmortem had to be reconstructed from CloudWatch and hand-written SQL. At 50
   concurrent that approach does not work.
2. **Load test at 1.5× target — 75 concurrent.** `make soak`
   (`ROOMS × USERS_PER_ROOM`) is the harness. Run it against a staging LiveKit
   project, not production.
3. **Repair the benchmark harness first** — as of 2026-08-05 it reports no
   STT/LLM/E2E stages locally (see `docs/performance-tests.md`). A load test that
   cannot measure latency only proves the system doesn't crash.
4. **Alarms on the metrics we already emit.** We have Grafana Cloud and a good
   metric set and, as far as I can tell, nothing alerting on it. Minimum:
   agent-runner CPU, memory, `stt_latency_ms` p90, egress 429 rate, bot start
   failures.
5. **Model the cost per session-hour** at 50 concurrent — ElevenLabs TTS +
   OpenAI LLM + egress + GPU. Not calculated here; it needs real per-session
   token and character counts from the pilot data, and it should be known before
   the rush rather than discovered in a bill.

---

## If there is only one week

In order: LiveKit production plan + egress limits (C1, external lead time) →
RC1 + RC2 (two config values, most of the user-visible pain) → autoscaling on
with non-burstable instances (C2/C3/C5) → egress 429 retry (RC4).

That leaves the memory leak and the shared-state bug unfixed, which caps you at
one instance per service and a few hours of sustained load. It is a defensible
place to stop for a short rush; it is not a place to stay.

---

## Open questions

1. **What is the actual LiveKit concurrent-egress limit at the tier we would
   buy, and what does it cost?** Everything about recording depends on this.
2. **Is 50 concurrent a peak or a sustained level?** A 30-minute peak and a
   four-hour plateau are different problems — the plateau is what exhausts CPU
   credits and surfaces the memory leak.
3. **What is the expected session length?** The pilot averaged ~5 minutes with
   `session_limit_minutes=5` set. If the rush means 30-minute sessions, C8 (token
   TTL) moves from an annoyance to the top of the list, and concurrency math
   changes.
4. **Who is on call, and what do they get paged for?** No alarms exist today.
