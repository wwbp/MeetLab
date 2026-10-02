"""Pre-warming the bot pool before a study (capacity.py).

A cold bot takes 132 s to join (4c spike), so before a study the console raises the
bot group's minimum, and an AWS scheduled action drops it back to 0 at the end time.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import capacity

GROUP = "meetlab-v2-staging-bots"
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


CLUSTER = "meetlab-v2-staging"


def _ecs(registered=()):
    ecs = mock.Mock()
    ecs.list_container_instances.return_value = {"containerInstanceArns": [f"arn:{i}" for i in registered]}
    return ecs


def _asg(min_size=0, max_size=2, desired=0, in_service=0, scheduled=None, stuck=0):
    asg = mock.Mock()
    asg.describe_auto_scaling_groups.return_value = {"AutoScalingGroups": [{
        "MinSize": min_size, "MaxSize": max_size, "DesiredCapacity": desired,
        "Instances": [{"InstanceId": f"i-{n}", "LifecycleState": "InService", "HealthStatus": "Healthy"}
                      for n in range(in_service)]
        + [{"InstanceId": "i-stuck", "LifecycleState": "Terminating:Wait", "HealthStatus": "Unhealthy"}] * stuck,
    }]}
    asg.describe_scheduled_actions.return_value = {"ScheduledUpdateGroupActions": (
        [{"ScheduledActionName": capacity.SCHEDULE, "StartTime": scheduled}] if scheduled else [])}
    return asg


class InstancesForTests(unittest.TestCase):
    def test_rounds_sessions_up_to_whole_instances(self):
        self.assertEqual(capacity.instances_for(sessions=4, per_instance=3, ceiling=10), 2)

    def test_never_more_than_the_group_allows(self):
        self.assertEqual(capacity.instances_for(sessions=50, per_instance=3, ceiling=2), 2)


class PrewarmTests(unittest.TestCase):
    def test_raises_the_minimum_and_schedules_its_return_to_zero(self):
        asg, until = _asg(), NOW + timedelta(hours=3)
        capacity.prewarm(asg, GROUP, sessions=4, until=until, per_instance=3, now=NOW, baseline=1)
        asg.update_auto_scaling_group.assert_called_once_with(AutoScalingGroupName=GROUP, MinSize=2)
        asg.put_scheduled_update_group_action.assert_called_once_with(
            AutoScalingGroupName=GROUP, ScheduledActionName=capacity.SCHEDULE, StartTime=until, MinSize=1)

    def test_never_prepares_below_the_always_warm_baseline(self):
        asg = _asg()
        capacity.prewarm(asg, GROUP, sessions=1, until=NOW + timedelta(hours=1), per_instance=3, now=NOW, baseline=2)
        asg.update_auto_scaling_group.assert_called_once_with(AutoScalingGroupName=GROUP, MinSize=2)

    def test_says_when_the_group_cannot_hold_every_session(self):
        out = capacity.prewarm(_asg(max_size=2), GROUP, sessions=50, until=NOW + timedelta(hours=1),
                               per_instance=3, now=NOW, baseline=1)
        self.assertEqual((out["instances"], out["capped"]), (2, True))

    def test_refuses_an_end_time_in_the_past_or_more_than_a_day_away(self):
        # A forgotten warm pool costs money all night; a past end time would never fire.
        for until in (NOW - timedelta(minutes=1), NOW + timedelta(hours=25)):
            with self.assertRaises(ValueError):
                capacity.prewarm(_asg(), GROUP, sessions=1, until=until, per_instance=3, now=NOW, baseline=1)

    def test_refuses_fewer_than_one_session(self):
        with self.assertRaises(ValueError):
            capacity.prewarm(_asg(), GROUP, sessions=0, until=NOW + timedelta(hours=1), per_instance=3, now=NOW, baseline=1)


class CancelTests(unittest.TestCase):
    def test_returns_to_the_baseline_and_removes_the_scheduled_end(self):
        asg = _asg(min_size=2, scheduled=NOW)
        capacity.cancel(asg, GROUP, baseline=1)
        asg.update_auto_scaling_group.assert_called_once_with(AutoScalingGroupName=GROUP, MinSize=1)
        asg.delete_scheduled_action.assert_called_once_with(AutoScalingGroupName=GROUP, ScheduledActionName=capacity.SCHEDULE)

    def test_cancelling_with_nothing_scheduled_is_fine(self):
        asg = _asg()
        capacity.cancel(asg, GROUP, baseline=1)
        asg.delete_scheduled_action.assert_not_called()


class StatusTests(unittest.TestCase):
    def test_reports_warm_instances_and_when_warming_ends(self):
        out = capacity.status(_asg(min_size=2, desired=2, in_service=1, scheduled=NOW), _ecs(["i-0"]), CLUSTER, GROUP, per_instance=3)
        self.assertEqual(out, {"min_instances": 2, "max_instances": 2, "desired_instances": 2,
                               "ready_instances": 1, "sessions_per_instance": 3, "warm_until": NOW.isoformat(),
                               "unhealthy_instances": 0})

    def test_counts_machines_stuck_unhealthy_in_the_group(self):
        # 2026-10-02: a bot machine stopped by hand sat in Terminating:Wait for 27 h; the
        # ECS draining hook can't finish on a stopped machine, and the pool never scaled in.
        out = capacity.status(_asg(in_service=1, stuck=1), _ecs(), CLUSTER, GROUP, per_instance=3)
        self.assertEqual(out["unhealthy_instances"], 1)


    def test_a_machine_is_ready_only_once_ecs_can_place_a_bot_on_it(self):
        # Auto Scaling says InService as soon as EC2 starts the machine; ECS registers it
        # about a minute later (2026-10-02: "ready 16 s after Prepare", from cold).
        ecs = _ecs()
        out = capacity.status(_asg(min_size=1, desired=1, in_service=1, scheduled=NOW), ecs, CLUSTER, GROUP, per_instance=3)
        self.assertEqual(out["ready_instances"], 0)
        ecs.list_container_instances.assert_called_once_with(
            cluster=CLUSTER, status="ACTIVE", filter="ec2InstanceId in ['i-0']")  # ECS's syntax, checked live

    def test_an_empty_pool_asks_ecs_nothing(self):
        ecs = _ecs()
        self.assertEqual(capacity.status(_asg(), ecs, CLUSTER, GROUP, per_instance=3)["ready_instances"], 0)
        ecs.list_container_instances.assert_not_called()


if __name__ == "__main__":
    unittest.main()
