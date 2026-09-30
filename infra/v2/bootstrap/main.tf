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

locals {
  repo = "repo:wwbp/MeetLab"
  oidc = "token.actions.githubusercontent.com"
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
}

resource "aws_iam_role" "plan" {
  name                 = "meetlab-v2-tf-plan"
  assume_role_policy   = local.trust["plan"]
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "plan" {
  name   = "state"
  role   = aws_iam_role.plan.id
  policy = local.state
}

resource "aws_iam_role" "apply" {
  name                 = "meetlab-v2-tf-apply"
  assume_role_policy   = local.trust["apply"]
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "apply" {
  name   = "state"
  role   = aws_iam_role.apply.id
  policy = local.state
}

output "plan_role_arn" { value = aws_iam_role.plan.arn }
output "apply_role_arn" { value = aws_iam_role.apply.arn }
