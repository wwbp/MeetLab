# Our own models (load-test readiness L3): an open LLM on vLLM and Kokoro speech on
# Kokoro-FastAPI, both speaking OpenAI's API, each its own GPU service. Off unless named
# in model_services; then bots learn their addresses and a room's config chooses them
# (bot.py _build_llm, _build_tts). Same shape as the STT NIM (stt_nim.tf): bots are
# RunTask tasks without Service Connect, so each sits behind an internal network load balancer.
#
# ponytail: g6.xlarge on-demand, 0 to 1 each, weights downloaded on every cold start
# (~15 GB for Qwen, minutes). Fine for tests; size and cache them from the load tests.

locals {
  models = {
    llm = {
      image  = "vllm/vllm-openai:v0.30.0"
      model  = "Qwen/Qwen2.5-7B-Instruct" # Apache-2.0, no token to download
      port   = 8000
      health = "/health"
      # Weights quantised to 8 bits as they load (~8 GB of the L4's 24 GB; the rest is KV
      # cache for concurrent rooms). Generation speed is memory-bound: an L4's 300 GB/s caps
      # 16-bit 7B near 20 tokens/s, and the bot waits for a whole first sentence (LEDGER).
      command = ["--model", "Qwen/Qwen2.5-7B-Instruct", "--quantization", "fp8", "--max-model-len", "8192", "--gpu-memory-utilization", "0.90"]
      memory  = 8192
      cache   = "/root/.cache/huggingface" # the weights, kept across restarts (see the task definition)
    }
    tts = {
      image   = "ghcr.io/remsky/kokoro-fastapi-gpu:v0.9.0"
      model   = "kokoro"
      port    = 8880
      health  = "/health"
      command = null
      memory  = 4096
      cache   = null # the image carries the model
    }
  }
  running_models = { for k, m in local.models : k => m if contains(local.model_services, k) }
  model_environment = concat(
    contains(local.model_services, "llm") ? [
      { name = "SELFHOSTED_LLM_URL", value = "http://${aws_lb.model["llm"].dns_name}:8000/v1" },
      { name = "SELFHOSTED_LLM_MODEL", value = local.models.llm.model },
    ] : [],
    contains(local.model_services, "tts") ? [
      { name = "KOKORO_TTS_URL", value = "http://${aws_lb.model["tts"].dns_name}:8880/v1" },
    ] : [],
  )
}

resource "aws_security_group" "model_lb" {
  for_each    = local.models
  name        = "${local.name}-${each.key}-lb"
  description = "${each.key} load balancer: app tasks in, the model out"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = each.value.port
    to_port         = each.value.port
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  egress {
    from_port   = each.value.port
    to_port     = each.value.port
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }
}

resource "aws_security_group" "model" {
  for_each    = local.models
  name        = "${local.name}-${each.key}"
  description = "${each.key} instances: the load balancer in; out for the image and weights"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = each.value.port
    to_port         = each.value.port
    protocol        = "tcp"
    security_groups = [aws_security_group.model_lb[each.key].id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_launch_template" "model" {
  for_each               = local.models
  name                   = "${local.name}-${each.key}"
  image_id               = data.aws_ssm_parameter.ecs_gpu_ami.value
  instance_type          = "g6.xlarge"
  vpc_security_group_ids = [aws_security_group.model[each.key].id]
  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 100 # image + weights
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
    tags          = { Name = "${local.name}-${each.key}", Project = "meetlab-v2", Environment = var.env }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meetlab-v2", Environment = var.env }
  }
}

resource "aws_autoscaling_group" "model" {
  for_each              = local.models
  name                  = "${local.name}-${each.key}"
  min_size              = 0
  max_size              = each.key == "tts" ? local.tts_replicas : 1 # one task per machine (fixed host port)
  vpc_zone_identifier   = [for s in aws_subnet.private : s.id]
  protect_from_scale_in = true
  launch_template {
    id      = aws_launch_template.model[each.key].id
    version = aws_launch_template.model[each.key].latest_version
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

resource "aws_ecs_capacity_provider" "model" {
  for_each = local.models
  name     = "${local.name}-${each.key}"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.model[each.key].arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }
  }
}

resource "aws_cloudwatch_log_group" "model" {
  for_each          = local.models
  name              = "/meetlab-v2/${var.env}/${each.key}"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "model" {
  for_each                 = local.models
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  # Deploys stop the old task first (the service below), so every change is a restart: the
  # weights come back from this machine's disk, not a 15 GB download. A Docker volume ECS
  # creates on first use and keeps after the task stops; a replaced machine starts cold.
  dynamic "volume" {
    for_each = each.value.cache == null ? [] : [each.key]
    content {
      name = "${each.key}-cache"
      docker_volume_configuration {
        scope         = "shared"
        autoprovision = true
        driver        = "local"
      }
    }
  }
  container_definitions = jsonencode([{
    name                 = each.key
    mountPoints          = each.value.cache == null ? [] : [{ sourceVolume = "${each.key}-cache", containerPath = each.value.cache }]
    image                = each.value.image
    essential            = true
    memoryReservation    = each.value.memory
    resourceRequirements = [{ type = "GPU", value = "1" }]
    command              = each.value.command
    portMappings         = [{ containerPort = each.value.port, hostPort = each.value.port, protocol = "tcp" }]
    linuxParameters      = { sharedMemorySize = 2048 }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.model[each.key].name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = each.key
      }
    }
  }])
}

resource "aws_lb" "model" {
  for_each           = local.running_models
  name               = "${local.name}-${each.key}"
  internal           = true
  load_balancer_type = "network"
  subnets            = [for s in aws_subnet.private : s.id]
  security_groups    = [aws_security_group.model_lb[each.key].id]
}

resource "aws_lb_target_group" "model" {
  for_each           = local.running_models
  name               = "${local.name}-${each.key}"
  port               = each.value.port
  protocol           = "TCP"
  target_type        = "instance"
  vpc_id             = aws_vpc.this.id
  preserve_client_ip = false # the model's security group trusts the load balancer's
  health_check {
    protocol = "HTTP"
    path     = each.value.health
  }
}

resource "aws_lb_listener" "model" {
  for_each          = local.running_models
  load_balancer_arn = aws_lb.model[each.key].arn
  port              = each.value.port
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.model[each.key].arn
  }
}

resource "aws_ecs_service" "model" {
  for_each        = local.running_models
  name            = "${local.name}-${each.key}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.model[each.key].arn
  desired_count   = each.key == "tts" ? local.tts_replicas : 1
  # One machine (max_size 1): stop the old task first, or the new one can never be placed
  # and the deploy waits forever (FP8, 2026-10-03).
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  availability_zone_rebalancing      = "DISABLED" # refused with maximum 100%; one task has nothing to balance
  deployment_circuit_breaker {
    enable   = true # a deploy that cannot start ends and rolls back (3 failures)
    rollback = true
  }
  health_check_grace_period_seconds = 1800  # cold start: image and weights download
  wait_for_steady_state             = false # the live test waits for a ready model instead
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.model[each.key].name
    weight            = 1
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.model[each.key].arn
    container_name   = each.key
    container_port   = each.value.port
  }
  depends_on = [aws_ecs_cluster_capacity_providers.this, aws_lb_listener.model]
}
