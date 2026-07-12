# GPU STT deployment (Parakeet) — runbook

How we serve the self-hosted Parakeet STT on a GPU, why we're moving off the current
sidecar, and the standard NVIDIA deployment to move to.

## TL;DR

- **Today:** a community FastAPI wrapper ([Shadowfita/parakeet-tdt-0.6b-v2-fastapi](https://github.com/Shadowfita/parakeet-tdt-0.6b-v2-fastapi))
  runs Parakeet behind `POST /transcribe`. It is **single-threaded / sequential** — fine for
  1–2 rooms, but it collapses under concurrency.
- **Proven bottleneck (2026-07-01 prod soak):** 10 rooms / 20 concurrent streams on the T4 →
  the GPU never crashed, but the server serialized requests to **stt_ms P50 ≈ 5.0s / P95 ≈ 7.3s**
  and served only ~17 of 473 offered turns. See `latency-experiments.md` §6.
- **Move to the standard NVIDIA deployment:** **NVIDIA Speech NIM for Parakeet-TDT** — the model
  packaged with CUDA + TensorRT + **Triton Inference Server**, which does **dynamic batching and
  concurrency natively** and scales to hundreds of parallel streams. This is the fix for 10-room
  scale; a bigger GPU alone is not.

## Status (as of 2026-07-07): NIM live in prod

- The Parakeet-TDT **NIM is deployed and serving prod** on the g6 (L4) via `infra/stt-nim/`.
  agent-runner points at it (`NEMOTRON_STT_URL=http://10.0.5.21:9000`); a real prod bot
  transcribed correctly through it. Prod concurrency benchmark (Experiment 7): **~22× throughput
  and ~22× lower p50 at conc 8 vs the serialized Shadowfita baseline, errors=0**. The NIM is
  *fast-serial* (~30 req/s ceiling), which is ~10% utilized at the 10-room target — big headroom.
- **Shadowfita fully removed** (tech-debt cleanup, two phases, both done):
  - **Phase 1:** local dev no longer runs the Shadowfita CPU sidecar. The local stack transcribes
    with **in-process whisper-base** via `STT_MODEL_OVERRIDE=whisper-base` (set in
    `.devcontainer/docker-compose.yml`); prod leaves the override unset and uses the DB config (NIM).
  - **Phase 2:** the `shadowfita` client API branch + its tests are deleted (the client is now
    NIM-only), and the old **T4 (`meetlab-stt-gpu`, `i-0f045aa6c337b55ab`) is terminated**.
    **Rollback is no longer a live env-flip** — it's a redeploy of a pre-cutover build.

> **⚠ Licensing — action needed.** The prod cutover on **2026-07-07** uses an **NVIDIA Developer
> Program** NGC key, which covers **development / testing / research** (≤16 GPUs) for free. If this
> prod traffic counts as **production** (not academic research), it's governed by **NVIDIA AI
> Enterprise**: a free **90-day evaluation**, then **~$4,500/GPU/yr**. **Decision owner: team;
> deadline to start the eval or confirm the research exemption ≈ 2026-10-05** (90 days from
> cutover). Nothing tracks this clock but this note — reconcile before then. See
> [reference: NIM licensing](../infra/stt-nim/README.md).

## Why not just keep Shadowfita

It loads and runs inference sequentially — one request at a time, no batching, no request
queue management. Concurrency numbers (Experiment 6): 4-way p50 290ms, 8-way 475ms, 16-way
842ms; 20-way under sustained real load ≈ 5s. The ceiling is the **serving layer**, not the T4.

**Baseline benchmark** (`make bench-stt-concurrency`, local CPU sidecar, 2026-07-01) — the
serialization is exact and unambiguous:

| concurrency | p50 | p95 | throughput |
|---|---|---|---|
| 1 | 708ms | 727ms | 1.41 req/s |
| 2 | 1413ms | 1419ms | 1.41 req/s |
| 4 | 2826ms | 2865ms | 1.41 req/s |
| 8 | 5642ms | 5666ms | 1.41 req/s |

Latency scales **linearly** with concurrency (8× at 8-way) while **throughput stays pinned at
1.41 req/s** — the server processes one request at a time; concurrency just lengthens the queue.
A batched Triton server should hold p50 roughly flat and let throughput climb. Re-run the same
tool against the NIM/Riva server (`STT_API=openai STT_URL=<nim>`) to quantify the fix.

## Target: NVIDIA Speech NIM (recommended)

NIM is NVIDIA's production-grade, GPU-accelerated container: model + CUDA + TensorRT + Triton +
a unified HTTP/gRPC API in one image. Triton handles batching/concurrency, so many rooms share
the GPU efficiently.

Sources: [Parakeet-TDT NIM deploy](https://docs.nvidia.com/nim/speech/26.05.0/asr/deploy-asr-models/parakeet-tdt.html),
[Speech NIM overview](https://docs.nvidia.com/nim/riva/asr/latest/getting-started.html),
[ASR support matrix](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html).

### GPU requirement (the T4 does NOT qualify)
The NIM requires **Compute Capability ≥ 8.0 and ≥16 GB VRAM** ([support matrix](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html)).
The current `meetlab-stt-gpu` is a **T4 (CC 7.5) → unsupported**. Chosen target: **`g6.xlarge`
(L4, CC 8.9, 24 GB)**, always-on, replacing the T4.

### Prerequisites
- **NGC API key** ("NGC Catalog" scope) → GitHub secret `NGC_API_KEY` (prod use is governed by
  NVIDIA AI Enterprise licensing — confirm before relying on it).
- Terraform state backend (S3 + DynamoDB), repo vars for VPC/subnet/SG/zone, and expanded OIDC role
  perms — see [infra/stt-nim/README.md](../infra/stt-nim/README.md).

### Deploy (as-code — not hand-CLI)
Provisioning and the container both live in the repo and roll out via GitHub Actions:
- **`infra/stt-nim/`** — Terraform: the g6, its SG (ports open only to the agent-runner EB SG), an
  SSM-managed instance profile, `user-data.sh` installing a **`nim-stt` systemd unit**, and a
  Route53 **private** record `stt-nim.vivaprox.internal` for a stable endpoint.
- **`.github/workflows/deploy-stt-nim.yml`** — assumes the AWS OIDC role → seeds the NGC key to SSM
  → `terraform apply` → SSM Run Command rolls the container + polls health **on the box**. Applies
  only on explicit `workflow_dispatch(action=apply)`; push to `main` runs `plan`.

The systemd unit runs the NVIDIA-documented config ([Parakeet-TDT deploy](https://docs.nvidia.com/nim/speech/26.05.0/asr/deploy-asr-models/parakeet-tdt.html)):
`nvcr.io/nim/nvidia/parakeet-0.6b-tdt:latest`, `--gpus all --shm-size=8GB`, ports 9000/50051,
`NIM_TAGS_SELECTOR=type=default`, model cache at `/opt/nim/cache`.
- **API:** HTTP `POST /v1/audio/transcriptions` (OpenAI-compatible) on :9000; gRPC on :50051.
  Parakeet-TDT NIM is **offline** (no streaming) — Triton batching is what gives concurrency.
- **Health:** `GET :9000/v1/health/ready` (checked on the box via SSM; the private IP isn't
  reachable from the GitHub runner).

### Bot-side integration (already shipped — just flip an env)
`NemotronHTTPSTTService` now speaks both APIs (`nemotron_stt.py`): Shadowfita's `/transcribe`
and the OpenAI-compatible `/v1/audio/transcriptions`, selected by **`NEMOTRON_STT_API`**
(`shadowfita` default | `openai`). Both return `{"text": ...}`; the segmented `(VADProcessor →
STT)` chain is unchanged (segments are already VAD-cut WAVs). To migrate:
1. `NEMOTRON_STT_API=openai` on the agent-runner env.
2. `NEMOTRON_STT_URL=http://<nim-host>:9000` (NIM HTTP port).

No per-room config or model-id change. Validate the fix:
- `make bench-stt-concurrency STT_URL=http://<nim>:9000` (run from inside the VPC) — see the
  measured curve in Experiment 7 of `latency-experiments.md` (~30 req/s, errors=0, ~22× vs baseline).
- `make soak SOAK_LOAD_ONLY=1 … STT_MODEL=parakeet-tdt-0.6b-v2` against the NIM; read latency from
  Grafana (the harness's DB checks only see the local DB, not prod's RDS).

## Alternative: Riva / Triton (fully OSS)

If NIM licensing is a blocker, deploy Parakeet on **Riva** (Triton-based, free on NGC) using the
OSS community tutorials: [nvidia-riva/tutorials](https://github.com/nvidia-riva/tutorials)
(`asr-deploy-parakeet-ctc.ipynb`, `asr-train-and-deploy-NGPU-LM-for-parakeet-rnnt.ipynb`). Same
Triton concurrency/batching; more manual setup (build `.rmir` → `riva-build`/`riva-deploy` →
`riva_start.sh`). gRPC streaming supported. This is the "standard NVIDIA OSS community" path.

## Ops — the NIM box

Prod serves STT from the NIM. The old T4 Shadowfita box (`meetlab-stt-gpu`, `i-0f045aa6c337b55ab`)
was **terminated** in Phase 2 — there is no live-flip rollback anymore.

| Task | How |
|---|---|
| Instance | EC2 `meetlab-stt-gpu-nim` (`i-0a5a5cb3bef4e4608`), g6.xlarge (L4), vivaprox VPC, private `10.0.5.21` |
| Manage | Terraform (`infra/stt-nim/`) + the Deploy STT NIM workflow — do not hand-CLI the box |
| Health | `GET :9000/v1/health/ready` (on-box; the private IP isn't reachable off-VPC) |
| Rollback | redeploy a pre-cutover agent-runner build (no Shadowfita env-flip — that path is deleted) |

## Migration (completed — Shadowfita/T4 → NIM/g6)

The migration is done; kept as a record of the sequence used (prod never pointed at a NIM that
wasn't up yet):

1. **Prereqs** (one-time): `NGC_API_KEY` secret, TF state backend, repo vars, OIDC role perms,
   licensing — see [infra/stt-nim/README.md](../infra/stt-nim/README.md). ✓
2. **Stood up the NIM:** Actions → **Deploy STT NIM** → `plan` → `apply`; SSM health reported ready. ✓
3. **Benchmarked** (from inside the VPC): `make bench-stt-concurrency STT_URL=http://<nim>:9000` —
   ~22× vs the serialized baseline, errors=0 (Experiment 7). ✓
4. **Cut over** via `agent-runner/.ebextensions/stt.config` (`NEMOTRON_STT_URL` → the NIM), shipped by
   the agent-runner CD. ✓
5. **Verified in prod:** bots transcribe through the NIM end-to-end (sanity soak). ✓
6. **Decommissioned the T4** (`meetlab-stt-gpu`, was not in Terraform): terminated in Phase 2. ✓
7. **Rollback** (if ever needed): redeploy a pre-cutover agent-runner build. The Shadowfita
   client path is deleted, so there is no `NEMOTRON_STT_API=shadowfita` env-flip anymore.
