# v2 ledger — decisions and costs

A running record, updated in the PR that makes each change. Newest first within each
section. Costs are on-demand us-east-1 list prices per month (730 h), before data
transfer. Budget rule (2026-09-30): staging may cost up to v1 production's average.

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

**v2 staging, running total:** **$114/month**, plus $0.085/hour per running bot instance (c6i.large, 0 when idle)

| Added | PR | Resource | $/month | Notes |
|---|---|---|---|---|
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
| 2026-10-02 | **STT NIM on on-demand `g6.xlarge`, not spot** (user's choice) | Spot never started: AWS's placement score for one spot g6.xlarge was 1/10 in every us-east-1 zone, and spot cost $0.56–0.68/hour against $0.805 on-demand, so it saved ~25% at best. Off by default, so on-demand costs only during tests | Before a study: reserved or savings-plan capacity if it runs for hours a day |
| 2026-10-02 | `g6.xlarge` is the smallest instance that runs the Parakeet NIM | NVIDIA's ASR NIM support matrix: Parakeet 0.6b TDT offline needs 13.75 GB GPU memory, and the ASR NIM needs compute capability 8.0+ and 16 GB VRAM. Fractional L4s (g6f) top out at 11.4 GB, and NVIDIA doesn't document fractional GPU support; T4 (g4dn) is compute capability 7.5. g5.xlarge (A10G) also fits but costs $1.006/hour | NVIDIA ships a smaller profile, or load tests show several sessions per GPU |
| 2026-10-02 | First live test passed 2026-10-02 (bots transcribed by it, no NIM errors); switched off again. Staging's Parakeet NIM is an ECS service on an on-demand `g6.xlarge` group (0 to 1), off unless `stt_nim_enabled`; then bots use it, else Deepgram | Same server and GPU as v1, so measurements carry over; ~$0 idle | Load tests: size it; before a study, on-demand or a warm instance |
| 2026-10-02 | Bots reach the NIM through an internal network load balancer, created only while it runs | Bot tasks are one-off RunTask tasks and can't use Service Connect; an NLB is a stable address with health checks, and already within CI's load-balancer permissions | — |
| 2026-10-02 | The NGC key is a Secrets Manager secret `meetlab-v2/staging/ngc` (`{"username":"$oauthtoken","password":...}`), stored by a person | ECS private-registry pulls need that shape; never in Terraform state | — |
| 2026-10-02 | The model cache lives on the instance disk; each cold start rebuilds it (~20 min) | Simplest; staging runs it only for tests | Before a study: EFS cache or a warm instance |
| 2026-10-02 | No in-process bots (iteration 9, completes 4c): `/start` refuses with 500 unless `BOT_DISPATCHER` is `ecs` or `docker`, before any session row exists. The runner no longer mints bot tokens or holds per-speaker sinks | One way to run a bot everywhere (ECS on staging, a container locally), so tests exercise the path production uses; a misconfigured runner can't leave a 'running' session no bot will join | — |
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
