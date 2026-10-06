# Speech-to-text on CPU (the stt-cpu image, stt-cpu/app.py): Parakeet TDT 0.6B v2, int8,
# behind the same endpoint as the GPU NIM, so bots use either unchanged (runner.tf). No GPU and
# no NVIDIA licence; the model is in the image, so a machine is ready in about a minute.
# Production keeps it on for unscheduled sessions; a study past the measured switch point turns
# the GPU on instead (docs/load-testing.md). Bots reach it through an internal load balancer,
# as for the NIM (one-off tasks can't use Service Connect).

resource "aws_security_group" "stt_cpu_lb" {
  name        = "${local.name}-stt-cpu-lb"
  description = "CPU speech-to-text load balancer: app tasks in, the server out"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  egress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }
}

resource "aws_security_group" "stt_cpu" {
  name        = "${local.name}-stt-cpu"
  description = "CPU speech-to-text machines: the load balancer in; out for the image"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.stt_cpu_lb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_launch_template" "stt_cpu" {
  name                   = "${local.name}-stt-cpu"
  image_id               = data.aws_ssm_parameter.ecs_ami.value
  instance_type          = local.stt_cpu_instance_type
  vpc_security_group_ids = [aws_security_group.stt_cpu.id]
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
    tags          = { Name = "${local.name}-stt-cpu", Project = "meetlab-v2", Environment = var.env }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meetlab-v2", Environment = var.env }
  }
}

resource "aws_autoscaling_group" "stt_cpu" {
  name                  = "${local.name}-stt-cpu"
  min_size              = 0
  max_size              = 1
  vpc_zone_identifier   = [for s in aws_subnet.private : s.id]
  protect_from_scale_in = true
  launch_template {
    id      = aws_launch_template.stt_cpu.id
    version = aws_launch_template.stt_cpu.latest_version
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

resource "aws_ecs_capacity_provider" "stt_cpu" {
  name = "${local.name}-stt-cpu"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.stt_cpu.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }
  }
}

resource "aws_cloudwatch_log_group" "stt_cpu" {
  name              = "/meetlab-v2/${var.env}/stt-cpu"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "stt_cpu" {
  family                   = "${local.name}-stt-cpu"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name              = "stt-cpu"
    image             = "${data.aws_ecr_repository.this["stt-cpu"].repository_url}:${var.image_tag}"
    essential         = true
    memoryReservation = 2048 # the int8 model is ~650 MB; sized by the switch-point run
    portMappings      = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.stt_cpu.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "stt-cpu"
      }
    }
  }])
}

resource "aws_lb" "stt_cpu" {
  count              = local.stt_cpu_enabled ? 1 : 0
  name               = "${local.name}-stt-cpu"
  internal           = true
  load_balancer_type = "network"
  subnets            = [for s in aws_subnet.private : s.id]
  security_groups    = [aws_security_group.stt_cpu_lb.id]
}

resource "aws_lb_target_group" "stt_cpu" {
  count              = local.stt_cpu_enabled ? 1 : 0
  name               = "${local.name}-stt-cpu"
  port               = 8000
  protocol           = "TCP"
  target_type        = "instance"
  vpc_id             = aws_vpc.this.id
  preserve_client_ip = false # the server's security group trusts the load balancer's
  health_check {
    protocol = "HTTP"
    path     = "/health"
  }
}

resource "aws_lb_listener" "stt_cpu" {
  count             = local.stt_cpu_enabled ? 1 : 0
  load_balancer_arn = aws_lb.stt_cpu[0].arn
  port              = 8000
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.stt_cpu[0].arn
  }
}

resource "aws_ecs_service" "stt_cpu" {
  count                              = local.stt_cpu_enabled ? 1 : 0
  name                               = "${local.name}-stt-cpu"
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.stt_cpu.arn
  desired_count                      = 1
  deployment_minimum_healthy_percent = 0 # one machine: stop the old task first (as the NIM)
  deployment_maximum_percent         = 100
  availability_zone_rebalancing      = "DISABLED"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  health_check_grace_period_seconds = 300
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.stt_cpu.name
    weight            = 1
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.stt_cpu[0].arn
    container_name   = "stt-cpu"
    container_port   = 8000
  }
  depends_on = [aws_ecs_cluster_capacity_providers.this, aws_lb_listener.stt_cpu]
}
