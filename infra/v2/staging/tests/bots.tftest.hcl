# Offline: mock provider, no AWS. Step 4c PR 1: one ECS task per meeting.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "the_bot_pool_starts_empty_on_a_small_instance" {
  command = apply

  assert {
    condition     = aws_autoscaling_group.bots.min_size == 0 && aws_launch_template.bots.instance_type == "c6i.large"
    error_message = "staging bots: 0 instances until a test needs one, on the smallest size that runs them (LEDGER)"
  }
  assert {
    condition     = aws_autoscaling_group.bots.protect_from_scale_in && one(aws_ecs_capacity_provider.bots.auto_scaling_group_provider).managed_termination_protection == "ENABLED"
    error_message = "scale-in must never kill a running meeting"
  }
  assert {
    condition     = one(aws_launch_template.bots.metadata_options).http_tokens == "required" && toset(aws_autoscaling_group.bots.vpc_zone_identifier) == toset([for s in aws_subnet.private : s.id])
    error_message = "bot instances: private subnets, IMDSv2"
  }
  assert {
    condition     = contains(aws_ecs_cluster_capacity_providers.this.capacity_providers, aws_ecs_capacity_provider.bots.name) && one(aws_ecs_cluster_capacity_providers.this.default_capacity_provider_strategy).capacity_provider == aws_ecs_capacity_provider.ec2.name
    error_message = "bots get their own pool; services stay on the default"
  }
}

run "a_bot_task_runs_one_session_and_gets_time_to_finish" {
  command = apply

  assert {
    condition     = jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].name == "bot" && jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].image == "${aws_ecr_repository.this["agent-runner"].repository_url}:0123abc"
    error_message = "container 'bot' (dispatch.py overrides it by name), same image as the runner"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].stopTimeout == 120
    error_message = "SIGTERM leaves 120 s (the ECS maximum) to flush audio and write ended"
  }
  assert {
    condition     = length(try(jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].portMappings, [])) == 0
    error_message = "a bot dials out to LiveKit; nothing dials in"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].secrets :
    strcontains(s.valueFrom, ":parameter/meetlab-v2/staging/") || startswith(s.valueFrom, aws_db_instance.this.master_user_secret[0].secret_arn)])
    error_message = "bot secrets come from staging parameters and the DB secret only"
  }
  assert {
    condition     = aws_ecs_task_definition.bot.skip_destroy
    error_message = "never deregister (see runner.tftest.hcl)"
  }
}

run "the_runner_can_start_and_stop_bot_tasks_and_nothing_else" {
  command = apply

  assert {
    condition     = aws_iam_role.runner_task.permissions_boundary == "arn:aws:iam::123456789012:policy/meetlab-v2-boundary" && aws_ecs_task_definition.runner_app.task_role_arn == aws_iam_role.runner_task.arn
    error_message = "the runner's task role carries the boundary"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.runner_dispatch.policy).Statement :
      alltrue([for r in flatten([s.Resource]) : anytrue([
        strcontains(r, ":task-definition/meetlab-v2-staging-bot:"),
        strcontains(r, ":task/meetlab-v2-staging/"),
        r == aws_iam_role.execution.arn,
    ])])])
    error_message = "RunTask only the bot family, Stop/Describe only this cluster's tasks, pass only the execution role"
  }
  assert {
    condition = contains([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : "${e.name}=${e.value}"],
    "BOT_DISPATCHER=ecs")
    error_message = "staging runs bots as tasks"
  }
}
