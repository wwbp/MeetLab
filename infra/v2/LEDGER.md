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

**v2 staging, running total:** **$114/month**

| Added | PR | Resource | $/month | Notes |
|---|---|---|---|---|
| 2026-09-30 | runner | agent-runner service + Service Connect namespace | ~0 | Shares the t3.medium; a rolling deploy may briefly add a second |
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

## Decisions

| Date | Decision | Why | Revisit when |
|---|---|---|---|
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
