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


def _asg(min_size=0, max_size=2, desired=0, in_service=0, scheduled=None):
    asg = mock.Mock()
    asg.describe_auto_scaling_groups.return_value = {"AutoScalingGroups": [{
        "MinSize": min_size, "MaxSize": max_size, "DesiredCapacity": desired,
        "Instances": [{"LifecycleState": "InService"}] * in_service,
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
        capacity.prewarm(asg, GROUP, sessions=4, until=until, per_instance=3, now=NOW)
        asg.update_auto_scaling_group.assert_called_once_with(AutoScalingGroupName=GROUP, MinSize=2)
        asg.put_scheduled_update_group_action.assert_called_once_with(
            AutoScalingGroupName=GROUP, ScheduledActionName=capacity.SCHEDULE, StartTime=until, MinSize=0)

    def test_says_when_the_group_cannot_hold_every_session(self):
        out = capacity.prewarm(_asg(max_size=2), GROUP, sessions=50, until=NOW + timedelta(hours=1),
                               per_instance=3, now=NOW)
        self.assertEqual((out["instances"], out["capped"]), (2, True))

    def test_refuses_an_end_time_in_the_past_or_more_than_a_day_away(self):
        # A forgotten warm pool costs money all night; a past end time would never fire.
        for until in (NOW - timedelta(minutes=1), NOW + timedelta(hours=25)):
            with self.assertRaises(ValueError):
                capacity.prewarm(_asg(), GROUP, sessions=1, until=until, per_instance=3, now=NOW)

    def test_refuses_fewer_than_one_session(self):
        with self.assertRaises(ValueError):
            capacity.prewarm(_asg(), GROUP, sessions=0, until=NOW + timedelta(hours=1), per_instance=3, now=NOW)


class CancelTests(unittest.TestCase):
    def test_drops_the_minimum_and_removes_the_scheduled_end(self):
        asg = _asg(min_size=2, scheduled=NOW)
        capacity.cancel(asg, GROUP)
        asg.update_auto_scaling_group.assert_called_once_with(AutoScalingGroupName=GROUP, MinSize=0)
        asg.delete_scheduled_action.assert_called_once_with(AutoScalingGroupName=GROUP, ScheduledActionName=capacity.SCHEDULE)

    def test_cancelling_with_nothing_scheduled_is_fine(self):
        asg = _asg()
        capacity.cancel(asg, GROUP)
        asg.delete_scheduled_action.assert_not_called()


class StatusTests(unittest.TestCase):
    def test_reports_warm_instances_and_when_warming_ends(self):
        out = capacity.status(_asg(min_size=2, desired=2, in_service=1, scheduled=NOW), GROUP, per_instance=3)
        self.assertEqual(out, {"min_instances": 2, "max_instances": 2, "desired_instances": 2,
                               "ready_instances": 1, "sessions_per_instance": 3, "warm_until": NOW.isoformat()})


if __name__ == "__main__":
    unittest.main()
