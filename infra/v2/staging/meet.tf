# The meet service. Secrets and the control API arrive in the next PR; meet boots
# without them (its config is read per request) and serves /api/health.

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

resource "aws_ecs_task_definition" "meet" {
  family                   = "meetlab-v2-staging-meet"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name              = "meet"
    image             = "${aws_ecr_repository.this["meet"].repository_url}:${var.image_tag}"
    essential         = true
    cpu               = 512
    memoryReservation = 768
    portMappings      = [{ containerPort = 3000, hostPort = 0, protocol = "tcp" }]
    environment = [
      { name = "MEET_BASE_URL", value = "https://meet-staging.wwbp.org" },
    ]
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
  task_definition       = aws_ecs_task_definition.meet.arn
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
  depends_on = [aws_lb_listener.https, aws_ecs_cluster_capacity_providers.this]
}
