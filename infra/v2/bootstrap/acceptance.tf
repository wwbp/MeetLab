# The role the CI `acceptance` job uses to run agent-runner/tests/acceptance_staging.py
# against staging after each deploy: read the four secrets the test needs, watch and
# stop bot tasks, exec into one to kill -9 it, read staging logs, and simulate the
# staging roles' permissions (permission_contract.py); and, for the load test workflow,
# start the load generator and read its results. Nothing else.

locals {
  cluster_arn = "arn:aws:ecs:us-east-1:${local.account}:cluster/meetlab-v2-staging"
}

resource "aws_iam_role" "acceptance" {
  name                 = "meetlab-v2-acceptance"
  assume_role_policy   = local.trust["acceptance"]
  max_session_duration = 14400 # the load test workflow waits on a soak with one credential
}

resource "aws_iam_role_policy" "acceptance" {
  name = "staging-acceptance"
  role = aws_iam_role.acceptance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = [for n in ["CONSOLE_PASSWORD", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SELFHOSTED_LIVEKIT_API_KEY", "SELFHOSTED_LIVEKIT_API_SECRET"] : "arn:aws:ssm:us-east-1:${local.account}:parameter/meetlab-v2/staging/${n}"]
      },
      {
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.us-east-1.amazonaws.com" } }
      },
      {
        Effect    = "Allow"
        Action    = "ecs:ListTasks"
        Resource  = "*"
        Condition = { ArnEquals = { "ecs:cluster" = local.cluster_arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks", "ecs:StopTask", "ecs:ExecuteCommand"]
        Resource = ["arn:aws:ecs:us-east-1:${local.account}:task/meetlab-v2-staging/*", local.cluster_arn]
      },
      {
        # acceptance transcript: is the STT NIM on, and healthy?
        Effect   = "Allow"
        Action   = "ecs:DescribeServices"
        Resource = "arn:aws:ecs:us-east-1:${local.account}:service/meetlab-v2-staging/*"
      },
      {
        # acceptance audio_recording: did a file land? Names only, never contents.
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = "arn:aws:s3:::meetlab-v2-staging-media-${local.account}"
        Condition = { StringLike = { "s3:prefix" = "recordings/*" } }
      },
      {
        # "Load test v2" (.github/workflows/loadtest-v2.yml): start the load generator
        # (infra/v2/stack/loadgen.tf) in its own subnets and group, and read its results.
        Effect    = "Allow"
        Action    = "ecs:RunTask"
        Resource  = "arn:aws:ecs:us-east-1:${local.account}:task-definition/meetlab-v2-staging-loadgen:*"
        Condition = { ArnEquals = { "ecs:cluster" = local.cluster_arn } }
      },
      {
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = [for r in ["task-execution", "loadgen"] : "arn:aws:iam::${local.account}:role/meetlab-v2-staging-${r}"]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
      {
        Effect    = "Allow"
        Action    = ["ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" } }
      },
      {
        # load_report.py: what each part of staging did during each step
        Effect    = "Allow"
        Action    = "cloudwatch:GetMetricData"
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" } }
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:aws:s3:::meetlab-v2-staging-media-${local.account}/loadtests/*"
      },
      {
        # agent-runner/tests/permission_contract.py
        Effect   = "Allow"
        Action   = "iam:SimulatePrincipalPolicy"
        Resource = ["arn:aws:iam::${local.account}:role/meetlab-v2-staging-*", "arn:aws:iam::${local.account}:user/meetlab-v2-staging-*"]
      },
      {
        Effect   = "Allow"
        Action   = "logs:FilterLogEvents"
        Resource = ["arn:aws:logs:us-east-1:${local.account}:log-group:/meetlab-v2/staging/*", "arn:aws:logs:us-east-1:${local.account}:log-group:/meetlab-v2/staging/*:*"]
      },
    ]
  })
}

output "acceptance_role_arn" { value = aws_iam_role.acceptance.arn }
