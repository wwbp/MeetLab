"""Right-sizing bots (2026-10-06): the report shows what the heaviest bot task used, so a bot's
CPU and memory reservation (infra/v2/stack/bots.tf) can be set from measurement.
    uv run python -m unittest tests.test_load_report_bots -v
"""
import unittest

from tests.load_report import _queries, markdown


class BotTaskMetricsTest(unittest.TestCase):
    def test_the_report_asks_for_the_cpu_speech_servers_machine(self):
        q = {x["Label"]: x["MetricStat"] for x in _queries()}
        self.assertIn({"Name": "AutoScalingGroupName", "Value": "meetlab-v2-staging-stt-cpu"}, q["stt-cpu_hosts_cpu"]["Metric"]["Dimensions"])

    def test_the_report_asks_for_each_bot_tasks_cpu_and_memory(self):
        q = {x["Label"]: x["MetricStat"] for x in _queries()}
        for label, metric in (("bot_task_cpu", "CpuUtilized"), ("bot_task_mem", "MemoryUtilized")):
            self.assertEqual(q[label]["Metric"]["Namespace"], "ECS/ContainerInsights")
            self.assertEqual(q[label]["Metric"]["MetricName"], metric)
            self.assertIn({"Name": "TaskDefinitionFamily", "Value": "meetlab-v2-staging-bot"}, q[label]["Metric"]["Dimensions"])
            self.assertEqual(q[label]["Stat"], "Maximum", "the heaviest task decides the reservation")

    def test_the_report_shows_the_heaviest_bot_against_its_reservation(self):
        r = {"run": "x", "shape": "spike", "profile": "p", "config": {}, "capacity_rooms": 30, "pass": True,
             "steps": [{"rooms": 30, "turns": 90, "reply_rate": 1.0, "p50_ms": 1, "p95_ms": 1, "p99_ms": 1,
                        "join_p95_s": 1, "pass": True, "why": [],
                        "server": {"stages": {}, "max": {"bot_task_cpu": 141.0, "bot_task_mem": 612.0}}}]}
        text = markdown(r)
        self.assertIn("heaviest bot task", text)
        self.assertIn("141 / 612", text)


if __name__ == "__main__":
    unittest.main()
