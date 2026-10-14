"""The repository's layout (user, 2026-10-14): one directory per deployable under services/,
shared code under packages/, tooling under tools/; the old agent-runner/ is gone. Run from the
repo root (make test-static):  python3 tests/system/layout_check.py
"""
import os
import sys

expected = {
    "services/api/meetlab_api": "the API: sessions, bot dispatch, recordings, settings",
    "services/bot/meetlab_bot": "the bot: one meeting's pipeline",
    "packages/core/meetlab_core": "shared: config, database, sessions, heartbeat, storage, events",
    "packages/core/meetlab_core/migrations": "the database schema's migrations",
    "tools/loadtest/meetlab_loadtest": "the load-test harness",
}
failures = [f"missing {d} ({why})" for d, why in expected.items() if not os.path.isdir(d)]
if os.path.exists("agent-runner"):
    failures.append("agent-runner/ still exists: its code belongs under services/ and packages/")
for f in failures:
    print("FAIL", f)
sys.exit(1 if failures else 0)
