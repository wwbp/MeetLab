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

locals {
  trust = {
    for role, subjects in {
      plan  = ["${local.repo}:pull_request", "${local.repo}:ref:refs/heads/v2"]
      apply = ["${local.repo}:environment:staging"]
    } :
    role => jsonencode({
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

  state = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = "arn:aws:s3:::${var.state_bucket}"
        Condition = { StringLike = { "s3:prefix" = "v2/*" } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::${var.state_bucket}/v2/*"
      },
    ]
  })

  ec2_read = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "ec2:Describe*", Resource = "*" }]
  })

  # Allow-only, keyed on our tag, so nothing here can reach another project's
  # resources: ec2 ids aren't name-prefixed, so the tag is the only handle.
  ec2_write = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "OwnResources"
        Effect    = "Allow"
        Action    = "ec2:*"
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/Project" = "meetlab-v2" } }
      },
      {
        Sid       = "CreateTagged"
        Effect    = "Allow"
        Action    = ["ec2:Create*", "ec2:AllocateAddress"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestTag/Project" = "meetlab-v2" } }
      },
      {
        Sid       = "TagOnlyOnCreate"
        Effect    = "Allow"
        Action    = "ec2:CreateTags"
        Resource  = "*"
        Condition = { Null = { "ec2:CreateAction" = "false" } }
      },
    ]
  })

  data_read = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["rds:Describe*", "rds:ListTagsForResource"], Resource = "*" },
      { Effect = "Allow", Action = ["s3:Get*", "s3:List*"], Resource = "arn:aws:s3:::meetlab-v2-*" },
    ]
  })

  data_write = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OwnDatabases"
        Effect = "Allow"
        Action = "rds:*"
        Resource = [
          "${local.rds}:db:meetlab-v2-*",
          "${local.rds}:subgrp:meetlab-v2-*",
          "${local.rds}:snapshot:meetlab-v2-*",
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
        Resource = "arn:aws:s3:::meetlab-v2-*"
      },
    ]
  })

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

  policies = {
    plan = { state = local.state, ec2 = local.ec2_read, data = local.data_read, images_read = local.images_read }
    apply = {
      state       = local.state, ec2 = local.ec2_read, ec2_write = local.ec2_write,
      data        = local.data_read, data_write = local.data_write,
      images_read = local.images_read, images = local.images_write,
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "meetlab-v2-tf-plan"
  assume_role_policy   = local.trust["plan"]
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "plan" {
  for_each = local.policies.plan
  name     = each.key
  role     = aws_iam_role.plan.id
  policy   = each.value
}

resource "aws_iam_role" "apply" {
  name                 = "meetlab-v2-tf-apply"
  assume_role_policy   = local.trust["apply"]
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "apply" {
  for_each = local.policies.apply
  name     = each.key
  role     = aws_iam_role.apply.id
  policy   = each.value
}

output "plan_role_arn" { value = aws_iam_role.plan.arn }
output "apply_role_arn" { value = aws_iam_role.apply.arn }

moved {
  from = aws_iam_role_policy.plan
  to   = aws_iam_role_policy.plan["state"]
}

moved {
  from = aws_iam_role_policy.apply
  to   = aws_iam_role_policy.apply["state"]
}
