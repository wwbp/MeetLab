"""Pre-warming the bot pool before a study: the console's "Prepare for study".

A cold bot takes 132 s to join (4c spike: instance boot, image pull), so before a
study the bot group's minimum goes up, and an AWS scheduled action drops it back to 0
at the end time. AWS does the expiry; nothing here has to remember it. Cancelling
drops the minimum at once.

ECS managed scaling still owns the desired count; it can't go below the minimum, and
managed termination protection keeps instances with running bots on scale-in.
"""
import math
from datetime import datetime, timedelta

SCHEDULE = "meetlab-prewarm-end"
MAX_WARM = timedelta(hours=24)  # a forgotten warm pool costs money all night


def instances_for(sessions: int, per_instance: int, ceiling: int) -> int:
    return min(ceiling, math.ceil(sessions / per_instance))


def _group(asg, group: str) -> dict:
    return asg.describe_auto_scaling_groups(AutoScalingGroupNames=[group])["AutoScalingGroups"][0]


def _scheduled_end(asg, group: str):
    actions = asg.describe_scheduled_actions(
        AutoScalingGroupName=group, ScheduledActionNames=[SCHEDULE])["ScheduledUpdateGroupActions"]
    return actions[0]["StartTime"] if actions else None


def prewarm(asg, group: str, sessions: int, until: datetime, per_instance: int, now: datetime) -> dict:
    if sessions < 1:
        raise ValueError("sessions must be at least 1")
    if not now < until <= now + MAX_WARM:
        raise ValueError("the end time must be in the future and within 24 hours")
    ceiling = _group(asg, group)["MaxSize"]
    n = instances_for(sessions, per_instance, ceiling)
    asg.update_auto_scaling_group(AutoScalingGroupName=group, MinSize=n)
    asg.put_scheduled_update_group_action(
        AutoScalingGroupName=group, ScheduledActionName=SCHEDULE, StartTime=until, MinSize=0)
    return {"instances": n, "capped": n * per_instance < sessions}


def cancel(asg, group: str) -> None:
    asg.update_auto_scaling_group(AutoScalingGroupName=group, MinSize=0)
    if _scheduled_end(asg, group) is not None:
        asg.delete_scheduled_action(AutoScalingGroupName=group, ScheduledActionName=SCHEDULE)


def status(asg, group: str, per_instance: int) -> dict:
    g = _group(asg, group)
    end = _scheduled_end(asg, group)
    return {
        "min_instances": g["MinSize"],
        "max_instances": g["MaxSize"],
        "desired_instances": g["DesiredCapacity"],
        "ready_instances": sum(1 for i in g["Instances"] if i["LifecycleState"] == "InService"),
        "sessions_per_instance": per_instance,
        "warm_until": end.isoformat() if end else None,
        # A machine stopped by hand can't finish ECS draining and blocks scale-in for
        # good (2026-10-02, 27 h). Normal drains stay Healthy.
        "unhealthy_instances": sum(1 for i in g["Instances"] if i.get("HealthStatus") == "Unhealthy"),
    }
