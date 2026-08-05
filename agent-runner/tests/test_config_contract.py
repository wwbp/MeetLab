"""Every bot_config field must actually reach the pipeline.

`vad_stop_secs` shipped for months as a full-stack feature — DB column, API
validation, a slider in the console — while its only appearance in `bot.py` was
inside an f-string being logged. Operators tuned it during a live pilot and it
changed nothing. Round-trip tests ("PUT accepts it, GET echoes it back") all
passed the whole time, because storage was never the broken part.

This module tests the thing that was actually broken: **is the field consumed?**
A field read only into a log message is dead, and dead config is worse than no
config because it absorbs the effort that would otherwise find the real control.

The check is deliberately generic rather than a list of hand-written per-field
assertions — a new knob is covered the day it is added, and a knob that quietly
loses its last real reader fails here immediately.

See docs/distillation-audit.md (Iteration 1) and docs/pilot-postmortem-2026-08.md.
"""

import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db.models import BotConfig

AGENT_RUNNER_DIR = Path(__file__).resolve().parent.parent
BOT_PY = AGENT_RUNNER_DIR / "bot.py"

# Columns that are legitimately not read by the bot pipeline, with the reason.
# Adding to this set is a deliberate act — it should need a justification, which
# is why the reason is required rather than a bare name.
NOT_PIPELINE_FIELDS = {
    "id": "primary key",
    "scope": "row selector, consumed by load_bot_config not the pipeline",
    "updated_at": "audit timestamp",
    "session_limit_minutes": (
        "advisory browser-side countdown; served to the client via meet's "
        "/api/connection-details and never read by the bot (see lib/session-limit.ts)"
    ),
}

# Known-dead fields. Each entry is a standing bug, not an exemption: the test
# asserts these are *still* dead so the cleanup is tracked, and fails loudly if
# one is quietly resurrected without removing it from here.
KNOWN_DEAD_FIELDS = {
    "vad_stop_secs": (
        "Read once, into a log f-string. Exposed as a console slider that does "
        "nothing. Scheduled for removal end-to-end — column, API validation, UI."
    ),
}


def _config_reads(source_path: Path) -> dict[str, list[bool]]:
    """Map each `bot_config.<field>` read to whether it sits inside an f-string.

    Returns {field: [is_inside_fstring, ...]} across every read in the file.
    A field whose reads are *all* True is consumed only by logging — i.e. dead.
    Also catches the `getattr(bot_config, "field", default)` form.
    """
    tree = ast.parse(source_path.read_text())

    # Every AST node that lives inside an f-string, so a read can be classified.
    in_fstring: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for child in ast.walk(node):
                in_fstring.add(id(child))

    reads: dict[str, list[bool]] = {}
    for node in ast.walk(tree):
        field = None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in ("bot_config", "cfg", "config"):
                field = node.attr
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "getattr":
            # getattr(bot_config, "stt_endpointing_ms", 200)
            if (
                len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in ("bot_config", "cfg", "config")
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                field = node.args[1].value
        if field:
            reads.setdefault(field, []).append(id(node) in in_fstring)
    return reads


class ConfigContractTests(unittest.TestCase):
    def setUp(self):
        self.columns = {c.name for c in BotConfig.__table__.columns}
        self.reads = _config_reads(BOT_PY)

    def test_every_pipeline_field_is_read_outside_a_log_statement(self):
        """The core contract: a configurable knob must change behaviour.

        If this fails for a new field, the field is either dead (wire it up or
        delete it) or genuinely not a pipeline concern (add it to
        NOT_PIPELINE_FIELDS with a reason).
        """
        expected = self.columns - set(NOT_PIPELINE_FIELDS) - set(KNOWN_DEAD_FIELDS)
        dead = []
        for field in sorted(expected):
            uses = self.reads.get(field, [])
            if not uses:
                dead.append(f"{field}: never read in bot.py")
            elif all(uses):
                dead.append(f"{field}: read {len(uses)}× but only inside f-strings (log-only)")

        self.assertEqual(
            dead,
            [],
            "bot_config fields that do not affect the pipeline:\n  " + "\n  ".join(dead),
        )

    def test_known_dead_fields_are_still_dead(self):
        """Tracks the cleanup backlog, and catches accidental resurrection.

        If a field here starts doing something, that is good news — remove it from
        KNOWN_DEAD_FIELDS so the contract test above starts guarding it properly.
        """
        for field in KNOWN_DEAD_FIELDS:
            self.assertIn(field, self.columns, f"{field} is gone — drop it from KNOWN_DEAD_FIELDS")
            uses = self.reads.get(field, [])
            self.assertTrue(
                uses and all(uses),
                f"{field} now has a real reader — remove it from KNOWN_DEAD_FIELDS "
                f"so it is covered by the contract test",
            )

    def test_exemptions_reference_real_columns(self):
        """Stops the allow-lists rotting into a list of names that no longer exist."""
        for field in {**NOT_PIPELINE_FIELDS, **KNOWN_DEAD_FIELDS}:
            self.assertIn(field, self.columns, f"exempted field {field!r} is not a bot_config column")

    def test_every_exemption_carries_a_reason(self):
        for field, reason in {**NOT_PIPELINE_FIELDS, **KNOWN_DEAD_FIELDS}.items():
            self.assertTrue(reason and reason.strip(), f"{field} is exempted with no reason given")


if __name__ == "__main__":
    unittest.main()
