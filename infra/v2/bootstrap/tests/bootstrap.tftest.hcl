# The shared lab account is the reason these tests exist: every other wwbp repo can
# already assume github-actions-service-acc. These roles must be reachable from this
# repo only, and the apply role only from the reviewer-gated `staging` environment.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
}

variables {
  state_bucket = "meetlab-tfstate-123456789012"
}

run "plan_role_trusts_only_this_repo_prs_and_v2_pushes" {
  command = plan

  assert {
    condition = toset(jsondecode(aws_iam_role.plan.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]) == toset([
      "repo:wwbp/MeetLab:pull_request",
      "repo:wwbp/MeetLab:ref:refs/heads/v2",
    ])
    error_message = "plan role must trust exactly this repo's PRs and pushes to v2"
  }

  assert {
    condition     = jsondecode(aws_iam_role.plan.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "audience must be pinned"
  }
}

run "apply_role_trusts_only_the_staging_environment" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.apply.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == ["repo:wwbp/MeetLab:environment:staging"]
    error_message = "apply role must be reachable only through the reviewer-gated staging environment"
  }
}

run "no_trust_policy_uses_wildcards" {
  command = plan

  assert {
    condition = alltrue([
      for r in [aws_iam_role.plan, aws_iam_role.apply] :
      !strcontains(r.assume_role_policy, "*") && !strcontains(r.assume_role_policy, "StringLike")
    ])
    error_message = "trust policies must match subjects exactly"
  }
}

run "state_access_is_confined_to_the_v2_prefix" {
  command = plan

  assert {
    condition = alltrue([
      for s in jsondecode(local.state).Statement :
      alltrue([for r in flatten([s.Resource]) : endswith(r, "meetlab-tfstate-123456789012") || strcontains(r, "meetlab-tfstate-123456789012/v2/")])
    ])
    error_message = "roles must not touch stt-nim or any other state in the bucket"
  }
}

run "plan_role_cannot_write_anything_but_its_lock" {
  command = plan

  assert {
    condition = alltrue([
      for s in flatten([for p in aws_iam_role_policy.plan : jsondecode(p.policy).Statement]) :
      alltrue([for a in flatten([s.Action]) : can(regex("^[a-z0-9-]+:(Describe|List|Get)", a)) || contains(["s3:PutObject", "s3:DeleteObject"], a)])
    ])
    error_message = "plan role is read-only apart from writing the state lock"
  }
}

run "plan_role_can_read_the_network" {
  command = plan

  assert {
    condition     = contains(flatten([for p in aws_iam_role_policy.plan : [for s in jsondecode(p.policy).Statement : s.Action]]), "ec2:Describe*")
    error_message = "plan needs ec2:Describe* to refresh the VPC"
  }
}

# The shared account holds other projects' VPCs and instances. Every EC2 write the
# apply role holds must be pinned to our tag: on the resource, on the create request,
# or (for CreateTags) only as part of a create, so it can't adopt someone else's.
run "apply_role_ec2_writes_are_confined_to_meetlab_v2" {
  command = plan

  assert {
    condition = alltrue([
      for s in flatten([for p in aws_iam_role_policy.apply : jsondecode(p.policy).Statement]) :
      anytrue([
        alltrue([for a in flatten([s.Action]) : !startswith(a, "ec2:") || startswith(a, "ec2:Describe")]),
        try(s.Condition.StringEquals["aws:ResourceTag/Project"], "") == "meetlab-v2",
        try(s.Condition.StringEquals["aws:RequestTag/Project"], "") == "meetlab-v2",
        try(s.Condition.Null["ec2:CreateAction"], "") == "false",
      ])
    ])
    error_message = "an ec2 write statement is not scoped to Project=meetlab-v2"
  }
}

run "plan_role_can_read_the_data_layer" {
  command = plan

  assert {
    condition = alltrue([for a in ["rds:Describe*", "rds:ListTagsForResource", "s3:Get*", "s3:List*"] :
    contains(flatten([for p in aws_iam_role_policy.plan : [for s in jsondecode(p.policy).Statement : s.Action]]), a)])
    error_message = "plan needs to refresh the database and bucket"
  }
}

# Every non-read statement the apply role holds must name our resources, carry our
# tag, or be usable only through a named AWS service. The allowed non-meetlab ARNs
# are the ones RDS insists on at create time (default parameter/option groups, and
# the rds!... secret it makes for the managed master password).
run "apply_role_writes_are_scoped_to_meetlab_v2" {
  command = plan

  assert {
    condition = alltrue([
      for s in flatten([for p in aws_iam_role_policy.apply : jsondecode(p.policy).Statement]) :
      anytrue([
        alltrue([for a in flatten([s.Action]) : can(regex(":(Describe|List|Get)", a))]),
        alltrue([for r in flatten([s.Resource]) : anytrue([
          strcontains(r, "meetlab-v2"),
          strcontains(r, "meetlab-tfstate-123456789012"),
          strcontains(r, ":secret:rds!"),
          strcontains(r, ":pg:default.postgres17"),
          strcontains(r, ":og:default:postgres-17"),
        ])]),
        try(s.Condition.StringEquals["aws:ResourceTag/Project"], "") == "meetlab-v2",
        try(s.Condition.StringEquals["aws:RequestTag/Project"], "") == "meetlab-v2",
        try(s.Condition.Null["ec2:CreateAction"], "") == "false",
        length(try(s.Condition.StringEquals["kms:ViaService"], [])) > 0,
      ])
    ])
    error_message = "an apply statement can write outside meetlab-v2"
  }
}

# The media bucket holds study participants' recordings. Terraform manages buckets,
# never objects, and any PR can assume the plan role, so neither role may reach objects.
run "ci_roles_cannot_read_recordings" {
  command = plan

  assert {
    condition = alltrue([
      for s in flatten([for p in merge(aws_iam_role_policy.plan, aws_iam_role_policy.apply) : jsondecode(p.policy).Statement]) :
      alltrue([for r in flatten([s.Resource]) : !(startswith(r, "arn:aws:s3:::meetlab-v2-") && strcontains(r, "/"))])
    ])
    error_message = "a CI role can reach objects in a meetlab-v2 bucket"
  }
}
