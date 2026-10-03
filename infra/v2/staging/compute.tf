# ECS on EC2. One capacity provider for the always-on services (meet, control API);
# the bot pool gets its own in a later PR, sized for per-session tasks.
#
# Bridge networking, not awsvpc: awsvpc gives each task an ENI, and a c6i.xlarge has
# 4, which would cap bots at 3 per instance.

data "aws_caller_identity" "current" {}

locals {
  # Created by infra/v2/bootstrap. CI can only create roles that carry it.
  boundary = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/meetlab-v2-boundary"
}

resource "aws_ecs_cluster" "this" {
  name = "meetlab-v2-staging"
  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled" # billed per task; on for load tests
  }
  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.this.arn
  }
}

# Service Connect: services find each other by name (meet -> http://agent-runner:7860)
# without a second load balancer.
resource "aws_service_discovery_http_namespace" "this" {
  name = "meetlab-v2-staging"
}

data "aws_ssm_parameter" "ecs_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

resource "aws_iam_role" "instance" {
  name                 = "meetlab-v2-staging-ecs-instance"
  permissions_boundary = local.boundary
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "instance" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "instance" {
  name = "meetlab-v2-staging-ecs-instance"
  role = aws_iam_role.instance.name
}

resource "aws_launch_template" "ecs" {
  name                   = "meetlab-v2-staging-ecs"
  image_id               = data.aws_ssm_parameter.ecs_ami.value
  instance_type          = "t3.medium"
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers in bridge mode sit one hop away
  }
  user_data = base64encode("#!/bin/bash\necho ECS_CLUSTER=${aws_ecs_cluster.this.name} >> /etc/ecs/ecs.config\n")
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "meetlab-v2-staging-ecs", Project = "meetlab-v2", Environment = "staging" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meetlab-v2", Environment = "staging" }
  }
}

# ponytail: one t3.medium for meet and the runner; bots have their own group (bots.tf).
resource "aws_autoscaling_group" "ecs" {
  name                  = "meetlab-v2-staging-ecs"
  min_size              = 1
  max_size              = 2
  vpc_zone_identifier   = [for s in aws_subnet.private : s.id]
  protect_from_scale_in = true
  launch_template {
    id      = aws_launch_template.ecs.id
    version = aws_launch_template.ecs.latest_version
  }
  instance_refresh {
    strategy = "Rolling"
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

resource "aws_ecs_capacity_provider" "ec2" {
  name = "meetlab-v2-staging-services"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.ecs.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = concat([aws_ecs_capacity_provider.ec2.name, aws_ecs_capacity_provider.bots.name, aws_ecs_capacity_provider.stt_nim.name, aws_ecs_capacity_provider.livekit.name], [for c in aws_ecs_capacity_provider.model : c.name])
  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.ec2.name
    weight            = 1
  }
}

resource "aws_iam_role" "execution" {
  name                 = "meetlab-v2-staging-task-execution"
  permissions_boundary = local.boundary
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}

locals {
  parameters = "arn:aws:ssm:us-east-1:${data.aws_caller_identity.current.account_id}:parameter/meetlab-v2/staging"
  db_secret  = aws_db_instance.this.master_user_secret[0].secret_arn
}

# Values are written by infra/v2/seed-staging-secrets.sh; Terraform only names them.
resource "aws_iam_role_policy" "execution_secrets" {
  name = "staging-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ssm:GetParameters", Resource = "${local.parameters}/*" },
      { Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = local.db_secret },
      {
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = ["ssm.us-east-1.amazonaws.com", "secretsmanager.us-east-1.amazonaws.com"] } }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
