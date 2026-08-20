"""Scaling is configuration, and configuration can silently undo a design.

Production ran the 2026-08-19 pilot on one 2-vCPU instance per service with
autoscaling switched off — min 1, max 1 — which is why five concurrent sessions
was the ceiling and eight collapsed. Raising that is an .ebextensions change so
it ships through CI/CD like anything else, rather than by hand on the console
where it drifts.

The two services are deliberately asymmetric, and the asymmetry is the thing
worth guarding:

**agent-runner scales out.** Bots run in whichever instance served their /start,
so load distributes naturally. The one shared background job — reconciling stale
conversations — is idempotent, so instances duplicate effort rather than corrupt
state.

**meet must not.** Seven modules under lib/ keep domain state on ``globalThis``:
bot-room claims, start locks, request history, participant presence. Two
instances would hold different answers to "does this room already have a bot",
so scaling meet out is a correctness failure, not a capacity trade-off. It stays
pinned until that state moves to Postgres.

That last one is why these are tests and not a comment. A future change that
raises meet's MaxSize to handle load would look entirely reasonable, pass every
behavioural test, and start handing rooms two bots. Here it fails instead.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

REPO = Path(__file__).resolve().parent.parent.parent
MEET_LIB = REPO / "meet" / "lib"


def option_settings(service: str) -> dict:
    """Flatten every .ebextensions option we can see for a service."""
    found: dict[str, str] = {}
    ebx = REPO / service / ".ebextensions"
    for path in sorted(ebx.glob("*.config")):
        text = path.read_text()
        for key in ("MinSize", "MaxSize", "InstanceType", "InstanceTypes"):
            m = re.search(rf"^\s*{key}:\s*(.+?)\s*$", text, re.MULTILINE)
            if m:
                found[key] = m.group(1).strip().strip('"').strip("'")
    return found


def meet_modules_with_global_state() -> list[str]:
    """Modules keeping domain state on globalThis, excluding tests."""
    hits = []
    for path in MEET_LIB.rglob("*.ts"):
        if ".test." in path.name:
            continue
        if "globalThis" in path.read_text():
            hits.append(path.relative_to(MEET_LIB).as_posix())
    return sorted(hits)


class TestAgentRunnerScalesOut(unittest.TestCase):
    def test_autoscaling_is_enabled(self):
        """max 1 is what made eight concurrent sessions collapse."""
        opts = option_settings("agent-runner")
        self.assertIn("MaxSize", opts, "agent-runner has no autoscaling config")
        self.assertGreater(
            int(opts["MaxSize"]), 1,
            "with MaxSize=1 nothing can be added under load; the pilot ceiling "
            "of five sessions was this setting, not the code",
        )

    def test_there_is_more_than_one_instance_at_rest(self):
        """A single instance means a deploy or a crash is a full outage."""
        opts = option_settings("agent-runner")
        self.assertGreaterEqual(int(opts.get("MinSize", 1)), 2)

    def test_the_instance_has_more_than_two_cores(self):
        """Measured: one 3-person session used 24% of 2 vCPU, and five 1:1
        sessions used 64%. Two cores cannot carry a pilot."""
        opts = option_settings("agent-runner")
        itype = opts.get("InstanceType") or opts.get("InstanceTypes", "")
        self.assertTrue(itype, "no instance type pinned in config")
        self.assertNotIn(
            "t3.medium", itype,
            "t3.medium is 2 vCPU and burstable — it throttles after hours of "
            "sustained load, which is exactly a pilot's shape",
        )


class TestMeetStaysPinned(unittest.TestCase):
    """The guard that matters most, because unpinning would look sensible."""

    def test_meet_is_still_holding_state_in_memory(self):
        """If this fails, the state moved and the pin below can be revisited."""
        self.assertTrue(
            meet_modules_with_global_state(),
            "no globalThis state left in meet/lib — the pin on MaxSize can now "
            "be lifted, and this test should be replaced",
        )

    def test_meet_is_not_scaled_out_while_that_is_true(self):
        opts = option_settings("meet")
        if "MaxSize" not in opts:
            self.skipTest("meet has no autoscaling config, so it cannot scale out")
        self.assertEqual(
            int(opts["MaxSize"]), 1,
            "meet keeps bot-room claims and start locks on globalThis, so two "
            f"instances disagree about which rooms have bots. Offending "
            f"modules: {meet_modules_with_global_state()}",
        )


if __name__ == "__main__":
    unittest.main()
