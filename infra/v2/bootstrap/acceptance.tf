# The role each environment's CI `acceptance` job uses to run
# agent-runner/tests/acceptance_staging.py after a deploy: read the secrets the test needs,
# watch and stop bot tasks, exec into one to kill -9 it, read the environment's logs, and
# simulate its roles' permissions (permission_contract.py); and, for the load test workflow,
# start the load generator and read its results. Nothing else, and only in its environment.

locals {
  cluster_arn = { for env, e in local.envs : env => "arn:aws:ecs:us-east-1:${local.account}:cluster/${e.p}" }
}

resource "aws_iam_role" "acceptance" {
  for_each             = local.envs
  name                 = each.value.acceptance_role
  assume_role_policy   = local.trust_for[each.key]
  max_session_duration = 14400 # the load test workflow waits on a soak with one credential
}

resource "aws_iam_role_policy" "acceptance" {
  for_each = local.envs
  name     = "${each.key}-acceptance"
  role     = aws_iam_role.acceptance[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = [for n in ["CONSOLE_PASSWORD", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SELFHOSTED_LIVEKIT_API_KEY", "SELFHOSTED_LIVEKIT_API_SECRET"] : "arn:aws:ssm:us-east-1:${local.account}:parameter/meetlab-v2/${each.key}/${n}"]
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
        Condition = { ArnEquals = { "ecs:cluster" = local.cluster_arn[each.key] } }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks", "ecs:StopTask", "ecs:ExecuteCommand"]
        Resource = ["arn:aws:ecs:us-east-1:${local.account}:task/${each.value.p}/*", local.cluster_arn[each.key]]
      },
      {
        # acceptance transcript: is the STT NIM on, and healthy?
        Effect   = "Allow"
        Action   = "ecs:DescribeServices"
        Resource = "arn:aws:ecs:us-east-1:${local.account}:service/${each.value.p}/*"
      },
      {
        # acceptance audio_recording: did a file land? Names only, never contents.
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = "arn:aws:s3:::${each.value.p}-media-${local.account}"
        Condition = { StringLike = { "s3:prefix" = "recordings/*" } }
      },
      {
        # "Load test v2" (.github/workflows/loadtest-v2.yml): start the load generator
        # (infra/v2/stack/loadgen.tf) in its own subnets and group, and read its results.
        Effect    = "Allow"
        Action    = "ecs:RunTask"
        Resource  = "arn:aws:ecs:us-east-1:${local.account}:task-definition/${each.value.p}-loadgen:*"
        Condition = { ArnEquals = { "ecs:cluster" = local.cluster_arn[each.key] } }
      },
      {
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = [for r in ["task-execution", "loadgen"] : "arn:aws:iam::${local.account}:role/${each.value.p}-${r}"]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
      {
        Effect    = "Allow"
        Action    = ["ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" } }
      },
      {
        # load_report.py: what each part of the environment did during each step
        Effect    = "Allow"
        Action    = "cloudwatch:GetMetricData"
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" } }
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:aws:s3:::${each.value.p}-media-${local.account}/loadtests/*"
      },
      {
        # agent-runner/tests/permission_contract.py
        Effect   = "Allow"
        Action   = "iam:SimulatePrincipalPolicy"
        Resource = ["arn:aws:iam::${local.account}:role/${each.value.p}-*", "arn:aws:iam::${local.account}:user/${each.value.p}-*"]
      },
      {
        Effect   = "Allow"
        Action   = "logs:FilterLogEvents"
        Resource = ["arn:aws:logs:us-east-1:${local.account}:log-group:/meetlab-v2/${each.key}/*", "arn:aws:logs:us-east-1:${local.account}:log-group:/meetlab-v2/${each.key}/*:*"]
      },
    ]
  })
}

output "acceptance_role_arns" { value = { for env, r in aws_iam_role.acceptance : env => r.arn } }

moved {
  from = aws_iam_role.acceptance
  to   = aws_iam_role.acceptance["staging"]
}

moved {
  from = aws_iam_role_policy.acceptance
  to   = aws_iam_role_policy.acceptance["staging"]
}
