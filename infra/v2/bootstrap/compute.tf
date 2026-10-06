# ECS, load balancer, autoscaling, logs, DNS and IAM for infra/v2/stack, plus the
# permissions boundary every CI-created role must carry.

data "aws_route53_zone" "wwbp" {
  name = "wwbp.org"
}

locals {
  account    = data.aws_caller_identity.current.account_id
  arn        = "arn:aws:%s:us-east-1:${data.aws_caller_identity.current.account_id}:%s"
  roles      = { for env, e in local.envs : env => "arn:aws:iam::${local.account}:role/${e.p}-*" }
  boundaries = { for env, e in local.envs : env => "arn:aws:iam::${local.account}:policy/${e.boundary}" }
  users      = { for env, e in local.envs : env => "arn:aws:iam::${local.account}:user/${e.p}-*" }

  compute_read = { for env, e in local.envs : env => jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:Describe*", "ecs:List*", "elasticloadbalancing:Describe*", "autoscaling:Describe*",
          "logs:Describe*", "logs:ListTagsForResource", "acm:DescribeCertificate", "acm:GetCertificate", "acm:ListCertificates",
          "acm:ListTagsForCertificate", "route53:GetHostedZone", "route53:ListHostedZones",
          "route53:ListResourceRecordSets", "route53:GetChange", "route53:ListTagsForResource",
          "servicediscovery:Get*", "servicediscovery:List*",
          "elasticache:Describe*", "elasticache:List*",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole", "iam:GetInstanceProfile", "iam:GetPolicy", "iam:GetPolicyVersion",
        ]
        Resource = [
          local.roles[env],
          "arn:aws:iam::${local.account}:instance-profile/${e.p}-*",
          "arn:aws:iam::aws:policy/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetUser", "iam:GetUserPolicy", "iam:ListUserPolicies"]
        Resource = local.users[env]
      },
      {
        # The ECS-optimized AMI id is a public AWS parameter.
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = "arn:aws:ssm:us-east-1::parameter/aws/service/ecs/*"
      },
    ]
  }) }

  compute_write = { for env, e in local.envs : env => jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OwnEcs"
        Effect = "Allow"
        Action = "ecs:*"
        Resource = [for r in [
          "cluster/${e.p}", "service/${e.p}/*", "task-definition/${e.p}-*:*",
          "capacity-provider/${e.p}-*", "task/${e.p}/*", "container-instance/${e.p}/*",
        ] : format(local.arn, "ecs", r)]
      },
      {
        # RegisterTaskDefinition has no resource ARN; the request tag scopes it.
        Sid       = "RegisterTaggedTaskDefinitions"
        Effect    = "Allow"
        Action    = ["ecs:RegisterTaskDefinition", "ecs:TagResource"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestTag/Project" = "meetlab-v2", "aws:RequestTag/Environment" = env } }
      },
      {
        Sid    = "OwnLoadBalancers"
        Effect = "Allow"
        Action = "elasticloadbalancing:*"
        Resource = [for r in [
          "loadbalancer/app/${e.p}*/*", "targetgroup/${e.p}*/*",
          "listener/app/${e.p}*/*", "listener-rule/app/${e.p}*/*",
          "loadbalancer/net/${e.p}*/*", "listener/net/${e.p}*/*", # the STT NIM's and TURN's
        ] : format(local.arn, "elasticloadbalancing", r)]
      },
      {
        # Redis for LiveKit and its egress (egress_server.tf); creating a cluster also
        # reads AWS's default parameter group.
        Sid    = "OwnRedis"
        Effect = "Allow"
        Action = "elasticache:*"
        Resource = [for r in ["cluster:${e.p}-*", "subnetgroup:${e.p}-*", "parametergroup:default.redis7"] :
        format(local.arn, "elasticache", r)]
      },
      {
        Sid      = "OwnAutoScalingGroups"
        Effect   = "Allow"
        Action   = "autoscaling:*"
        Resource = format(local.arn, "autoscaling", "autoScalingGroup:*:autoScalingGroupName/${e.p}-*")
      },
      {
        # ASGs and capacity checks call RunInstances as us. Every resource but these
        # five must match OwnResources (our tag), including the launch template.
        Sid       = "RunFromOurLaunchTemplates"
        Effect    = "Allow"
        Action    = "ec2:RunInstances"
        Resource  = ["arn:aws:ec2:us-east-1::image/*", format(local.arn, "ec2", "instance/*"), format(local.arn, "ec2", "volume/*"), format(local.arn, "ec2", "network-interface/*"), format(local.arn, "ec2", "spot-instances-request/*")]
        Condition = { ArnLike = { "ec2:LaunchTemplate" = format(local.arn, "ec2", "launch-template/*") } }
      },
      {
        Sid      = "OwnLogGroups"
        Effect   = "Allow"
        Action   = "logs:*"
        Resource = [format(local.arn, "logs", "log-group:/meetlab-v2/${env}/*"), format(local.arn, "logs", "log-group:/meetlab-v2/${env}/*:*")]
      },
      {
        # Cloud Map ids are random, so our tag is the handle; ECS registers the
        # runner as a service inside the (tagged) namespace.
        Sid       = "CreateTaggedNamespaces"
        Effect    = "Allow"
        Action    = ["servicediscovery:CreateHttpNamespace", "servicediscovery:TagResource"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestTag/Project" = "meetlab-v2", "aws:RequestTag/Environment" = env } }
      },
      {
        Sid       = "OwnNamespaces"
        Effect    = "Allow"
        Action    = "servicediscovery:*"
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/Project" = "meetlab-v2", "aws:ResourceTag/Environment" = env } }
      },
      {
        Sid       = "OneDnsName"
        Effect    = "Allow"
        Action    = "route53:ChangeResourceRecordSets"
        Resource  = data.aws_route53_zone.wwbp.arn
        Condition = { "ForAllValues:StringEquals" = { "route53:ChangeResourceRecordSetsNormalizedRecordNames" = e.hostnames } }
      },
    ]
  }) }

  iam_write = { for env, e in local.envs : env => jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CreateRolesOnlyWithTheBoundary"
        Effect    = "Allow"
        Action    = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
        Resource  = local.roles[env]
        Condition = { StringEquals = { "iam:PermissionsBoundary" = local.boundaries[env] } }
      },
      {
        # The boundary caps whatever these attach.
        Sid    = "ManageOwnRoles"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
          "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
        ]
        Resource = local.roles[env]
      },
      {
        # The LiveKit egress user (stack egress.tf); a person mints its key.
        Sid       = "CreateUsersOnlyWithTheBoundary"
        Effect    = "Allow"
        Action    = ["iam:CreateUser", "iam:PutUserPermissionsBoundary"]
        Resource  = local.users[env]
        Condition = { StringEquals = { "iam:PermissionsBoundary" = local.boundaries[env] } }
      },
      {
        Sid      = "ManageOwnUsers"
        Effect   = "Allow"
        Action   = ["iam:DeleteUser", "iam:TagUser", "iam:UntagUser", "iam:PutUserPolicy", "iam:DeleteUserPolicy"]
        Resource = local.users[env]
      },
      {
        Sid    = "OwnInstanceProfiles"
        Effect = "Allow"
        Action = [
          "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:TagInstanceProfile",
          "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
        ]
        Resource = "arn:aws:iam::${local.account}:instance-profile/${e.p}-*"
      },
      {
        Sid       = "PassOwnRoles"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = local.roles[env]
        Condition = { StringEquals = { "iam:PassedToService" = ["ecs-tasks.amazonaws.com", "ec2.amazonaws.com"] } }
      },
      {
        Sid      = "NeverTheCiRolesOrTheBoundary"
        Effect   = "Deny"
        Action   = "iam:*"
        Resource = concat(["arn:aws:iam::${local.account}:role/meetlab-v2-tf-*"], [for e in local.envs : "arn:aws:iam::${local.account}:role/${e.acceptance_role}"], values(local.boundaries))
      },
      {
        Sid      = "NoRoleLosesItsBoundary"
        Effect   = "Deny"
        Action   = "iam:DeleteRolePermissionsBoundary"
        Resource = "*"
      },
      {
        Sid      = "NoKeysNoUserLosesItsBoundary"
        Effect   = "Deny"
        Action   = ["iam:CreateAccessKey", "iam:UpdateAccessKey", "iam:DeleteUserPermissionsBoundary"]
        Resource = "*"
      },
    ]
  }) }
}

# The ceiling for every role an environment's stack creates, one per environment: run containers, pull our images, write
# logs, read our parameters and secrets, use our buckets, launch bot tasks.
resource "aws_iam_policy" "boundary" {
  for_each = local.envs
  name     = each.value.boundary
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunContainers"
        Effect = "Allow"
        Action = [
          "ecs:*", "ec2:DescribeTags", "ec2:DescribeInstances", "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
          "logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup", "cloudwatch:PutMetricData",
        ]
        Resource = "*"
      },
      {
        Sid      = "OwnParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = ["arn:aws:ssm:us-east-1:${local.account}:parameter/meetlab-v2/${each.key}/*", "arn:aws:ssm:us-east-1::parameter/aws/service/*"]
      },
      {
        Sid      = "OwnSecrets"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = ["arn:aws:secretsmanager:us-east-1:${local.account}:secret:rds!*", "arn:aws:secretsmanager:us-east-1:${local.account}:secret:meetlab-v2/${each.key}/*"]
      },
      {
        Sid       = "DecryptThem"
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = ["ssm.us-east-1.amazonaws.com", "secretsmanager.us-east-1.amazonaws.com"] } }
      },
      {
        Sid      = "OwnBuckets"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${each.value.p}-*", "arn:aws:s3:::${each.value.p}-*/*"]
      },
      {
        # Prepare for study (agent-runner capacity.py): warm a bot pool, no other group.
        Sid      = "PrewarmBotPools"
        Effect   = "Allow"
        Action   = ["autoscaling:UpdateAutoScalingGroup", "autoscaling:PutScheduledUpdateGroupAction", "autoscaling:DeleteScheduledAction"]
        Resource = format(local.arn, "autoscaling", "autoScalingGroup:*:autoScalingGroupName/${each.value.p}-bots")
      },
      {
        Sid      = "ReadAutoScaling"
        Effect   = "Allow"
        Action   = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:DescribeScheduledActions"]
        Resource = "*"
      },
      {
        # ECS Exec into bot tasks: the Session Manager channels, nothing else of SSM.
        Sid      = "EcsExecChannels"
        Effect   = "Allow"
        Action   = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
        Resource = "*"
      },
      {
        Sid      = "LaunchBots"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [local.roles[each.key]]
      },
    ]
  })
}

moved {
  from = aws_iam_policy.boundary
  to   = aws_iam_policy.boundary["staging"]
}
