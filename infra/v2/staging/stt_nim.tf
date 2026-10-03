# Staging's own Parakeet NIM: the GPU speech-to-text v1 runs on a g6 (infra/stt-nim),
# here as an ECS service on an on-demand GPU group. Off unless stt_nim_enabled: then bots
# transcribe with it instead of Deepgram (runner.tf).
#
# Bots are one-off RunTask tasks, which can't use Service Connect, so they reach the
# NIM through an internal network load balancer: a stable address, created only
# while the NIM runs.
#
# ponytail: g6.xlarge on-demand, 0 to 1, model cache on the instance disk. Every cold start
# rebuilds the model (~20 min, v1 docs). Fine for test runs; before a study, size it
# from load tests and keep the cache (EFS or a warm instance).

locals {
  stt_nim_image = "nvcr.io/nim/nvidia/parakeet-0.6b-tdt:latest" # same as v1
  # Stored by a person (docs/v2-deployment.md): {"username":"$oauthtoken","password":"<NGC key>"}.
  ngc_secret = "arn:aws:secretsmanager:us-east-1:${data.aws_caller_identity.current.account_id}:secret:meetlab-v2/staging/ngc"
}

data "aws_ssm_parameter" "ecs_gpu_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended/image_id"
}

resource "aws_security_group" "stt_nim_lb" {
  name        = "meetlab-v2-staging-stt-nim-lb"
  description = "STT NIM load balancer: app tasks in, the NIM out"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 9000
    to_port         = 9000
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  egress {
    from_port   = 9000
    to_port     = 9000
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }
}

resource "aws_security_group" "stt_nim" {
  name        = "meetlab-v2-staging-stt-nim"
  description = "STT NIM instances: the load balancer in; out for the NGC image and model"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 9000
    to_port         = 9000
    protocol        = "tcp"
    security_groups = [aws_security_group.stt_nim_lb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_launch_template" "stt_nim" {
  name                   = "meetlab-v2-staging-stt-nim"
  image_id               = data.aws_ssm_parameter.ecs_gpu_ami.value
  instance_type          = "g6.xlarge" # smallest GPU that fits Parakeet (LEDGER)
  vpc_security_group_ids = [aws_security_group.stt_nim.id]
  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 100 # NIM image + model cache
      volume_type           = "gp3"
      delete_on_termination = true
    }
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  user_data = base64encode("#!/bin/bash\necho ECS_CLUSTER=${aws_ecs_cluster.this.name} >> /etc/ecs/ecs.config\n")
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "meetlab-v2-staging-stt-nim", Project = "meetlab-v2", Environment = "staging" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meetlab-v2", Environment = "staging" }
  }
}

resource "aws_autoscaling_group" "stt_nim" {
  name                  = "meetlab-v2-staging-stt-nim"
  min_size              = 0
  max_size              = 1
  vpc_zone_identifier   = [for s in aws_subnet.private : s.id]
  protect_from_scale_in = true
  launch_template {
    id      = aws_launch_template.stt_nim.id
    version = aws_launch_template.stt_nim.latest_version
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
    ignore_changes = [desired_capacity] # ECS managed scaling owns it
  }
}

resource "aws_ecs_capacity_provider" "stt_nim" {
  name = "meetlab-v2-staging-stt-nim"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.stt_nim.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }
  }
}

resource "aws_cloudwatch_log_group" "stt_nim" {
  name              = "/meetlab-v2/staging/stt-nim"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "stt_nim" {
  family                   = "meetlab-v2-staging-stt-nim"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  # The built model stays on this machine across restarts (deploys stop the old task
  # first): a Docker volume filled from the image's own cache directory, so the NIM's user
  # can write it (NVIDIA: the cache must be writable). Without it, every restart rebuilds
  # the model (~20 min; v1 never kept it either).
  volume {
    name = "stt-nim-cache"
    docker_volume_configuration {
      scope         = "shared"
      autoprovision = true
      driver        = "local"
    }
  }
  container_definitions = jsonencode([{
    name                  = "stt-nim"
    mountPoints           = [{ sourceVolume = "stt-nim-cache", containerPath = "/opt/nim/.cache" }]
    image                 = local.stt_nim_image
    essential             = true
    memoryReservation     = 8192
    resourceRequirements  = [{ type = "GPU", value = "1" }]
    repositoryCredentials = { credentialsParameter = local.ngc_secret }
    portMappings          = [{ containerPort = 9000, hostPort = 9000, protocol = "tcp" }]
    environment           = [{ name = "NIM_HTTP_API_PORT", value = "9000" }]
    secrets               = [{ name = "NGC_API_KEY", valueFrom = "${local.ngc_secret}:password::" }]
    linuxParameters       = { sharedMemorySize = 8192 }
    ulimits               = [{ name = "nofile", softLimit = 2048, hardLimit = 2048 }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.stt_nim.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "stt-nim"
      }
    }
  }])
}

resource "aws_iam_role_policy" "execution_ngc" {
  name = "ngc-secret"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = "${local.ngc_secret}-*" }]
  })
}

resource "aws_lb" "stt_nim" {
  count              = var.stt_nim_enabled ? 1 : 0
  name               = "meetlab-v2-staging-stt-nim"
  internal           = true
  load_balancer_type = "network"
  subnets            = [for s in aws_subnet.private : s.id]
  security_groups    = [aws_security_group.stt_nim_lb.id]
}

resource "aws_lb_target_group" "stt_nim" {
  count              = var.stt_nim_enabled ? 1 : 0
  name               = "meetlab-v2-staging-stt-nim"
  port               = 9000
  protocol           = "TCP"
  target_type        = "instance"
  vpc_id             = aws_vpc.this.id
  preserve_client_ip = false # the NIM's security group trusts the load balancer's
  health_check {
    protocol = "HTTP"
    path     = "/v1/health/ready"
  }
}

resource "aws_lb_listener" "stt_nim" {
  count             = var.stt_nim_enabled ? 1 : 0
  load_balancer_arn = aws_lb.stt_nim[0].arn
  port              = 9000
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.stt_nim[0].arn
  }
}

resource "aws_ecs_service" "stt_nim" {
  count           = var.stt_nim_enabled ? 1 : 0
  name            = "meetlab-v2-staging-stt-nim"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.stt_nim.arn
  desired_count   = 1
  # One machine (max_size 1): stop the old task first, or the new one can never be placed
  # and the deploy waits forever (FP8, 2026-10-03).
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  availability_zone_rebalancing      = "DISABLED" # refused with maximum 100%; one task has nothing to balance
  deployment_circuit_breaker {
    enable   = true # a deploy that cannot start ends and rolls back (3 failures)
    rollback = true
  }
  health_check_grace_period_seconds = 2700 # first boot builds the model (~20 min, v1)
  # ponytail: the apply doesn't wait for a ready NIM (up to ~30 min cold); the stt_nim
  # acceptance scenario waits for it instead.
  wait_for_steady_state = false
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.stt_nim.name
    weight            = 1
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.stt_nim[0].arn
    container_name   = "stt-nim"
    container_port   = 9000
  }
  depends_on = [aws_ecs_cluster_capacity_providers.this, aws_lb_listener.stt_nim]
}
