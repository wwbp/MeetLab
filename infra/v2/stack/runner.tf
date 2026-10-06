# agent-runner: the control API and console backend. Private: only meet reaches it,
# by name, through Service Connect. It starts each meeting's bot as an ECS task
# (bots.tf); it no longer runs bots itself.

# Shared by the runner and the bot task: the bot code runs in both images' processes.
locals {
  bot_environment = concat(local.livekit_environment, [
    { name = "DB_HOST", value = aws_db_instance.this.address },
    { name = "DB_NAME", value = aws_db_instance.this.db_name },
    { name = "DB_USER", value = aws_db_instance.this.username },
    # Per-speaker audio goes to the media bucket through each task's own role (no S3
    # keys: storage.py falls back to the default credential chain). Video egress
    # needs a key LiveKit can use; that is 8b.
    { name = "STORAGE_BACKEND", value = "s3" },
    { name = "S3_BUCKET", value = aws_s3_bucket.media.bucket },
    { name = "S3_REGION", value = "us-east-1" },
    ], local.stt_nim_enabled ? [
    # Staging's own Parakeet NIM (stt_nim.tf), as v1 runs.
    { name = "NEMOTRON_STT_URL", value = "http://${aws_lb.stt_nim[0].dns_name}:9000" },
    { name = "STT_MODEL_OVERRIDE", value = "parakeet-tdt-0.6b-v2" },
    ] : [
    # NIM off: Deepgram, so a staging bot never needs a GPU running.
    { name = "STT_MODEL_OVERRIDE", value = "nova-3-general" },
  ], local.model_environment) # our LLM and voice, when running (models.tf)
  bot_secrets = concat(
    [for n in ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY", "BOT_RUNNER_SECRET", "CONSOLE_PASSWORD"] :
    { name = n, valueFrom = "${local.parameters}/${n}" }],
    local.livekit_secrets, # LiveKit Cloud, or ours (livekit.tf)
    [{ name = "DB_PASSWORD", valueFrom = "${local.db_secret}:password::" }],
  )
}

resource "aws_cloudwatch_log_group" "runner" {
  name              = "/meetlab-v2/${var.env}/agent-runner"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "runner_app" {
  family                   = "${local.name}-agent-runner"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.runner_task.arn
  container_definitions = jsonencode([{
    name              = "agent-runner"
    image             = "${data.aws_ecr_repository.this["agent-runner"].repository_url}:${var.image_tag}"
    essential         = true
    cpu               = 1024
    memoryReservation = 1536
    portMappings      = [{ name = "http", containerPort = 7860, hostPort = 0, protocol = "tcp", appProtocol = "http" }]
    environment = concat(local.bot_environment, [
      # Step 4c: each meeting's bot is its own ECS task (dispatch.py), not a coroutine here.
      { name = "BOT_DISPATCHER", value = "ecs" },
      { name = "ECS_CLUSTER", value = aws_ecs_cluster.this.name },
      { name = "BOT_TASK_DEFINITION", value = aws_ecs_task_definition.bot.family },
      { name = "BOT_CAPACITY_PROVIDER", value = aws_ecs_capacity_provider.bots.name },
      { name = "BOT_ASG_NAME", value = aws_autoscaling_group.bots.name }, # Prepare for study
      { name = "BOT_POOL_MIN", value = tostring(local.bot_pool_min) },
      # heartbeat.py: a session silent for 30 s is failed; look every 10 s.
      { name = "CONVERSATION_RECONCILE_INTERVAL_SECONDS", value = "10" },
    ])
    # The runner requests video egress. LiveKit Cloud gets the write-only egress key with
    # each request (egress.tf); our own egress uploads with its task role, so then no key at
    # all (egress_server.tf). Bots never hold it. Minted by a person into SSM:
    # docs/v2-deployment.md.
    secrets = concat(local.bot_secrets, var.livekit_self_hosted ? [] : [for n in ["EGRESS_S3_KEY_ID", "EGRESS_S3_KEY_SECRET"] :
    { name = n, valueFrom = "${local.parameters}/${n}" }])
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:7860/health')\""]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 120 # migrations run before the server starts
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.runner.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "agent-runner"
      }
    }
  }])
}

resource "aws_ecs_service" "runner" {
  name                  = "${local.name}-agent-runner"
  cluster               = aws_ecs_cluster.this.id
  task_definition       = aws_ecs_task_definition.runner_app.arn
  desired_count         = 1
  wait_for_steady_state = true
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.ec2.name
    weight            = 1
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
    service {
      port_name      = "http"
      discovery_name = "agent-runner"
      client_alias {
        port     = 7860
        dns_name = "agent-runner"
      }
    }
  }
  depends_on = [aws_ecs_cluster_capacity_providers.this]
}

# Same as meet's (meet.tf): the first revision's state predates skip_destroy.
removed {
  from = aws_ecs_task_definition.runner
  lifecycle {
    destroy = false
  }
}
