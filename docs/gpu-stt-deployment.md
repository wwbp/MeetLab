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
- `make bench-stt-concurrency STT_API=openai STT_URL=http://<nim>:9000` — expect p50 to stay
  ~flat as concurrency climbs and throughput to scale (vs the serialized baseline above).
- `make soak SOAK_LOAD_ONLY=1 … STT_MODEL=parakeet-tdt-0.6b-v2` against the NIM; re-read Grafana —
  expect P50 to hold near the low-concurrency ~334ms across 10 rooms.

## Alternative: Riva / Triton (fully OSS)

If NIM licensing is a blocker, deploy Parakeet on **Riva** (Triton-based, free on NGC) using the
OSS community tutorials: [nvidia-riva/tutorials](https://github.com/nvidia-riva/tutorials)
(`asr-deploy-parakeet-ctc.ipynb`, `asr-train-and-deploy-NGPU-LM-for-parakeet-rnnt.ipynb`). Same
Triton concurrency/batching; more manual setup (build `.rmir` → `riva-build`/`riva-deploy` →
`riva_start.sh`). gRPC streaming supported. This is the "standard NVIDIA OSS community" path.

## Ops (current sidecar, until migration)

| Task | How |
|---|---|
| Instance | EC2 `meetlab-stt-gpu` (`i-0f045aa6c337b55ab`), T4, vivaprox VPC, private `10.0.5.115` |
| Start / stop | `aws ec2 start-instances --instance-ids i-0f045aa6c337b55ab` / `stop-instances …`. Run only when needed (~$0.53/hr, ~$380/mo if 24/7). |
| Wiring | agent-runner finds it via `NEMOTRON_STT_URL` on the EB env (`agent-runner` / app `vivaprox`) |
| Health (Shadowfita) | `GET :8000/healthz` |
| First-symptom check | parakeet room's bot joins but never replies → check this server first |

## Migration plan (Shadowfita/T4 → NIM/g6)

Bot-side client is already shipped; the sequence is deliberate so prod never points at a NIM that
isn't up yet:

1. **Prereqs** (one-time): `NGC_API_KEY` secret, TF state backend, repo vars, OIDC role perms,
   licensing — see [infra/stt-nim/README.md](../infra/stt-nim/README.md).
2. **Stand up the NIM:** Actions → **Deploy STT NIM** → `plan`, review, then `apply`. Confirm the
   SSM health step reports ready.
3. **Benchmark before cutover** (from agent-runner):
   `make bench-stt-concurrency STT_API=openai STT_URL=http://stt-nim.vivaprox.internal:9000` — expect
   p50 ~flat vs the serialized baseline. This is the headline before/after.
4. **Cut over (separate PR — merge only after step 3 passes):** add
   `agent-runner/.ebextensions/stt.config`, which the existing agent-runner CD deploys:
   ```yaml
   option_settings:
     aws:elasticbeanstalk:application:environment:
       NEMOTRON_STT_API: openai
       NEMOTRON_STT_URL: http://stt-nim.vivaprox.internal:9000
   ```
5. **Verify in prod:** 10-room `make soak SOAK_LOAD_ONLY=1 STT_MODEL=parakeet-tdt-0.6b-v2` → Grafana
   `meetlab_stt_latency_ms`; expect P50 to hold and turns-served ≈ turns-offered.
6. **Decommission the T4** (`meetlab-stt-gpu`, not in Terraform): stop, then terminate once confident.
7. **Rollback** anytime: revert the `.ebextensions` change → CD redeploys `NEMOTRON_STT_API=shadowfita`.
