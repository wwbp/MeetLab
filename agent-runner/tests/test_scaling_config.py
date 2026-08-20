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

**meet must not**, because seven modules under its lib/ keep domain state on
``globalThis``. That half of the guard lives in meet/lib/scaling-config.test.ts,
where it can see the modules it asserts about — this container only mounts
agent-runner.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

SERVICE_ROOT = Path(__file__).resolve().parent.parent


def option_settings() -> dict:
    """Flatten every .ebextensions option for agent-runner."""
    found: dict[str, str] = {}
    ebx = SERVICE_ROOT / ".ebextensions"
    for path in sorted(ebx.glob("*.config")):
        text = path.read_text()
        for key in ("MinSize", "MaxSize", "InstanceType", "InstanceTypes"):
            m = re.search(rf"^\s*{key}:\s*(.+?)\s*$", text, re.MULTILINE)
            if m:
                found[key] = m.group(1).strip().strip('"').strip("'")
    return found


class TestAgentRunnerScalesOut(unittest.TestCase):
    def test_autoscaling_is_enabled(self):
        """max 1 is what made eight concurrent sessions collapse."""
        opts = option_settings()
        self.assertIn("MaxSize", opts, "agent-runner has no autoscaling config")
        self.assertGreater(
            int(opts["MaxSize"]), 1,
            "with MaxSize=1 nothing can be added under load; the pilot ceiling "
            "of five sessions was this setting, not the code",
        )

    def test_there_is_more_than_one_instance_at_rest(self):
        """A single instance means a deploy or a crash is a full outage."""
        opts = option_settings()
        self.assertGreaterEqual(int(opts.get("MinSize", 1)), 2)

    def test_the_instance_has_more_than_two_cores(self):
        """Measured: one 3-person session used 24% of 2 vCPU, and five 1:1
        sessions used 64%. Two cores cannot carry a pilot."""
        opts = option_settings()
        itype = opts.get("InstanceType") or opts.get("InstanceTypes", "")
        self.assertTrue(itype, "no instance type pinned in config")
        self.assertNotIn(
            "t3.medium", itype,
            "t3.medium is 2 vCPU and burstable — it throttles after hours of "
            "sustained load, which is exactly a pilot's shape",
        )


if __name__ == "__main__":
    unittest.main()
