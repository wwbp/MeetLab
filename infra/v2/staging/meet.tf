# The meet service: the public app and console. It reaches agent-runner by name
# through Service Connect.

resource "aws_cloudwatch_log_group" "meet" {
  name              = "/meetlab-v2/staging/meet"
  retention_in_days = 30
}

resource "aws_lb_target_group" "meet" {
  name                 = "meetlab-v2-staging-meet"
  port                 = 3000
  protocol             = "HTTP"
  vpc_id               = aws_vpc.this.id
  target_type          = "instance"
  deregistration_delay = 30
  health_check {
    path    = "/api/health"
    matcher = "200"
  }
}

resource "aws_ecs_task_definition" "meet_app" {
  family                   = "meetlab-v2-staging-meet"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name              = "meet"
    image             = "${aws_ecr_repository.this["meet"].repository_url}:${var.image_tag}"
    essential         = true
    cpu               = 512 # peak ~315 units at 100 rooms joining at once (spike, 2026-10-05)
    memoryReservation = 512 # peak ~175 MB
    portMappings      = [{ containerPort = 3000, hostPort = 0, protocol = "tcp" }]
    environment = concat(local.livekit_environment, [
      { name = "MEET_BASE_URL", value = "https://meet-staging.wwbp.org" },
      { name = "BOT_RUNNER_URL", value = "http://agent-runner:7860/" },
    ])
    secrets = concat(local.livekit_secrets, [for n in ["BOT_RUNNER_SECRET", "CONSOLE_PASSWORD"] :
    { name = n, valueFrom = "${local.parameters}/${n}" }])
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.meet.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "meet"
      }
    }
  }])
}

resource "aws_ecs_service" "meet" {
  name                  = "meetlab-v2-staging-meet"
  cluster               = aws_ecs_cluster.this.id
  task_definition       = aws_ecs_task_definition.meet_app.arn
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
  load_balancer {
    target_group_arn = aws_lb_target_group.meet.arn
    container_name   = "meet"
    container_port   = 3000
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
  }
  depends_on = [aws_lb_listener.https, aws_ecs_cluster_capacity_providers.this]
}

# Revision 1 was recorded without skip_destroy, so replacing it would call
# DeregisterTaskDefinition, which CI must not hold. Forget it instead: it stays
# registered and unused. (Hence the meet_app address.)
removed {
  from = aws_ecs_task_definition.meet
  lifecycle {
    destroy = false
  }
}
