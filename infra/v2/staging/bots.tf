# The bot pool (step 4c): one ECS task per meeting, on its own capacity provider so
# bots scale 0..N while meet and the runner stay up. agent-runner starts tasks with
# RunTask (dispatch.py); a task exits when its meeting ends.
#
# ponytail: c6i.large, 0 to 2 instances. Staging sizing; right-size from measured
# per-session load when load testing resumes (LEDGER).

resource "aws_cloudwatch_log_group" "bot" {
  name              = "/meetlab-v2/staging/bot"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "bot" {
  family                   = "meetlab-v2-staging-bot"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.bot_task.arn
  container_definitions = jsonencode([{
    name              = "bot"
    image             = "${aws_ecr_repository.this["agent-runner"].repository_url}:${var.image_tag}"
    essential         = true
    cpu               = 512
    memoryReservation = 1024
    stopTimeout       = 120 # the ECS maximum: SIGTERM, then this long to flush and write ended
    environment       = local.bot_environment
    secrets           = local.bot_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.bot.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "bot"
      }
    }
  }])
}

resource "aws_launch_template" "bots" {
  name                   = "meetlab-v2-staging-bots"
  image_id               = data.aws_ssm_parameter.ecs_ami.value
  instance_type          = "c6i.large"
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  user_data = base64encode("#!/bin/bash\necho ECS_CLUSTER=${aws_ecs_cluster.this.name} >> /etc/ecs/ecs.config\n")
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "meetlab-v2-staging-bots", Project = "meetlab-v2", Environment = "staging" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meetlab-v2", Environment = "staging" }
  }
}

resource "aws_autoscaling_group" "bots" {
  name                  = "meetlab-v2-staging-bots"
  min_size              = 0
  max_size              = 2
  vpc_zone_identifier   = [for s in aws_subnet.private : s.id]
  protect_from_scale_in = true
  launch_template {
    id      = aws_launch_template.bots.id
    version = aws_launch_template.bots.latest_version
  }
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }
  tag {
    key                 = "Project"
    value               = "meetlab-v2"
    propagate_at_launch = true
  }
  lifecycle {
    # ECS managed scaling owns the desired count; "Prepare for study" (capacity.py)
    # owns the minimum, so a deploy mid-study doesn't reset a warm pool.
    ignore_changes = [desired_capacity, min_size]
  }
}

resource "aws_ecs_capacity_provider" "bots" {
  name = "meetlab-v2-staging-bots"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.bots.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }
  }
}

# The bot's own role: only the Session Manager channels ECS Exec needs, so a person
# can open a shell in a staging bot (and kill -9 it for the heartbeat test).
resource "aws_iam_role" "bot_task" {
  name                 = "meetlab-v2-staging-bot-task"
  permissions_boundary = local.boundary
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "bot_exec" {
  name = "ecs-exec"
  role = aws_iam_role.bot_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
      Resource = "*"
    }]
  })
}

# Per-speaker audio: the bot only adds files under recordings/ (it cannot read, list
# or delete them); the runner only reads them, for downloads and transcripts.
resource "aws_iam_role_policy" "bot_recordings" {
  name = "write-recordings"
  role = aws_iam_role.bot_task.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "s3:PutObject", Resource = "${aws_s3_bucket.media.arn}/recordings/*" }]
  })
}

resource "aws_iam_role_policy" "runner_recordings" {
  name = "read-recordings"
  role = aws_iam_role.runner_task.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "s3:GetObject", Resource = "${aws_s3_bucket.media.arn}/recordings/*" }]
  })
}

# Prepare for study (capacity.py): the runner raises the bot pool's minimum and
# schedules its return to 0. This group only; the rest of autoscaling is read-only.
resource "aws_iam_role_policy" "runner_prewarm" {
  name = "prewarm-bots"
  role = aws_iam_role.runner_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["autoscaling:UpdateAutoScalingGroup", "autoscaling:PutScheduledUpdateGroupAction", "autoscaling:DeleteScheduledAction"]
        # By name: the ARN's middle is a random ID (and the permission contract can't know it).
        Resource = "arn:aws:autoscaling:us-east-1:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/${aws_autoscaling_group.bots.name}"
      },
      { Effect = "Allow", Action = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:DescribeScheduledActions"], Resource = "*" },
    ]
  })
}

# agent-runner may start bot tasks, and stop or inspect tasks in this cluster.
resource "aws_iam_role" "runner_task" {
  name                 = "meetlab-v2-staging-runner-task"
  permissions_boundary = local.boundary
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "runner_dispatch" {
  name = "dispatch-bots"
  role = aws_iam_role.runner_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ecs:RunTask", "ecs:TagResource"]
        Resource  = "arn:aws:ecs:us-east-1:${data.aws_caller_identity.current.account_id}:task-definition/${aws_ecs_task_definition.bot.family}:*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      {
        # heartbeat.py finds a silent session's task by startedBy before stopping it.
        Effect    = "Allow"
        Action    = "ecs:ListTasks"
        Resource  = "*"
        Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:StopTask", "ecs:DescribeTasks", "ecs:TagResource"]
        Resource = "arn:aws:ecs:us-east-1:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.this.name}/*"
      },
      { Effect = "Allow", Action = "iam:PassRole", Resource = [aws_iam_role.execution.arn, aws_iam_role.bot_task.arn] },
    ]
  })
}
