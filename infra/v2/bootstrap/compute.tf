# ECS, load balancer, autoscaling, logs, DNS and IAM for infra/v2/staging, plus the
# permissions boundary every CI-created role must carry.

data "aws_route53_zone" "wwbp" {
  name = "wwbp.org"
}

locals {
  account  = data.aws_caller_identity.current.account_id
  arn      = "arn:aws:%s:us-east-1:${data.aws_caller_identity.current.account_id}:%s"
  roles    = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/meetlab-v2-staging-*"
  boundary = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/meetlab-v2-boundary"
  users    = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/meetlab-v2-staging-*"

  compute_read = jsonencode({
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
          local.roles,
          "arn:aws:iam::${local.account}:instance-profile/meetlab-v2-staging-*",
          "arn:aws:iam::aws:policy/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetUser", "iam:GetUserPolicy", "iam:ListUserPolicies"]
        Resource = local.users
      },
      {
        # The ECS-optimized AMI id is a public AWS parameter.
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = "arn:aws:ssm:us-east-1::parameter/aws/service/ecs/*"
      },
    ]
  })

  compute_write = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OwnEcs"
        Effect = "Allow"
        Action = "ecs:*"
        Resource = [for r in [
          "cluster/meetlab-v2-*", "service/meetlab-v2-*/*", "task-definition/meetlab-v2-*:*",
          "capacity-provider/meetlab-v2-*", "task/meetlab-v2-*/*", "container-instance/meetlab-v2-*/*",
        ] : format(local.arn, "ecs", r)]
      },
      {
        # RegisterTaskDefinition has no resource ARN; the request tag scopes it.
        Sid       = "RegisterTaggedTaskDefinitions"
        Effect    = "Allow"
        Action    = ["ecs:RegisterTaskDefinition", "ecs:TagResource"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestTag/Project" = "meetlab-v2" } }
      },
      {
        Sid    = "OwnLoadBalancers"
        Effect = "Allow"
        Action = "elasticloadbalancing:*"
        Resource = [for r in [
          "loadbalancer/app/meetlab-v2-*/*", "targetgroup/meetlab-v2-*/*",
          "listener/app/meetlab-v2-*/*", "listener-rule/app/meetlab-v2-*/*",
          "loadbalancer/net/meetlab-v2-*/*", "listener/net/meetlab-v2-*/*", # the STT NIM's
        ] : format(local.arn, "elasticloadbalancing", r)]
      },
      {
        # Redis for LiveKit and its egress (egress_server.tf); creating a cluster also
        # reads AWS's default parameter group.
        Sid    = "OwnRedis"
        Effect = "Allow"
        Action = "elasticache:*"
        Resource = [for r in ["cluster:meetlab-v2-*", "subnetgroup:meetlab-v2-*", "parametergroup:default.redis7"] :
        format(local.arn, "elasticache", r)]
      },
      {
        Sid      = "OwnAutoScalingGroups"
        Effect   = "Allow"
        Action   = "autoscaling:*"
        Resource = format(local.arn, "autoscaling", "autoScalingGroup:*:autoScalingGroupName/meetlab-v2-*")
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
        Resource = [format(local.arn, "logs", "log-group:/meetlab-v2/*"), format(local.arn, "logs", "log-group:/meetlab-v2/*:*")]
      },
      {
        # Cloud Map ids are random, so our tag is the handle; ECS registers the
        # runner as a service inside the (tagged) namespace.
        Sid       = "CreateTaggedNamespaces"
        Effect    = "Allow"
        Action    = ["servicediscovery:CreateHttpNamespace", "servicediscovery:TagResource"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestTag/Project" = "meetlab-v2" } }
      },
      {
        Sid       = "OwnNamespaces"
        Effect    = "Allow"
        Action    = "servicediscovery:*"
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/Project" = "meetlab-v2" } }
      },
      {
        Sid       = "OneDnsName"
        Effect    = "Allow"
        Action    = "route53:ChangeResourceRecordSets"
        Resource  = data.aws_route53_zone.wwbp.arn
        Condition = { "ForAllValues:StringEquals" = { "route53:ChangeResourceRecordSetsNormalizedRecordNames" = ["meet-staging.wwbp.org", "livekit-staging.wwbp.org", "turn-staging.wwbp.org"] } }
      },
    ]
  })

  iam_write = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CreateRolesOnlyWithTheBoundary"
        Effect    = "Allow"
        Action    = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
        Resource  = local.roles
        Condition = { StringEquals = { "iam:PermissionsBoundary" = local.boundary } }
      },
      {
        # The boundary caps whatever these attach.
        Sid    = "ManageOwnRoles"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
          "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
        ]
        Resource = local.roles
      },
      {
        # The LiveKit egress user (staging egress.tf); a person mints its key.
        Sid       = "CreateUsersOnlyWithTheBoundary"
        Effect    = "Allow"
        Action    = ["iam:CreateUser", "iam:PutUserPermissionsBoundary"]
        Resource  = local.users
        Condition = { StringEquals = { "iam:PermissionsBoundary" = local.boundary } }
      },
      {
        Sid      = "ManageOwnUsers"
        Effect   = "Allow"
        Action   = ["iam:DeleteUser", "iam:TagUser", "iam:UntagUser", "iam:PutUserPolicy", "iam:DeleteUserPolicy"]
        Resource = local.users
      },
      {
        Sid    = "OwnInstanceProfiles"
        Effect = "Allow"
        Action = [
          "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:TagInstanceProfile",
          "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
        ]
        Resource = "arn:aws:iam::${local.account}:instance-profile/meetlab-v2-staging-*"
      },
      {
        Sid       = "PassOwnRoles"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = local.roles
        Condition = { StringEquals = { "iam:PassedToService" = ["ecs-tasks.amazonaws.com", "ec2.amazonaws.com"] } }
      },
      {
        Sid      = "NeverTheCiRolesOrTheBoundary"
        Effect   = "Deny"
        Action   = "iam:*"
        Resource = ["arn:aws:iam::${local.account}:role/meetlab-v2-tf-*", local.boundary]
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
  })
}

# The ceiling for every role infra/v2 creates: run containers, pull our images, write
# logs, read our parameters and secrets, use our buckets, launch bot tasks.
resource "aws_iam_policy" "boundary" {
  name = "meetlab-v2-boundary"
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
        Resource = ["arn:aws:ssm:us-east-1:${local.account}:parameter/meetlab-v2/*", "arn:aws:ssm:us-east-1::parameter/aws/service/*"]
      },
      {
        Sid      = "OwnSecrets"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = ["arn:aws:secretsmanager:us-east-1:${local.account}:secret:rds!*", "arn:aws:secretsmanager:us-east-1:${local.account}:secret:meetlab-v2/*"]
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
        Resource = ["arn:aws:s3:::meetlab-v2-*", "arn:aws:s3:::meetlab-v2-*/*"]
      },
      {
        # Prepare for study (agent-runner capacity.py): warm a bot pool, no other group.
        Sid      = "PrewarmBotPools"
        Effect   = "Allow"
        Action   = ["autoscaling:UpdateAutoScalingGroup", "autoscaling:PutScheduledUpdateGroupAction", "autoscaling:DeleteScheduledAction"]
        Resource = format(local.arn, "autoscaling", "autoScalingGroup:*:autoScalingGroupName/meetlab-v2-*-bots")
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
        Resource = [local.roles]
      },
    ]
  })
}
