"""Every Bot Config field is in all three places it must be, or researchers can't use it:
the database (agent-runner/db/models.py), the runner's /config API (agent-runner/runner.py)
and the console's Bot Config form (meet/app/(shell)/config/page.tsx). The two services' own
tests can't see each other (each container mounts only its own directory), so this runs from
the repository root in CI (make test-config-parity). Whether the bot *uses* each field is
tests/test_config_contract.py. tts_aggregation_mode was missing from the form, and stt_vad_mode
and stt_delay had no input on screen (2026-10-04).

    python3 agent-runner/tests/config_parity_check.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOT_FIELDS = {"id", "scope", "updated_at", "created_at"}


def columns(models: str) -> set[str]:
    start = models.index("class BotConfig")
    end = models.find("\nclass ", start + 10)
    return set(re.findall(r"^\s+(\w+): Mapped", models[start:end if end > 0 else None], re.M)) - NOT_FIELDS


def missing(models: str, runner: str, form: str) -> dict[str, list[str]]:
    cols = columns(models)
    api = set(re.findall(r'"(\w+)": cfg\.\w+', runner))
    # Shown, not merely carried: an input bound to form.<field> (stt_vad_mode and stt_delay
    # were in the form's data with no input on screen, 2026-10-04).
    in_form = {c for c in cols if re.search(rf"(value|checked)=\{{[^}}]*\bform\.{c}\b", form)}
    return {"runner /config": sorted(cols - api), "console form": sorted(cols - in_form)}


def main() -> int:
    gaps = missing((ROOT / "agent-runner/db/models.py").read_text(), (ROOT / "agent-runner/runner.py").read_text(),
                   (ROOT / "meet/app/(shell)/config/page.tsx").read_text())
    bad = {where: fields for where, fields in gaps.items() if fields}
    for where, fields in bad.items():
        print(f"Bot Config fields missing from the {where}: {', '.join(fields)}")
    if not bad:
        print("every Bot Config field is in the database, the runner's API and the console form")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
