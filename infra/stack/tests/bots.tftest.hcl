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
    error_message = "staging bots: none kept warm (tests and studies use Prepare for study), on the smallest size that runs them (LEDGER)"
  }
  assert {
    condition     = contains([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : "${e.name}=${e.value}"], "BOT_POOL_MIN=0")
    error_message = "Prepare for study returns to the same baseline Terraform keeps"
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
    condition     = jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].name == "bot" && jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].image == "${data.aws_ecr_repository.this["agent-runner"].repository_url}:0123abc"
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
        r == aws_iam_role.bot_task.arn,
        # ListTasks takes no task resource; it is scoped by the cluster condition.
        r == "*" && try(s.Condition.ArnEquals["ecs:cluster"], "") == aws_ecs_cluster.this.arn,
    ])])])
    error_message = "RunTask only the bot family, Stop/Describe only this cluster's tasks, pass only the execution role"
  }
  assert {
    condition = contains([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : "${e.name}=${e.value}"],
    "BOT_DISPATCHER=ecs")
    error_message = "staging runs bots as tasks"
  }
}

# ECS Exec into bot tasks (debugging, and kill -9 for the heartbeat acceptance test).
# The bot's task role may open Session Manager channels and nothing else.
run "a_bot_task_can_be_exec_d_into_and_nothing_more" {
  command = apply

  assert {
    condition     = aws_ecs_task_definition.bot.task_role_arn == aws_iam_role.bot_task.arn && aws_iam_role.bot_task.permissions_boundary == "arn:aws:iam::123456789012:policy/meetlab-v2-boundary"
    error_message = "bot tasks get their own role, capped by the boundary"
  }
  assert {
    condition = toset(flatten([for s in jsondecode(aws_iam_role_policy.bot_exec.policy).Statement : s.Action])) == toset([
      "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
    ])
    error_message = "only the four Session Manager channel actions ECS Exec needs"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.runner_dispatch.policy).Statement :
      toset(flatten([s.Resource])) == toset([aws_iam_role.execution.arn, aws_iam_role.bot_task.arn])
    if contains(flatten([s.Action]), "iam:PassRole")])
    error_message = "to start a bot the runner passes its execution and task roles, and only those"
  }
}

# heartbeat.py stops a silent session's task by finding it (ListTasks startedBy).
# Without ListTasks the reconciler failed the session but could not stop the task:
# AccessDenied on staging, 2026-10-01. A hung bot would have kept running.
run "the_runner_can_find_a_sessions_task_in_this_cluster_only" {
  command = apply

  assert {
    condition = anytrue([for s in jsondecode(aws_iam_role_policy.runner_dispatch.policy).Statement :
      contains(flatten([s.Action]), "ecs:ListTasks") && try(s.Condition.ArnEquals["ecs:cluster"], "") == aws_ecs_cluster.this.arn
    ])
    error_message = "ListTasks, limited to this cluster"
  }
}

# Per-speaker audio (iteration 8a-2): the bot writes with its own role, the runner
# reads for downloads and transcripts. No S3 keys anywhere in either task.
run "recordings_go_to_s3_through_task_roles_not_keys" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.bot_recordings.policy).Statement[0].Action == "s3:PutObject" && jsondecode(aws_iam_role_policy.bot_recordings.policy).Statement[0].Resource == "${aws_s3_bucket.media.arn}/recordings/*"
    error_message = "the bot may only add files under recordings/"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.runner_recordings.policy).Statement[0].Action == "s3:GetObject" && jsondecode(aws_iam_role_policy.runner_recordings.policy).Statement[0].Resource == "${aws_s3_bucket.media.arn}/recordings/*"
    error_message = "the runner may only read files under recordings/"
  }
  assert {
    condition = alltrue([for td in [aws_ecs_task_definition.bot, aws_ecs_task_definition.runner_app] :
      contains([for e in jsondecode(td.container_definitions)[0].environment : "${e.name}=${e.value}"], "STORAGE_BACKEND=s3") &&
      contains([for e in jsondecode(td.container_definitions)[0].environment : "${e.name}=${e.value}"], "S3_BUCKET=${aws_s3_bucket.media.bucket}") &&
    !anytrue([for e in concat(jsondecode(td.container_definitions)[0].environment, jsondecode(td.container_definitions)[0].secrets) : startswith(e.name, "S3_KEY")])])
    error_message = "both tasks store to the media bucket, with no S3 keys"
  }
}

# Prepare for study (capacity.py): the runner raises the bot pool's minimum and
# schedules its return to 0. Only the bot pool: never the services or the GPU.
run "the_runner_can_prewarm_the_bot_pool_and_no_other" {
  command = apply

  assert {
    condition = (
      toset(flatten([for s in jsondecode(aws_iam_role_policy.runner_prewarm.policy).Statement : s.Action if flatten([s.Resource]) == ["arn:aws:autoscaling:us-east-1:123456789012:autoScalingGroup:*:autoScalingGroupName/meetlab-v2-staging-bots"]])) ==
      toset(["autoscaling:UpdateAutoScalingGroup", "autoscaling:PutScheduledUpdateGroupAction", "autoscaling:DeleteScheduledAction"])
    )
    error_message = "update and schedule the bot group itself"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.runner_prewarm.policy).Statement :
      flatten([s.Resource]) == ["arn:aws:autoscaling:us-east-1:123456789012:autoScalingGroup:*:autoScalingGroupName/meetlab-v2-staging-bots"] ||
      (flatten([s.Resource]) == ["*"] && alltrue([for a in flatten([s.Action]) : startswith(a, "autoscaling:Describe")])) ||
      (flatten([s.Action]) == ["ecs:ListContainerInstances"] && flatten([s.Resource]) == [aws_ecs_cluster.this.arn])
    ])
    error_message = "anything beyond the bot group is read-only"
  }
  assert {
    condition = anytrue([for s in jsondecode(aws_iam_role_policy.runner_prewarm.policy).Statement :
    flatten([s.Action]) == ["ecs:ListContainerInstances"] && flatten([s.Resource]) == [aws_ecs_cluster.this.arn]])
    error_message = "a machine is ready once ECS lists it in this cluster (capacity.py)"
  }
  assert {
    condition     = contains([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : "${e.name}=${e.value}"], "BOT_ASG_NAME=${aws_autoscaling_group.bots.name}")
    error_message = "the runner knows which group to warm"
  }
}

# Load-test readiness (L1): a bounded pool per bot, and a ceiling the ramp can raise.
run "each_bot_holds_at_most_two_database_connections" {
  command = apply

  assert {
    condition = (
      contains([for e in jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].environment : "${e.name}=${e.value}"], "DB_POOL_SIZE=2") &&
      contains([for e in jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].environment : "${e.name}=${e.value}"], "DB_MAX_OVERFLOW=0")
    )
    error_message = "100 bots on SQLAlchemy's default pool (up to 15 each) would exhaust db.t4g.small's ~180 connections"
  }
  assert {
    condition     = length([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : e if e.name == "DB_POOL_SIZE"]) == 0
    error_message = "the runner keeps the default pool: it serves every console request"
  }
}

run "the_bot_pool_ceiling_is_a_setting" {
  command = apply

  variables {
    bot_pool_max = 34
  }

  assert {
    condition     = aws_autoscaling_group.bots.max_size == 34
    error_message = "a load test raises the ceiling with bot_pool_max (default 2 keeps staging's cost capped)"
  }
}

# The image runs python directly, so without an init python is PID 1, which ignores a SIGKILL
# sent inside its container (kill9 hung, 2026-10-07) and reaps no zombies. ECS's own init fixes both.
run "an_init_is_pid_1_in_bot_and_runner_tasks" {
  command = apply

  assert {
    condition = alltrue([for td in [aws_ecs_task_definition.bot, aws_ecs_task_definition.runner_app] :
      try(jsondecode(td.container_definitions)[0].linuxParameters.initProcessEnabled, false)
    ])
    error_message = "bot and runner run under ECS's init (initProcessEnabled)"
  }
}
