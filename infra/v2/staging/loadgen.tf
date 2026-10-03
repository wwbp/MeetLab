# The load generator (load-test readiness L5): synthetic participants (agent-runner's
# tests/load_run.py) run inside AWS as a one-off Fargate task, started by the "Load test
# v2" workflow, so a run measures staging rather than a laptop's network. They come in
# the front door as people do: meet's console and LiveKit's public address. Fargate:
# no machines to keep, billed only while a run lasts, and each run picks its size.

resource "aws_security_group" "loadgen" {
  name        = "meetlab-v2-staging-loadgen"
  description = "Load generator: out only (meet and LiveKit, through the NAT)"
  vpc_id      = aws_vpc.this.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "loadgen" {
  name                 = "meetlab-v2-staging-loadgen"
  permissions_boundary = local.boundary
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ecs-tasks.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "loadgen_results" {
  name = "write-results"
  role = aws_iam_role.loadgen.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "s3:PutObject", Resource = "${aws_s3_bucket.media.arn}/loadtests/*" }]
  })
}

resource "aws_cloudwatch_log_group" "loadgen" {
  name              = "/meetlab-v2/staging/loadgen"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "loadgen" {
  family                   = "meetlab-v2-staging-loadgen"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 4096 # a run asks for more (the workflow's cpu input)
  memory                   = 8192
  skip_destroy             = true # see tests/runner.tftest.hcl
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.loadgen.arn
  container_definitions = jsonencode([{
    name        = "loadgen"
    image       = "${aws_ecr_repository.this["agent-runner"].repository_url}:${var.image_tag}"
    essential   = true
    command     = ["python", "tests/load_run.py"]
    environment = concat(local.livekit_environment, [{ name = "MEET_URL", value = "https://meet-staging.wwbp.org" }])
    secrets = concat(local.livekit_secrets, [
      { name = "CONSOLE_PASSWORD", valueFrom = "${local.parameters}/CONSOLE_PASSWORD" },
      { name = "OPENAI_API_KEY", valueFrom = "${local.parameters}/OPENAI_API_KEY" }, # the answer judge (judge.py)
    ])
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.loadgen.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = "loadgen"
      }
    }
  }])
}
