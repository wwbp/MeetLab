"""The live tests and the permission contract run against one environment, named by
MEETLAB_ENV (staging by default): with prod, every name they use is production's, and
none is staging's (a production deploy must never check staging and call it green).

Pure/offline. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        python -m unittest tests.test_live_test_env -v
"""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))


def load(module, env):
    with mock.patch.dict(os.environ, {"MEETLAB_ENV": env}):
        sys.modules.pop(module, None)
        return importlib.import_module(module)


class TestLiveTestEnv(unittest.TestCase):
    def tearDown(self):
        for m in ("acceptance_staging", "permission_contract"):
            sys.modules.pop(m, None)

    def test_staging_is_the_default(self):
        live = load("acceptance_staging", "staging")
        self.assertEqual(live.CLUSTER, "meetlab-v2-staging")
        self.assertEqual(live.MEET, "https://meet-staging.wwbp.org")

    def test_prod_names_production_everywhere(self):
        live = load("acceptance_staging", "prod")
        self.assertEqual(live.CLUSTER, "meetlab-v2-prod")
        self.assertEqual(live.MEET, "https://meet-v2.wwbp.org")  # meet.wwbp.org stays v1's until cutover
        self.assertEqual(live.LOGS, "/meetlab-v2/prod")
        self.assertEqual(live.MEDIA_BUCKET, "meetlab-v2-prod-media-848180123498")
        self.assertEqual(live.POOL_BASELINE, 1)  # production keeps one bot machine warm (profiles.tf)

    def test_staging_keeps_no_bot_machine_warm(self):
        self.assertEqual(load("acceptance_staging", "staging").POOL_BASELINE, 0)

    def test_the_contract_checks_production_roles_only(self):
        contract = load("permission_contract", "prod")
        names = " ".join(f"{c.role} {c.resource}" for c in contract.ALLOW + contract.DENY)
        self.assertIn("meetlab-v2-prod-runner-task", names)
        self.assertNotIn("meetlab-v2-staging", names)

    def test_an_unknown_environment_is_refused(self):
        with self.assertRaises(KeyError):
            load("acceptance_staging", "production")


if __name__ == "__main__":
    unittest.main()
