# The v1 -> v2 data copy task (migrate.tf, infra/migrate.sh) ran once, for the cutover
# (2026-10-14, MATCH: infra/LEDGER.md), and its code is gone (git history: tag v2.1.2 has it).
# Terraform forgets what it made and deletes nothing: task definitions stay registered (CI may
# not deregister them), and the log group's run logs expire with its 90-day retention.
removed {
  from = aws_ecs_task_definition.migrate
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_ecs_task_definition.migration
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_cloudwatch_log_group.migrate
  lifecycle {
    destroy = false
  }
}
