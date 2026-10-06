# The two roles GitHub Actions uses for infra/v2. Applied by a human, once per change,
# because the apply role must not be able to widen its own permissions. Its state lives
# outside the v2/ prefix for the same reason. See README.md.
#
# Permissions start at state access only; each infra/v2 PR that adds a resource type
# adds the actions it needs here, so the roles never hold more than the stack uses.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    bucket       = "meetlab-tfstate-848180123498"
    key          = "bootstrap/v2.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { Project = "meetlab-v2", ManagedBy = "terraform/infra/v2/bootstrap" }
  }
}

variable "state_bucket" {
  type    = string
  default = "meetlab-tfstate-848180123498"
}

data "aws_caller_identity" "current" {}

locals {
  repo = "repo:wwbp/MeetLab"
  oidc = "token.actions.githubusercontent.com"
  rds  = "arn:aws:rds:us-east-1:${data.aws_caller_identity.current.account_id}"
}

# Shared with every other lab repo; referenced, never managed here.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://${local.oidc}"
}

# The two environments infra/v2/stack is applied as. Every per-environment policy below
# is built once for each and names only that environment's resources (p = name prefix),
# so neither environment's CI can reach the other's database, recordings or tasks.
locals {
  envs = {
    staging = {
      p               = "meetlab-v2-staging", github = "staging", apply_role = "meetlab-v2-tf-apply",
      acceptance_role = "meetlab-v2-acceptance", boundary = "meetlab-v2-boundary",
      hostnames       = ["meet-staging.wwbp.org", "livekit-staging.wwbp.org", "turn-staging.wwbp.org"],
    }
    prod = {
      p               = "meetlab-v2-prod", github = "production", apply_role = "meetlab-v2-tf-apply-prod",
      acceptance_role = "meetlab-v2-acceptance-prod", boundary = "meetlab-v2-prod-boundary",
      # -v2 until cutover; meet.wwbp.org is v1's until then (docs/v1-to-v2-migration.md)
      hostnames = ["meet-v2.wwbp.org", "livekit-v2.wwbp.org", "turn-v2.wwbp.org"],
    }
  }

  trust_for = { for name, subjects in merge(
    { plan = ["${local.repo}:pull_request", "${local.repo}:ref:refs/heads/v2"] },
    { for env, e in local.envs : env => ["${local.repo}:environment:${e.github}"] },
    ) :
    name => jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect    = "Allow"
        Action    = "sts:AssumeRoleWithWebIdentity"
        Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
        Condition = {
          StringEquals = {
            "${local.oidc}:aud" = "sts.amazonaws.com"
            "${local.oidc}:sub" = subjects
          }
        }
      }]
    })
  }

  state = { for env, e in local.envs : env => jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = "arn:aws:s3:::${var.state_bucket}"
        Condition = { StringLike = { "s3:prefix" = "v2/${env}/*" } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::${var.state_bucket}/v2/${env}/*"
      },
    ]
  }) }

  ec2_read = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "ec2:Describe*", Resource = "*" }]
  })

  # Allow-only, keyed on our tags, so nothing here can reach another project's (or the
  # other environment's) resources: ec2 ids aren't name-prefixed, so tags are the only handle.
  ec2_write = { for env, e in local.envs : env => jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "OwnResources"
        Effect    = "Allow"
        Action    = "ec2:*"
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/Project" = "meetlab-v2", "aws:ResourceTag/Environment" = env } }
      },
      {
        Sid       = "CreateTagged"
        Effect    = "Allow"
        Action    = ["ec2:Create*", "ec2:AllocateAddress"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestTag/Project" = "meetlab-v2", "aws:RequestTag/Environment" = env } }
      },
      {
        Sid       = "TagOnlyOnCreate"
        Effect    = "Allow"
        Action    = "ec2:CreateTags"
        Resource  = "*"
        Condition = { Null = { "ec2:CreateAction" = "false" } }
      },
    ]
  }) }

  data_read = { for env, e in local.envs : env => jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["rds:Describe*", "rds:ListTagsForResource"], Resource = "*" },
      { Effect = "Allow", Action = ["s3:Get*", "s3:List*"], Resource = "arn:aws:s3:::${e.p}-*" },
    ]
  }) }

  data_write = { for env, e in local.envs : env => jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OwnDatabases"
        Effect = "Allow"
        Action = "rds:*"
        Resource = [
          "${local.rds}:db:${e.p}",
          "${local.rds}:subgrp:${e.p}",
          "${local.rds}:snapshot:${e.p}-*",
        ]
      },
      {
        # CreateDBInstance is also authorized against the defaults it attaches.
        Sid      = "DefaultGroupsAtCreate"
        Effect   = "Allow"
        Action   = "rds:CreateDBInstance"
        Resource = ["${local.rds}:pg:default.postgres17", "${local.rds}:og:default:postgres-17"]
      },
      {
        # manage_master_user_password: RDS creates an rds!db-... secret as the caller.
        Sid      = "ManagedMasterPassword"
        Effect   = "Allow"
        Action   = ["secretsmanager:CreateSecret", "secretsmanager:TagResource"]
        Resource = "arn:aws:secretsmanager:us-east-1:${data.aws_caller_identity.current.account_id}:secret:rds!*"
      },
      {
        Sid       = "DefaultKeysViaService"
        Effect    = "Allow"
        Action    = ["kms:DescribeKey", "kms:CreateGrant", "kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = ["rds.us-east-1.amazonaws.com", "secretsmanager.us-east-1.amazonaws.com"] } }
      },
      {
        Sid      = "OwnBuckets"
        Effect   = "Allow"
        Action   = "s3:*"
        Resource = "arn:aws:s3:::${e.p}-*"
      },
    ]
  }) }

  images_read = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ecr:Describe*", "ecr:List*", "ecr:GetLifecyclePolicy", "ecr:GetRepositoryPolicy"]
      Resource = "*"
    }]
  })

  # Manage and push to our repositories. GetAuthorizationToken has no resource scope;
  # the token alone grants nothing without repository permissions.
  images_write = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ecr:*"
        Resource = ["arn:aws:ecr:us-east-1:${data.aws_caller_identity.current.account_id}:repository/meetlab-v2/*"]
      },
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
    ]
  })

  # The plan role refreshes staging for PR plans; production is planned inside its own
  # gated deploy, with its apply role.
  plan_policies = {
    state       = local.state["staging"], ec2 = local.ec2_read, data = local.data_read["staging"],
    images_read = local.images_read, compute_read = local.compute_read["staging"],
  }
  apply_policies = merge([for env, e in local.envs : {
    for name, policy in {
      state        = local.state[env], ec2 = local.ec2_read, ec2_write = local.ec2_write[env],
      data         = local.data_read[env], data_write = local.data_write[env],
      images_read  = local.images_read, images = local.images_write,
      compute_read = local.compute_read[env], compute = local.compute_write[env], iam = local.iam_write[env],
    } : "${env}/${name}" => { env = env, name = name, policy = policy }
  }]...)
}

resource "aws_iam_role" "plan" {
  name                 = "meetlab-v2-tf-plan"
  assume_role_policy   = local.trust_for["plan"]
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "plan" {
  for_each = local.plan_policies
  name     = each.key
  role     = aws_iam_role.plan.id
  policy   = each.value
}

resource "aws_iam_role" "apply" {
  for_each             = local.envs
  name                 = each.value.apply_role
  assume_role_policy   = local.trust_for[each.key]
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "apply" {
  for_each = local.apply_policies
  name     = each.value.name
  role     = aws_iam_role.apply[each.value.env].id
  policy   = each.value.policy
}

output "plan_role_arn" { value = aws_iam_role.plan.arn }
output "apply_role_arns" { value = { for env, r in aws_iam_role.apply : env => r.arn } }

# Staging's role and policies, from before there were two environments: same names, moved.
moved {
  from = aws_iam_role.apply
  to   = aws_iam_role.apply["staging"]
}

moved {
  from = aws_iam_role_policy.plan
  to   = aws_iam_role_policy.plan["state"]
}

moved {
  from = aws_iam_role_policy.apply["state"]
  to   = aws_iam_role_policy.apply["staging/state"]
}

moved {
  from = aws_iam_role_policy.apply["ec2"]
  to   = aws_iam_role_policy.apply["staging/ec2"]
}

moved {
  from = aws_iam_role_policy.apply["ec2_write"]
  to   = aws_iam_role_policy.apply["staging/ec2_write"]
}

moved {
  from = aws_iam_role_policy.apply["data"]
  to   = aws_iam_role_policy.apply["staging/data"]
}

moved {
  from = aws_iam_role_policy.apply["data_write"]
  to   = aws_iam_role_policy.apply["staging/data_write"]
}

moved {
  from = aws_iam_role_policy.apply["images_read"]
  to   = aws_iam_role_policy.apply["staging/images_read"]
}

moved {
  from = aws_iam_role_policy.apply["images"]
  to   = aws_iam_role_policy.apply["staging/images"]
}

moved {
  from = aws_iam_role_policy.apply["compute_read"]
  to   = aws_iam_role_policy.apply["staging/compute_read"]
}

moved {
  from = aws_iam_role_policy.apply["compute"]
  to   = aws_iam_role_policy.apply["staging/compute"]
}

moved {
  from = aws_iam_role_policy.apply["iam"]
  to   = aws_iam_role_policy.apply["staging/iam"]
}
