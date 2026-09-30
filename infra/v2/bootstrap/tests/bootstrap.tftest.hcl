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
      for s in jsondecode(aws_iam_role_policy.plan.policy).Statement :
      alltrue([for a in flatten([s.Action]) : can(regex("^[a-z0-9-]+:(Describe|List|Get)", a)) || contains(["s3:PutObject", "s3:DeleteObject"], a)])
    ])
    error_message = "plan role is read-only apart from writing the state lock"
  }
}
