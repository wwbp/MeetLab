# Moving v1's data to v2 production

v2 production starts from a **copy** of v1's data. v1 is never changed, so until the first real
study runs on v2, switching back is moving the address back. Nothing moves until a check shows
every row and every file arrived.

## What moves

| Data | From | To |
|---|---|---|
| Database (conversations, utterances, speakers, media index, events, Bot Config) | v1 RDS `meetlab`, Postgres 17.9 | a snapshot restored into v2 production (encrypted on the way), then v2's six upgrades |
| Recordings, per-speaker audio, transcripts | S3 `meetlab-media-848180123498-us-east-1-an` | v2 production's bucket, **same object names**, so database rows need no rewrite |
| Completion-code secret | v1's LiveKit secret | kept for codes, so past codes can still be checked |

Logs (CloudWatch, 90 days) and metrics (Grafana) stay where they are. Staging's data is test
data; only the voiced conversation library is copied (`data/conversation-library/`).

## Only the pilot arrives

v2 production keeps only the pilot (decided 2026-10-06): the sessions participants joined
through start links (`link-*` rooms: 79 of v1's 558) with their turns, speakers, recordings and
events, plus every Bot Config row (the study conditions). Load tests, benches and developer
tests stay in v1 and its snapshot. After the upgrade, `migration_check.prune_to_pilot` deletes
the rest from the copy (never from v1); `migration_check.py SOURCE TARGET --pilot` then checks
that every pilot row arrived whole and nothing else did. Only the kept sessions' files are copied.

## What v2's upgrades change in v1's rows

Four of the six only add columns. Two rewrite rows, and these are the only changes allowed
(`migration_check.V1_TO_V2`):

- the older of two "running" sessions in one room is ended (none in v1 as of 2026-10-06);
- every Bot Config row at the 300 ms end-of-turn timer moves to 50 ms, study conditions included
  (decided 2026-10-06).

`tests/test_migration_v1_to_v2.py` builds a v1 database with v1-shaped rows, upgrades it, and
fails if anything else differs. It also proves the check catches a lost row and a changed value.

## Phases

| Phase | What | Gate |
|---|---|---|
| 0. Protect v1 | manual snapshot, deletion protection, bucket versioning, read-only inventory | ✅ 2026-10-06 (ledger, Manual actions) |
| 1. The check, test-first | `migration_check.py` and its test | ✅ green in CI |
| 2. Build v2 production | the staging Terraform as `prod`: production sizes, database across zones, its own versioned bucket; no public address | live tests pass on its private address |
| 3. Rehearse | restore today's v1 snapshot, upgrade, copy the bucket, run the check and the live tests, time each step; throw it away | `MATCH`; repeat until clean |
| 4. Cutover (~2 h, no study booked) | v1 stops taking sessions; final snapshot; restore, upgrade, prune to the pilot, copy the pilot's files; the check (`--pilot`); move the address; live tests and one real call | `MATCH`, then live tests, then the call; any failure, the address goes back to v1 |
| 5. Hold | v1 stopped but whole | 2–4 weeks of real studies on v2 |
| 6. Retire | staging, then v1: final snapshot, test-restore it, then remove; v1's bucket kept read-only | each final snapshot restores and matches |

Before v1's machines are removed: 2 media rows hold local file paths (files on v1's own
machines) and 32 recordings never finished; check both.

## The check

```bash
# Database: v1 (source) against v2 (target, pruned to the pilot); prints row counts, allowed
# changes, then MATCH or NO MATCH (a missing, changed or extra row is a mismatch)
uv run python migration_check.py "$V1_DATABASE_URL" "$V2_DATABASE_URL" --pilot
```

Files: `migration_check.manifest(bucket)` lists every object with its size and SHA-256 (it reads
each file, so run it in-region), and `compare_files(v1, v2)` must return nothing.
