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
| 2026-09-30 | **Load testing (50 / 100 sessions)** | Staging uses v1's vendor keys (OpenAI, ElevenLabs, Deepgram, LiveKit), which share v1's quotas and bill; an exhausted ElevenLabs quota makes bots silent with no error | Staging has its own keys, or free drop-in models for STT/TTS/LLM, so scale tests measure our infrastructure without spending vendor quota |

## Follow-ups found along the way

| Found | Item | Where | Why it matters |
|---|---|---|---|
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
| STT | Deepgram (`STT_MODEL_OVERRIDE`); NIM to come as `g6.xlarge` **spot**, 0 instances unless a test needs it (~$0.55/hour on) | On-demand or reserved NIM capacity, sized from the test |
| Bot pool | `c6i.large`, 0 to 2 | Instance type, floor and ceiling from measured per-session load |
| Services instance | one `t3.medium` | Sized from the test |
| Database | `db.t4g.small`, single-AZ | Sized up, multi-AZ before a study |
| NAT | one | One per AZ before production |

## Decisions

| Date | Decision | Why | Revisit when |
|---|---|---|---|
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
| 2026-09-30 | CI permissions are inline policies, at 7.1k of the 10.2k-character limit per role | Simplest while small | Move to managed policies when the next PR would pass the limit |
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
