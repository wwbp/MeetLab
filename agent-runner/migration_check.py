"""Prove a copy lost nothing: every row of every table, and every file, matches its source.

A fingerprint is one hash per row, keyed by primary key, over the source's columns only, so
columns a later schema adds don't count as differences. `compare` lists what is missing or
changed; changes to `allowed` columns are reported apart (they're expected, but are counted).

    uv run python migration_check.py SOURCE_DATABASE_URL TARGET_DATABASE_URL [--pilot]
"""
import asyncio
import hashlib
import sys
from dataclasses import dataclass, field

import asyncpg


# What v2's upgrades may change in v1's rows; nothing else may differ.
#  - one running session per room (b3d9e5f1a2c8): the older of two running sessions in a room ends
#  - end-of-turn timer 300 -> 50 ms (c4d2e7f1a9b3): every Bot Config row at 300, the study
#    conditions included (user's decision, 2026-10-06; all 55 of v1's rows were at 300)
V1_TO_V2 = {"conversations": {"status", "ended_at"}, "bot_config": {"user_speech_timeout_ms"}}


# v2 production keeps only the pilot (user, 2026-10-06): sessions participants joined through
# start links, and what belongs to them. Bot Config is kept whole (the study conditions).
PILOT_ROOMS = "link-%"
_KEPT = f"SELECT id FROM conversations WHERE room_name LIKE '{PILOT_ROOMS}'"


async def pilot_rows(conn) -> dict[str, set[str]]:
    """The primary keys to keep, per table (bot_config: absent = all of it)."""
    q = {
        "conversations": _KEPT,
        "utterances": f"SELECT id FROM utterances WHERE conv_id IN ({_KEPT})",
        "speakers": f"SELECT DISTINCT speaker_id FROM utterances WHERE conv_id IN ({_KEPT})",
        "media_files": f"SELECT id FROM media_files WHERE conv_id IN ({_KEPT})",
        "events": f"SELECT id::text FROM events WHERE conv_id IN ({_KEPT})",
    }
    return {t: {r[0] for r in await conn.fetch(sql)} for t, sql in q.items()}


async def prune_to_pilot(conn) -> None:
    """Delete everything but the pilot from a copy (never from v1), children before parents."""
    async with conn.transaction():
        await conn.execute(f"DELETE FROM events WHERE conv_id IS NULL OR conv_id NOT IN ({_KEPT})")
        await conn.execute(f"DELETE FROM media_files WHERE conv_id NOT IN ({_KEPT})")
        await conn.execute(f"DELETE FROM utterances WHERE conv_id NOT IN ({_KEPT})")
        await conn.execute(f"DELETE FROM conversations WHERE id NOT IN ({_KEPT})")
        await conn.execute("DELETE FROM speakers WHERE id NOT IN (SELECT speaker_id FROM utterances)")


def restrict(source: dict, keep: dict[str, set[str]]) -> dict:
    """The source's fingerprint narrowed to the rows that should arrive."""
    return {t: {**spec, "rows": {pk: v for pk, v in spec["rows"].items() if t not in keep or pk in keep[t]}}
            for t, spec in source.items()}


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    changed: dict[str, list[str]] = field(default_factory=dict)  # allowed changes, by table


async def _tables(conn) -> dict[str, dict]:
    rows = await conn.fetch("""
        SELECT c.table_name, array_agg(c.column_name::text ORDER BY c.ordinal_position) AS columns
        FROM information_schema.columns c JOIN information_schema.tables t USING (table_schema, table_name)
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' AND c.table_name <> 'alembic_version'
        GROUP BY 1""")
    out = {}
    for r in rows:
        pk = await conn.fetch("""
            SELECT a.attname::text FROM pg_index i JOIN pg_attribute a
              ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = $1::regclass AND i.indisprimary ORDER BY a.attnum""", r["table_name"])
        out[r["table_name"]] = {"columns": list(r["columns"]), "pk": [p[0] for p in pk]}
    return out


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def fingerprint(conn, like: dict | None = None) -> dict:
    """{table: {"columns", "pk", "rows": {pk: {column: md5}}}}; `like` = use its tables and columns."""
    tables = {t: {"columns": v["columns"], "pk": v["pk"]} for t, v in like.items()} if like else await _tables(conn)
    for table, spec in tables.items():
        cols = ", ".join(f"md5(coalesce(to_jsonb({_q(c)})::text, 'NULL')) AS {_q(c)}" for c in spec["columns"])
        key = " || '/' || ".join(f"{_q(c)}::text" for c in spec["pk"])
        rows = await conn.fetch(f"SELECT {key} AS _pk, {cols} FROM {_q(table)}")
        spec["rows"] = {r["_pk"]: {c: r[c] for c in spec["columns"]} for r in rows}
    return tables


def _sample(keys) -> str:
    keys = sorted(keys)
    return ", ".join(keys[:10]) + (f" (+{len(keys) - 10} more)" if len(keys) > 10 else "")


def compare(source: dict, target: dict, allowed: dict[str, set[str]] | None = None) -> Report:
    allowed = allowed or {}
    report = Report()
    for table, spec in sorted(source.items()):
        src, dst = spec["rows"], target.get(table, {}).get("rows", {})
        missing = src.keys() - dst.keys()
        if missing:
            report.problems.append(f"{table}: {len(missing)} row(s) missing: {_sample(missing)}")
        extra = dst.keys() - src.keys()
        if extra:  # a copy holds what its source held (a pilot copy: the pilot), nothing else
            report.problems.append(f"{table}: {len(extra)} row(s) that should not be there: {_sample(extra)}")
        changed, expected = [], []
        for pk in src.keys() & dst.keys():
            diff = {c for c in spec["columns"] if src[pk][c] != dst[pk][c]}
            if diff - allowed.get(table, set()):
                changed.append(pk)
            elif diff:
                expected.append(pk)
        if changed:
            report.problems.append(f"{table}: {len(changed)} row(s) changed: {_sample(changed)}")
        if expected:
            report.changed[table] = sorted(expected)
    return report


def compare_files(source: dict[str, tuple], target: dict[str, tuple]) -> list[str]:
    """Manifests are {key: (size, sha256)}. Files only in the target are fine; v2 adds its own."""
    missing = source.keys() - target.keys()
    different = [k for k in source.keys() & target.keys() if source[k] != target[k]]
    problems = []
    if missing:
        problems.append(f"files: {len(missing)} missing: {_sample(missing)}")
    if different:
        problems.append(f"files: {len(different)} different: {_sample(different)}")
    return problems


def manifest(bucket: str, s3=None) -> dict[str, tuple]:
    """{key: (size, sha256)} of every object, read in full (run it in-region)."""
    import boto3
    s3 = s3 or boto3.client("s3")
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            h = hashlib.sha256()
            for chunk in s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].iter_chunks(1 << 20):
                h.update(chunk)
            out[obj["Key"]] = (obj["Size"], h.hexdigest())
    return out


async def _main(source_url: str, target_url: str, pilot: bool = False) -> int:
    src, dst = await asyncpg.connect(source_url), await asyncpg.connect(target_url)
    try:
        before = await fingerprint(src)
        if pilot:  # the target was pruned to the pilot (prune_to_pilot): compare just that
            before = restrict(before, await pilot_rows(src))
        report = compare(before, await fingerprint(dst, like=before), allowed=V1_TO_V2)
    finally:
        await src.close()
        await dst.close()
    for table, spec in sorted(before.items()):
        print(f"{table}: {len(spec['rows'])} row(s)")
    for table, pks in report.changed.items():
        print(f"allowed change: {table}: {_sample(pks)}")
    for p in report.problems:
        print(f"PROBLEM {p}")
    print("MATCH" if not report.problems else "NO MATCH")
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1], sys.argv[2], pilot="--pilot" in sys.argv[3:])))
