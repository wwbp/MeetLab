# NVIDIA Parakeet-TDT NIM on a g6 (L4) GPU instance — the concurrent, Triton-batched STT
# server that replaces the single-threaded Shadowfita sidecar. See docs/gpu-stt-deployment.md.

# Latest AWS Deep Learning Base GPU AMI (Ubuntu 22.04) — ships NVIDIA driver + Docker +
# Container Toolkit, so user-data only has to install + start the NIM unit.
data "aws_ami" "dl_base_gpu" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

# ── Security group: NIM ports reachable ONLY from the agent-runner EB SG ──────────────
resource "aws_security_group" "nim" {
  name        = "meetlab-stt-nim"
  description = "Parakeet NIM — HTTP/gRPC open to the agent-runner EB SG only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "meetlab-stt-nim" })

  ingress {
    description     = "NIM HTTP (OpenAI-compatible /v1/audio/transcriptions)"
    from_port       = 9000
    to_port         = 9000
    protocol        = "tcp"
    security_groups = [var.agent_runner_sg_id]
  }

  ingress {
    description     = "NIM gRPC"
    from_port       = 50051
    to_port         = 50051
    protocol        = "tcp"
    security_groups = [var.agent_runner_sg_id]
  }

  egress {
    description = "All egress (NGC image pull, model download)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── IAM: SSM-managed (Run Command, no SSH) + read the NGC key from SSM SecureString ───
resource "aws_iam_role" "nim" {
  name = "meetlab-stt-nim"
  tags = var.tags
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.nim.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "ngc_ssm_read" {
  name = "read-ngc-key"
  role = aws_iam_role.nim.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.region}:*:parameter${var.ngc_api_key_ssm_param}"
      },
      {
        Effect    = "Allow"
        Action    = ["kms:Decrypt"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" } }
      }
    ]
  })
}

resource "aws_iam_instance_profile" "nim" {
  name = "meetlab-stt-nim"
  role = aws_iam_role.nim.name
  tags = var.tags
}

# ── The GPU instance ──────────────────────────────────────────────────────────────────
resource "aws_instance" "nim" {
  ami                    = data.aws_ami.dl_base_gpu.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.nim.id]
  iam_instance_profile   = aws_iam_instance_profile.nim.name

  user_data = templatefile("${path.module}/user-data.sh", {
    nim_image             = var.nim_image
    nim_tags_selector     = var.nim_tags_selector
    ngc_api_key_ssm_param = var.ngc_api_key_ssm_param
    region                = var.region
  })
  # Re-run user-data when the NIM config changes (replaces the instance).
  user_data_replace_on_change = true

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2
  }

  tags = merge(var.tags, { Name = "meetlab-stt-gpu-nim" })
}

# ── Stable private DNS so NEMOTRON_STT_URL doesn't change with the instance ───────────
resource "aws_route53_record" "nim" {
  zone_id = var.private_zone_id
  name    = var.stt_dns_name
  type    = "A"
  ttl     = 60
  records = [aws_instance.nim.private_ip]
}
