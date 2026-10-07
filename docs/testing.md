# Testing

How MeetLab is tested, organised by the industry's standard levels and types, with the pass
criteria each one holds the system to. Test levels and types follow the ISTQB Foundation syllabus
(v4); performance test types follow the ISTQB Performance Testing syllabus (CT-PT), whose seven
types each map to one load-test shape here; pass criteria are written as service level objectives
(SLOs), as Google's SRE practice defines them. Every size production runs was chosen by these
tests (`infra/v2/LEDGER.md`, Measurements; `agent-runner/capacity_model.py`).

## Service level objectives

What every performance test, and production, is held to.

| SLI (what is measured) | SLO (the objective) | Where it is measured |
|---|---|---|
| Reply time: end of a person's speech to the bot's first audio | **p95 ≤ 2,000 ms** (past 2 s a reply feels broken) | the load generator, as a participant hears it |
| Turns answered | **≥ 95%** | the load generator |
| Sessions that fail to start; participants disconnected | **0** | the load generator |
| Sessions left running after a test | **0** | the runner's database |
| Words misheard (speech-to-text) | **≤ 5%** word error rate | stored turns against what was said |
| Every session recorded | **100%** of rooms asked for, available (or marked failed) | the meetings API |
| Harness valid | the load generator kept real time (event-loop lag < 500 ms) | the load generator |

Resource ceilings: CPU and database connections at most 80%, the headroom that keeps queueing out
of the reply time.

## Test levels (ISTQB)

| Level | What it checks | Ours | Command | Runs |
|---|---|---|---|---|
| **Static testing** (static analysis) | defects without running code | eslint, TypeScript, knip (meet); vulture, deptry (agent-runner); `terraform validate`/`fmt`; the image budget | `make test-static` | every PR (CI) |
| **Component** (unit) | one unit in isolation | runner `unittest`, meet `vitest`, the CPU speech server | `make test-unit`, `make test-stt-cpu` | every PR |
| **Component integration** | units through their interfaces | the runner against Postgres; meet's API routes; database migrations (`test_migration_v1_to_v2`) | `make test-integration` | every PR (`ci.yml`) |
| **Contract** (interface agreements) | what one part promises another | a Bot settings field exists in DB, API and form; CI and task roles have exactly the permissions used; Terraform's tests | `make test-config-parity`, `make test-infra`, `permission_contract.py` | every PR / deploy |
| **System and acceptance** (end to end) | real meetings on staging, as a user and an operator | 16+ live scenarios: start/stop, a killed bot replaced, recording, two people, chat, TURN on 443, the study flow, session limits | `acceptance_staging.py` | after every deploy |
| **Regression** (test type) | nothing that worked broke | all of the above, on every PR and deploy | — | — |

## Performance test types (ISTQB CT-PT)

Each runs on staging, configured as production (`docs/load-testing.md`: how to run and read).

| Type | ISTQB definition | Our shape | Parameters | It answers |
|---|---|---|---|---|
| **Load** | handling anticipated realistic load | `load` | climb to the target, hold it (15 min) | does the expected study size meet every SLO? |
| **Stress** | handling peak load, and past it | `stress` | 0.25× → 2× the target | where does it bend, and how? |
| **Spike** | a sudden peak, then back to steady state | `spike` | everyone at once, hold, then 0 | does a study launching at once hold, and recover? |
| **Endurance (soak)** | performance over a long, steady load | `soak` | the target for an hour | leaks, drift, slow decay |
| **Concurrency** | simultaneous actions | `spike` (all rooms join in the same seconds) | as spike | simultaneous joins and bot starts |
| **Capacity** | the most it takes within its objectives | `breakpoint` | +step every few minutes until an SLO breaks | rooms at once, within every SLO |
| **Scalability** | growing to meet future load | `capacity_model.py`, checked by stress and spike runs | machines per part, per study size | what to add, and when |

Latency is reported inside every run, per stage (turn-end wait, LLM first token, first sentence,
voice first audio), and quality too (word error rate, judged replies, voice naturalness): so a
performance test also says *why* a reply was slow and whether it was still good.

## Running everything

```bash
make test-static test-unit test-integration test-infra test-config-parity   # local, ~15 min
# then on staging (a session PR switches the staging profile on): the Load test v2 workflow,
# shapes load, stress, spike, soak, breakpoint (docs/load-testing.md)
```

Results, with the run IDs, go in `infra/v2/LEDGER.md` (Measurements).

Sources: [ISTQB CT-PT syllabus](https://istqb.org/?sdm_process_download=1&download_id=3591);
ISTQB Foundation Level syllabus v4.0 (test levels and types); Google, *Site Reliability
Engineering*, ch. 4 "Service Level Objectives".
