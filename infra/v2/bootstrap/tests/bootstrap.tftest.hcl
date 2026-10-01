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
        can(s.Condition.ArnLike["ec2:LaunchTemplate"]),
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
        s.Effect == "Deny",
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
        # DNS: only our one record name in the shared wwbp.org zone.
        length(try(s.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsNormalizedRecordNames"], [])) > 0,
        # RunInstances from a launch template; the template itself must carry our tag.
        can(s.Condition.ArnLike["ec2:LaunchTemplate"]),
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

run "ci_can_read_and_push_images" {
  command = plan

  assert {
    condition = alltrue([for a in ["ecr:Describe*", "ecr:List*", "ecr:GetLifecyclePolicy"] :
    contains(flatten([for p in aws_iam_role_policy.plan : [for s in jsondecode(p.policy).Statement : s.Action]]), a)])
    error_message = "plan needs to refresh the repositories"
  }
  assert {
    condition = anytrue([for s in jsondecode(aws_iam_role_policy.apply["images"].policy).Statement :
    contains(flatten([s.Action]), "ecr:*") && flatten([s.Resource]) == ["arn:aws:ecr:us-east-1:123456789012:repository/meetlab-v2/*"]])
    error_message = "apply manages and pushes to meetlab-v2/* repositories only"
  }
}

run "plan_role_can_read_compute" {
  command = plan

  assert {
    condition = alltrue([for a in ["ecs:Describe*", "elasticloadbalancing:Describe*", "autoscaling:Describe*", "logs:Describe*", "iam:GetRole", "acm:DescribeCertificate", "acm:GetCertificate", "route53:GetHostedZone"] :
    contains(flatten([for p in aws_iam_role_policy.plan : [for s in jsondecode(p.policy).Statement : s.Action]]), a)])
    error_message = "plan needs to refresh the compute layer"
  }
}

# The boundary is the ceiling for every role CI creates. It must not grant IAM
# (beyond passing our own roles to ECS) or reach other projects' secrets.
run "boundary_caps_what_ci_created_roles_can_do" {
  command = plan

  assert {
    condition     = aws_iam_policy.boundary.name == "meetlab-v2-boundary"
    error_message = "staging references the boundary by this name"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_policy.boundary.policy).Statement :
      alltrue([for a in flatten([s.Action]) : !startswith(a, "iam:") || (a == "iam:PassRole" && flatten([s.Resource]) == ["arn:aws:iam::123456789012:role/meetlab-v2-staging-*"])])
    ])
    error_message = "the boundary grants no IAM beyond passing meetlab-v2-staging roles"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_policy.boundary.policy).Statement :
      alltrue([for r in flatten([s.Resource]) : r == "*" || anytrue([
        strcontains(r, "meetlab-v2"), strcontains(r, ":secret:rds!"), strcontains(r, "parameter/aws/service/"),
      ])])
      if anytrue([for a in flatten([s.Action]) : can(regex("^(ssm|secretsmanager|s3):", a))])
    ])
    error_message = "the boundary reaches parameters, secrets or buckets outside meetlab-v2"
  }
}

run "ci_can_only_create_roles_that_carry_the_boundary" {
  command = plan

  assert {
    condition = alltrue([for s in flatten([for p in aws_iam_role_policy.apply : jsondecode(p.policy).Statement]) :
      try(s.Condition.StringEquals["iam:PermissionsBoundary"], "") == "arn:aws:iam::123456789012:policy/meetlab-v2-boundary"
      if s.Effect == "Allow" && anytrue([for a in flatten([s.Action]) : contains(["iam:CreateRole", "iam:PutRolePermissionsBoundary"], a)])
    ])
    error_message = "CreateRole must require meetlab-v2-boundary"
  }
}

run "ci_cannot_touch_its_own_roles_or_the_boundary" {
  command = plan

  assert {
    condition = alltrue([for s in flatten([for p in aws_iam_role_policy.apply : jsondecode(p.policy).Statement]) :
      alltrue([for r in flatten([s.Resource]) : !strcontains(r, "meetlab-v2-tf-") && !strcontains(r, "policy/meetlab-v2-boundary")])
      if s.Effect == "Allow"
    ])
    error_message = "an allow reaches the CI roles or the boundary"
  }
  assert {
    condition = anytrue([for s in flatten([for p in aws_iam_role_policy.apply : jsondecode(p.policy).Statement]) :
      s.Effect == "Deny" && contains(flatten([s.Action]), "iam:*") &&
      contains(flatten([s.Resource]), "arn:aws:iam::123456789012:role/meetlab-v2-tf-*") &&
      contains(flatten([s.Resource]), "arn:aws:iam::123456789012:policy/meetlab-v2-boundary")
    ])
    error_message = "an explicit deny must protect the CI roles and the boundary"
  }
  assert {
    condition = anytrue([for s in flatten([for p in aws_iam_role_policy.apply : jsondecode(p.policy).Statement]) :
    s.Effect == "Deny" && contains(flatten([s.Action]), "iam:DeleteRolePermissionsBoundary")])
    error_message = "no role may lose its boundary"
  }
}

run "ci_can_manage_our_service_connect_namespace" {
  command = plan

  assert {
    condition = alltrue([for a in ["servicediscovery:Get*", "servicediscovery:List*"] :
    contains(flatten([for p in aws_iam_role_policy.plan : [for s in jsondecode(p.policy).Statement : s.Action]]), a)])
    error_message = "plan needs to refresh the namespace"
  }
  assert {
    condition = anytrue([for s in flatten([for p in aws_iam_role_policy.apply : jsondecode(p.policy).Statement]) :
    contains(flatten([s.Action]), "servicediscovery:CreateHttpNamespace") && try(s.Condition.StringEquals["aws:RequestTag/Project"], "") == "meetlab-v2"])
    error_message = "namespaces are created only with our tag (their ARNs are random ids)"
  }
}

run "the_boundary_allows_ecs_exec_channels" {
  command = plan

  assert {
    condition = alltrue([for a in ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"] :
    contains(flatten([for s in jsondecode(aws_iam_policy.boundary.policy).Statement : s.Action]), a)])
    error_message = "ECS Exec needs these four in the boundary"
  }
}

# The CI acceptance job (infra-v2.yml) runs live tests against staging after each
# deploy. Its role reads only what the test needs and acts only on staging tasks.
run "acceptance_role_is_staging_only_and_least_privilege" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.acceptance.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == ["repo:wwbp/MeetLab:environment:staging"]
    error_message = "assumable only from the staging environment"
  }
  assert {
    condition = toset(flatten([for s in jsondecode(aws_iam_role_policy.acceptance.policy).Statement : s.Resource if contains(flatten([s.Action]), "ssm:GetParameter")])) == toset([
      for n in ["CONSOLE_PASSWORD", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"] : "arn:aws:ssm:us-east-1:123456789012:parameter/meetlab-v2/staging/${n}"
    ])
    error_message = "reads exactly the four secrets the acceptance test uses"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.acceptance.policy).Statement :
      alltrue([for r in flatten([s.Resource]) : strcontains(r, "meetlab-v2-staging") || strcontains(r, "/meetlab-v2/staging") || r == "*"])
    ])
    error_message = "every resource is staging"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.acceptance.policy).Statement :
      alltrue([for a in flatten([s.Action]) : contains([
        "ssm:GetParameter", "kms:Decrypt", "ecs:ListTasks", "ecs:DescribeTasks", "ecs:StopTask", "ecs:ExecuteCommand", "logs:FilterLogEvents",
        "iam:SimulatePrincipalPolicy",
    ], a)])])
    error_message = "only the actions the acceptance test performs"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.acceptance.policy).Statement :
    length(try(s.Condition, {})) > 0 if contains(flatten([s.Resource]), "*")])
    error_message = "any statement on * must be narrowed by a condition"
  }
}

run "acceptance_can_check_the_permission_contract_of_staging_roles_only" {
  command = plan

  assert {
    condition = anytrue([for s in jsondecode(aws_iam_role_policy.acceptance.policy).Statement :
      contains(flatten([s.Action]), "iam:SimulatePrincipalPolicy") && flatten([s.Resource]) == ["arn:aws:iam::123456789012:role/meetlab-v2-staging-*"]
    ])
    error_message = "permission_contract.py simulates meetlab-v2-staging-* roles, and only those"
  }
}
