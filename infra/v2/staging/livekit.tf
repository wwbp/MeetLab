# Self-hosted LiveKit (load-test readiness L2): our own livekit-server, the version local
# development runs, on one machine with a public IP. Signalling goes through the load
# balancer's TLS (wss://livekit-staging.wwbp.org); media goes straight to the machine,
# the standard LiveKit shape. Off unless livekit_self_hosted; LiveKit Cloud otherwise.
#
# ponytail: no TURN (participants behind strict firewalls can't connect) and no egress
# server (no video recording): fine for load tests. Add both before it serves a study.

locals {
  livekit_host = "livekit-staging.wwbp.org"
  # What meet, the runner and the bots connect with: ours when switched on, LiveKit Cloud otherwise.
  livekit_environment = var.livekit_self_hosted ? [{ name = "LIVEKIT_URL", value = "wss://${local.livekit_host}" }] : []
  livekit_secrets = var.livekit_self_hosted ? [
    { name = "LIVEKIT_API_KEY", valueFrom = "${local.parameters}/SELFHOSTED_LIVEKIT_API_KEY" },
    { name = "LIVEKIT_API_SECRET", valueFrom = "${local.parameters}/SELFHOSTED_LIVEKIT_API_SECRET" },
    ] : [for n in ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"] :
  { name = n, valueFrom = "${local.parameters}/${n}" }]
}

output "livekit" {
  description = "Which LiveKit the live tests use, and whether it records video"
  value = var.livekit_self_hosted ? {
    url = "wss://${local.livekit_host}", key_parameter = "SELFHOSTED_LIVEKIT_API_KEY", secret_parameter = "SELFHOSTED_LIVEKIT_API_SECRET", video = false
    } : {
    url = null, key_parameter = "LIVEKIT_API_KEY", secret_parameter = "LIVEKIT_API_SECRET", video = true
  }
}

resource "aws_security_group" "livekit" {
  name        = "meetlab-v2-staging-livekit"
  description = "Self-hosted LiveKit: media from anywhere, signalling via the load balancer"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 7880
    to_port         = 7880
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  ingress {
    from_port   = 7881
    to_port     = 7881
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 7882
    to_port     = 7882
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_launch_template" "livekit" {
  name          = "meetlab-v2-staging-livekit"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = "c6i.large"
  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.livekit.id]
  }
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
    tags          = { Name = "meetlab-v2-staging-livekit", Project = "meetlab-v2", Environment = "staging" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meetlab-v2", Environment = "staging" }
  }
}

resource "aws_autoscaling_group" "livekit" {
  name                  = "meetlab-v2-staging-livekit"
  min_size              = 0
  max_size              = 1
  vpc_zone_identifier   = [for s in aws_subnet.public : s.id]
  protect_from_scale_in = true
  launch_template {
    id      = aws_launch_template.livekit.id
    version = aws_launch_template.livekit.latest_version
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

resource "aws_ecs_capacity_provider" "livekit" {
  name = "meetlab-v2-staging-livekit"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.livekit.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }
  }
}

resource "aws_cloudwatch_log_group" "livekit" {
  name              = "/meetlab-v2/staging/livekit"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "livekit" {
  family                   = "meetlab-v2-staging-livekit"
  requires_compatibilities = ["EC2"]
  network_mode             = "host" # WebRTC media on the machine's own ports
  skip_destroy             = true   # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name              = "livekit"
    image             = "livekit/livekit-server:v1.12.0"
    essential         = true
    memoryReservation = 1024
    portMappings = [
      { containerPort = 7880, hostPort = 7880, protocol = "tcp" },
      { containerPort = 7881, hostPort = 7881, protocol = "tcp" },
      { containerPort = 7882, hostPort = 7882, protocol = "udp" },
    ]
    secrets = [
      { name = "KEY", valueFrom = "${local.parameters}/SELFHOSTED_LIVEKIT_API_KEY" },
      { name = "SECRET", valueFrom = "${local.parameters}/SELFHOSTED_LIVEKIT_API_SECRET" },
    ]
    environment = [{ name = "WEBHOOK", value = "https://meet-staging.wwbp.org/api/concierge/webhooks/livekit" }]
    # The config holds the key, so it is assembled here from the secrets, never in Terraform.
    entryPoint = ["sh", "-c"]
    command = [join("", [
      "export LIVEKIT_CONFIG=\"$(printf 'port: 7880\\nrtc:\\n  tcp_port: 7881\\n  udp_port: 7882\\n  use_external_ip: true\\n",
      "keys:\\n  %s: %s\\nwebhook:\\n  api_key: %s\\n  urls: [%s]\\n' \"$KEY\" \"$SECRET\" \"$KEY\" \"$WEBHOOK\")\"; ",
      "exec /livekit-server",
    ])]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.livekit.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "livekit"
      }
    }
  }])
}

resource "aws_lb_target_group" "livekit" {
  count       = var.livekit_self_hosted ? 1 : 0
  name        = "meetlab-v2-staging-livekit"
  port        = 7880
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = aws_vpc.this.id
  health_check {
    path = "/" # livekit-server answers "OK"
  }
}

resource "aws_lb_listener_rule" "livekit" {
  count        = var.livekit_self_hosted ? 1 : 0
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.livekit[0].arn
  }
  condition {
    host_header {
      values = [local.livekit_host]
    }
  }
}

resource "aws_route53_record" "livekit" {
  count   = var.livekit_self_hosted ? 1 : 0
  zone_id = data.aws_route53_zone.wwbp.zone_id
  name    = local.livekit_host
  type    = "A"
  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}

resource "aws_ecs_service" "livekit" {
  count           = var.livekit_self_hosted ? 1 : 0
  name            = "meetlab-v2-staging-livekit"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.livekit.arn
  desired_count   = 1
  # One machine, host ports (max_size 1): stop the old task first (see models.tf).
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.livekit.name
    weight            = 1
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.livekit[0].arn
    container_name   = "livekit"
    container_port   = 7880
  }
  depends_on = [aws_ecs_cluster_capacity_providers.this, aws_lb_listener_rule.livekit]
}
