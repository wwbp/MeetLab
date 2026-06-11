# Production Performance Setup — Configure & Test Plan

Goal: after the latency-instrumentation PR (#39) merges and deploys, verify the
production stack reports the same metrics we validated locally, and make those
metrics queryable from the CLI for everyone.

## What production looks like today (verified 2026-06-10 via AWS CLI)

- **AWS account** 848180123498, region us-east-1. App `vivaprox`, Elastic Beanstalk
  environments: `agent-runner` and `meeting-client` (both Green/Ready). Single-container
  Docker on EB; CD deploys on push to main (`.github/workflows/cd.yml`).
- **Tracing/metrics export** is already configured on the prod agent-runner:
  `ENABLE_TRACING=true`, OTLP → `https://otlp-gateway-prod-us-east-3.grafana.net/otlp`
  (http/protobuf), `OTEL_SERVICE_NAME=meetlan-otel` (note the "meetlan" typo — it is in
  the metric labels, don't "fix" it without updating dashboards).
- **Grafana Cloud stack** `meetlab-prod` (prod-us-east-3). Prometheus read endpoint:
  `https://prometheus-prod-66-prod-us-east-3.grafana.net/api/prom`, instance ID `3238458`.
- **Logs**: EB CloudWatch groups, most useful one:
  `/aws/elasticbeanstalk/agent-runner/var/log/eb-docker/containers/eb-current-app/stdouterr.log`
- **Leftovers to clean up eventually**: `GRAFANA_PROM_API_KEY`, `GRAFANA_PROM_REMOTE_WRITE_URL`,
  `GRAFANA_PROM_USER_ID` env vars on the EB environment are from the removed
  Prometheus/Alloy setup (PR #35) and are unused by the current code.

## Phase 0 — CLI access (one-time, ~10 min)

- [x] AWS CLI authenticated (verified: account 848180123498, EB + CloudWatch readable)
- [x] Query wrapper added: `scripts/grafana-prom.sh`
- [ ] **(You, in Grafana Cloud portal)** Create a read token at
  https://grafana.com/orgs/sabhay/access-policies → Create access policy →
  realm: `sabhay` (prod-us-east-3), scope **metrics:read** → Add token. The Grafana
  web UI for dashboards is https://sabhay.grafana.net. Then:
  ```bash
  mkdir -p ~/.config/meetlab
  pbpaste > ~/.config/meetlab/grafana-read-token   # or paste manually
  chmod 600 ~/.config/meetlab/grafana-read-token
  ```
  The token already in `agent-runner/.env.runner` is OTLP **write-only** — tested, it
  returns "invalid scope requested" on queries.
- [ ] Smoke test: `scripts/grafana-prom.sh --labels` → should list `meetlab_*` metric names.

## Verification run 2026-06-10 (pre-merge, deployed = main @ 79c7afa)

Ran a 5-session benchmark against prod (`AGENT_RUNNER_URL` + prod LiveKit Cloud creds
pulled from EB). **Production works end to end:**

- [x] Deployed version confirmed: `agent-runner-79c7afa...` = current main (PR #39 not yet in)
- [x] `/health` 200, logs clean (only internet-scanner 404 noise — the runner is public)
- [x] Auth enforced and working: `/start`, `/config`, `/conversations` all require
  `Authorization: Bearer $BOT_RUNNER_SECRET` (benchmark script now sends it)
- [x] 5/5 sessions: bot spawned → joined LiveKit Cloud → greeted → Deepgram transcribed
  the question → answered "The capital of France is Paris." → ElevenLabs TTS delivered
- [x] Prod stage latencies from logs (response turn): LLM TTFB 271/284/414/480/1344 ms,
  TTS TTFB ~133–160 ms — consistent with local numbers

Two harness findings:

1. **Fixed:** `run_benchmark_matrix.py` never sent the Bearer header (works locally only
   because compose blanks `BOT_RUNNER_SECRET`). Now sends it when the env var is set.
2. **Open:** `_poll_bot_utterance` reads the **local Postgres directly**, so a prod-pointed
   run reports 0 samples collected even when prod succeeds (results live in prod RDS).
   Remote benchmarking needs an HTTP result path — e.g. expose utterance timing meta on an
   authenticated GET — or accept that prod latency comes from Grafana histograms only.

One observation to watch: prod transcript clipped the first word ("Is the capital of
France." instead of "What is..."), suggesting the benchmark's 1.5s connect-settle is tight
for prod TURN negotiation. Locally the full sentence transcribes.

## Phase 1 — Verify the deploy carried the new instrumentation (~5 min, after merge)

- [ ] Watch CD finish: `gh run watch` (or GitHub Actions UI), confirm EB env recovers to Green:
  ```bash
  aws elasticbeanstalk describe-environment-health --environment-name agent-runner \
    --attribute-names All --query '{Status:Status,Health:HealthStatus}'
  ```
- [ ] Tail prod logs for a clean boot (no tracebacks):
  ```bash
  aws logs tail /aws/elasticbeanstalk/agent-runner/var/log/eb-docker/containers/eb-current-app/stdouterr.log --since 15m --follow
  ```
- [ ] Hit the prod health endpoint: `curl -s https://<agent-runner-CNAME>/health`

## Phase 2 — Generate traffic and verify metrics arrive (~15 min)

- [ ] Run one real voice session against prod (meet UI → voice agent), or run the
  benchmark against prod (Experiment 2, Step 2 recipe):
  ```bash
  AGENT_RUNNER_URL=https://<agent-runner-CNAME> \
  LIVEKIT_URL=wss://<prod-livekit> \
  make benchmark-exp2 BENCHMARK_SAMPLES=10
  ```
- [ ] Confirm the NEW histogram exists in Grafana Cloud (this is the key check —
  `meetlab.stt_latency_ms` only ships in PR #39). Note: the OTLP→Prometheus
  translation appends the unit, so the stored name is
  `meetlab_stt_latency_ms_milliseconds_*` (same for all our histograms):
  ```bash
  scripts/grafana-prom.sh 'count(meetlab_stt_latency_ms_milliseconds_bucket)'
  scripts/grafana-prom.sh 'histogram_quantile(0.5, sum by (le) (rate(meetlab_stt_latency_ms_milliseconds_bucket[15m])))'
  ```
- [ ] Sanity-compare prod P50s against the local 2026-06-10 numbers
  (STT ~395ms, LLM ~400ms, TTS ~160ms, total ~1,010ms). Prod should be similar or
  better (AWS↔API colocation); record the comparison in `docs/latency-experiments.md`.

## Phase 3 — Dashboard + alert (~30 min, in Grafana UI)

- [ ] One dashboard, four panels (queries below), variable `stt_model`:
  ```promql
  # Total user-perceived latency P50/P95 (the headline)
  histogram_quantile(0.50, sum by (le) (rate(meetlab_stt_latency_ms_milliseconds_bucket[5m])))
    + histogram_quantile(0.50, sum by (le) (rate(meetlab_e2e_latency_ms_milliseconds_bucket[5m])))
  # Per-stage P50s
  histogram_quantile(0.50, sum by (le) (rate(meetlab_llm_ttft_ms_milliseconds_bucket[5m])))
  histogram_quantile(0.50, sum by (le) (rate(meetlab_tts_ttfb_ms_milliseconds_bucket[5m])))
  # Throughput
  rate(meetlab_utterances_total[5m]) * 60
  ```
- [ ] Alert: post-STT E2E P95 > 2000ms for 10m → notify the team channel.
- [ ] Link the dashboard URL in `docs/performance-tests.md` ("Watching latency live" table).

## Phase 4 — Hygiene (when convenient)

- [ ] Remove the stale `GRAFANA_PROM_*` env vars from the EB environment (after
  confirming nothing references them):
  ```bash
  aws elasticbeanstalk update-environment --environment-name agent-runner \
    --options-to-remove Namespace=aws:elasticbeanstalk:application:environment,OptionName=GRAFANA_PROM_API_KEY \
                        Namespace=aws:elasticbeanstalk:application:environment,OptionName=GRAFANA_PROM_REMOTE_WRITE_URL \
                        Namespace=aws:elasticbeanstalk:application:environment,OptionName=GRAFANA_PROM_USER_ID
  ```
  ⚠️ triggers an environment update/restart — do it in a quiet window.
- [ ] Move the write token out of the `.env.runner` comment block (it is a live
  credential in a file that is gitignored but easy to paste around).
- [ ] Rewrite the stale "Production Grafana setup" section in
  `docs/latency-experiments.md` (still describes the removed Prometheus scrape model).

## Phase 5 — Experiment 6: GPU-enabled local STT vs Deepgram (planned)

> **Superseded by the stage-gated plan in `docs/experiment-6-gpu-stt.md`** — that doc
> is the live collaboration surface for this experiment. The notes below are the
> original sketch, kept for context.

**Goal:** beat Deepgram's `stt_ms` floor of ~395ms P50 (its endpointing knob is a
measured no-op — Experiment 2 addendum) with GPU faster-whisper, where our local
Silero VAD wait *is* tunable.

**Latency model:** whisper `stt_ms` = VAD `stop_secs` + GPU segment inference.
T4-class GPU transcribes 2–8s utterances in ~150–300ms with large-v3-turbo, so
150ms VAD + 200ms inference ≈ 350ms < 395ms. Deepgram streams during speech;
whisper pays the whole segment after silence — that race is the experiment.

**Prerequisites (harness, ~half a day):**
- [ ] HTTP result path: `_poll_bot_utterance` reads the local Postgres, so remote
  hosts can't be benchmarked (verified against prod). Add an authenticated GET for
  utterance timing meta and use it when `AGENT_RUNNER_URL` is remote.
- [ ] Raise the 1.5s connect-settle in `_run_sample` (prod clipped the first word).
- [ ] Warm + cache the Whisper model per process (per-participant copies would
  exhaust a 16GB T4 at ~8 participants; CPU load is 4s and blocks the event loop).

**Run (~1 day, ~$5 GPU time):**
- [ ] EC2 g4dn.xlarge (T4, ~$0.53/hr) + nvidia-container-toolkit; agent-runner
  container with `--gpus all`, `LIVEKIT_URL` = the same LiveKit Cloud as prod
- [ ] Matrix from that box: {whisper-turbo, distil-large-v3, whisper-base} ×
  stop_secs {200,150,100} × 10 samples, **plus nova-3 baseline from the same box**
- [ ] One hosted-whisper config (e.g. Groq large-v3-turbo) as a zero-ops hedge

**Decision criteria:** stt_ms P50 < 395ms AND accurate transcripts (false-termination
rubric from Experiment 2) AND stable with 2–3 concurrent bots. Win → productionize
(GPU node or STT sidecar; T4 24/7 ≈ $380/mo vs Deepgram per-minute — check volume).
Lose → stay on Deepgram, attack LLM TTFT (the other ~400ms) instead.

## Known limitations to keep in mind

- Prod metric names: OTLP histograms arrive in Prometheus with `.` → `_`
  (`meetlab.stt_latency_ms` → `meetlab_stt_latency_ms_bucket/...`).
- Traces in prod also go to the Grafana OTLP gateway (Tempo) — same blind spot as
  local Jaeger: **no STT spans** (Experiment 3). STT visibility is only via
  `meetlab_stt_latency_ms`.
- Token TTL for LiveKit sessions is 15 min with no refresh — long prod test sessions
  will drop silently (known constraint, unrelated to metrics).
