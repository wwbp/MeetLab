# agent-runner: the control API, console backend and (for now) the bots, which still
# run inside it. Private: only meet reaches it, by name, through Service Connect.
# Per-session bot tasks replace the in-process bots in a later PR.

resource "aws_cloudwatch_log_group" "runner" {
  name              = "/meetlab-v2/staging/agent-runner"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "runner" {
  family                   = "meetlab-v2-staging-agent-runner"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name              = "agent-runner"
    image             = "${aws_ecr_repository.this["agent-runner"].repository_url}:${var.image_tag}"
    essential         = true
    cpu               = 1024
    memoryReservation = 1536
    portMappings      = [{ name = "http", containerPort = 7860, hostPort = 0, protocol = "tcp", appProtocol = "http" }]
    environment = [
      { name = "DB_HOST", value = aws_db_instance.this.address },
      { name = "DB_NAME", value = aws_db_instance.this.db_name },
      { name = "DB_USER", value = aws_db_instance.this.username },
      # ponytail: recordings stay on the container disk (lost on restart) until the
      # bot-pool PR gives tasks a role for the media bucket.
      { name = "STORAGE_BACKEND", value = "local" },
    ]
    secrets = concat(
      [for n in ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "BOT_RUNNER_SECRET", "CONSOLE_PASSWORD"] :
      { name = n, valueFrom = "${local.parameters}/${n}" }],
      [{ name = "DB_PASSWORD", valueFrom = "${local.db_secret}:password::" }],
    )
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
  name                  = "meetlab-v2-staging-agent-runner"
  cluster               = aws_ecs_cluster.this.id
  task_definition       = aws_ecs_task_definition.runner.arn
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
