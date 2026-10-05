# Video recording on our own LiveKit (the user's decision, 2026-10-05): LiveKit's egress server
# (the version local development runs) records a room as an mp4 into the media bucket. It uploads
# with its own task role, which may only add recordings: no key exists (LiveKit Cloud still needs
# one, egress.tf). LiveKit hands it recordings through Redis.
#
# Off between studies (egress_count = 0, like the GPUs): a recording request needs an egress
# machine already up (LiveKit gives up after about a second), so switch it on before a study.
# ponytail: one recording per c6i.2xlarge (a room composite runs Chrome, cost 4 CPU at 80%);
# right-size from a measured recording when studies need many at once (LEDGER).

resource "aws_security_group" "redis" {
  name        = "meetlab-v2-staging-redis"
  description = "Redis for LiveKit and its egress"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.livekit.id, aws_security_group.egress.id]
  }
}

resource "aws_elasticache_subnet_group" "redis" {
  count      = var.livekit_self_hosted ? 1 : 0
  name       = "meetlab-v2-staging-redis"
  subnet_ids = [for s in aws_subnet.private : s.id]
}

resource "aws_elasticache_cluster" "redis" {
  count                = var.livekit_self_hosted ? 1 : 0
  cluster_id           = "meetlab-v2-staging-redis"
  engine               = "redis"
  engine_version       = "7.1"
  parameter_group_name = "default.redis7" # named: unnamed, AWS checks parametergroup:* (refused, bootstrap)
  node_type            = "cache.t4g.micro"
  num_cache_nodes      = 1
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.redis[0].name
  security_group_ids   = [aws_security_group.redis.id]
}

locals {
  redis_address = var.livekit_self_hosted ? "${aws_elasticache_cluster.redis[0].cache_nodes[0].address}:6379" : ""
}

resource "aws_security_group" "egress" {
  name        = "meetlab-v2-staging-egress"
  description = "LiveKit egress: outbound only (joins rooms, uploads to S3)"
  vpc_id      = aws_vpc.this.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_cloudwatch_log_group" "egress" {
  name              = "/meetlab-v2/staging/egress"
  retention_in_days = 30
}

resource "aws_iam_role" "egress_task" {
  name                 = "meetlab-v2-staging-egress-task"
  permissions_boundary = local.boundary
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "egress_recordings" {
  name = "add-recordings"
  role = aws_iam_role.egress_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
      Resource = "${aws_s3_bucket.media.arn}/recordings/*"
    }]
  })
}

resource "aws_ecs_task_definition" "egress" {
  family                   = "meetlab-v2-staging-egress"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.egress_task.arn
  container_definitions = jsonencode([{
    name              = "egress"
    image             = "livekit/egress:v1.13.0"
    essential         = true
    memoryReservation = 6144
    linuxParameters   = { capabilities = { add = ["SYS_ADMIN"], drop = [] } } # Chrome, per LiveKit's docs
    # With linuxParameters set, ECS stores these empty lists explicitly; declared here too, or
    # every plan shows a replacement and the deploy's drift check fails (2026-10-05, #186).
    environment    = []
    mountPoints    = []
    portMappings   = []
    systemControls = []
    volumesFrom    = []
    secrets = [
      { name = "KEY", valueFrom = "${local.parameters}/SELFHOSTED_LIVEKIT_API_KEY" },
      { name = "SECRET", valueFrom = "${local.parameters}/SELFHOSTED_LIVEKIT_API_SECRET" },
    ]
    # The config holds the key, so it is assembled here from the secrets, never in Terraform.
    # No S3 key: the upload uses the task role (AWS's default credentials).
    entryPoint = ["sh", "-c"]
    command = [join("", [
      "export EGRESS_CONFIG_BODY=\"$(printf 'api_key: %s\\napi_secret: %s\\nws_url: wss://${local.livekit_host}\\n",
      "redis:\\n  address: ${local.redis_address}\\nhealth_port: 8080\\n",
      "storage:\\n  s3:\\n    bucket: ${aws_s3_bucket.media.bucket}\\n    region: us-east-1\\n' \"$KEY\" \"$SECRET\")\"; ",
      "exec /entrypoint.sh", # the image's own start: PulseAudio (Chrome's audio), then egress
    ])]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.egress.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "egress"
      }
    }
  }])
}

resource "aws_launch_template" "egress" {
  name                   = "meetlab-v2-staging-egress"
  image_id               = data.aws_ssm_parameter.ecs_ami.value
  instance_type          = "c6i.2xlarge"
  vpc_security_group_ids = [aws_security_group.egress.id]
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
    tags          = { Name = "meetlab-v2-staging-egress", Project = "meetlab-v2", Environment = "staging" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meetlab-v2", Environment = "staging" }
  }
}

resource "aws_autoscaling_group" "egress" {
  name                  = "meetlab-v2-staging-egress"
  min_size              = 0
  max_size              = max(var.egress_count, 1)
  vpc_zone_identifier   = [for s in aws_subnet.private : s.id]
  protect_from_scale_in = true
  launch_template {
    id      = aws_launch_template.egress.id
    version = aws_launch_template.egress.latest_version
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

resource "aws_ecs_capacity_provider" "egress" {
  name = "meetlab-v2-staging-egress"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.egress.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }
  }
}

resource "aws_ecs_service" "egress" {
  count           = var.livekit_self_hosted ? 1 : 0
  name            = "meetlab-v2-staging-egress"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.egress.arn
  desired_count   = var.egress_count
  # A recording in progress is lost if its task stops: stop old tasks only once new ones run.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.egress.name
    weight            = 1
  }
  depends_on = [aws_ecs_cluster_capacity_providers.this]
}
