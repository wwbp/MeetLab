"""The decision matrix in docs/infrastructure.md is capacity_model.matrix(), row for row, so the
page can't drift from the model (user, 2026-10-14). Run from the repo root (make test-static):

    python3 tests/system/capacity_doc_check.py
"""
import sys

sys.path.insert(0, "services/api")
from meetlab_api.capacity_model import matrix  # noqa: E402

doc = open("docs/infrastructure.md").read()
missing = [row for row in (
    f"| {r['rooms']} | {r['bot_machines']} | {r['stt']} | {r['db']} | {r['binding']} "
    f"| ${r['usd_per_hour']:.2f} | ${r['usd_per_month']:,.0f} |" for r in matrix()) if row not in doc]
for row in missing:
    print(f"FAIL docs/infrastructure.md lacks the model's row: {row}")
sys.exit(1 if missing else 0)
