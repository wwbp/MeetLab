# Offline: mock provider, no AWS. One stack, two environments: `env` names everything, so
# staging keeps today's names exactly (no resource replaced) and production gets its own.
# (The mock account is 123456789012; staging's real bucket ends in 848180123498.)

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "test"
}

run "staging_keeps_todays_names" {
  command = plan

  assert {
    condition = alltrue([
      aws_ecs_cluster.this.name == "meetlab-v2-staging",
      aws_s3_bucket.media.bucket == "meetlab-v2-staging-media-123456789012",
      aws_db_instance.this.identifier == "meetlab-v2-staging",
      aws_cloudwatch_log_group.meet.name == "/meetlab-v2/staging/meet",
      aws_route53_record.meet.name == "meet-staging.wwbp.org",
      aws_route53_record.livekit[0].name == "livekit-staging.wwbp.org",
      aws_route53_record.turn[0].name == "turn-staging.wwbp.org",
      local.parameters == "arn:aws:ssm:us-east-1:123456789012:parameter/meetlab-v2/staging",
      local.boundary == "arn:aws:iam::123456789012:policy/meetlab-v2-boundary",
    ])
    error_message = "staging's names must not change: a renamed resource is a replaced one"
  }
}

run "production_has_its_own_names" {
  command = plan

  variables {
    env             = "prod"
    hostname_suffix = "-v2"
  }

  assert {
    condition = alltrue([
      aws_ecs_cluster.this.name == "meetlab-v2-prod",
      aws_s3_bucket.media.bucket == "meetlab-v2-prod-media-123456789012",
      aws_db_instance.this.identifier == "meetlab-v2-prod",
      aws_cloudwatch_log_group.meet.name == "/meetlab-v2/prod/meet",
      aws_route53_record.meet.name == "meet-v2.wwbp.org",
      aws_route53_record.livekit[0].name == "livekit-v2.wwbp.org",
      aws_route53_record.turn[0].name == "turn-v2.wwbp.org",
      local.parameters == "arn:aws:ssm:us-east-1:123456789012:parameter/meetlab-v2/prod",
      local.boundary == "arn:aws:iam::123456789012:policy/meetlab-v2-prod-boundary",
    ])
    error_message = "production must share no name with staging"
  }
}

run "only_staging_and_prod" {
  command = plan

  variables {
    env = "dev"
  }

  expect_failures = [var.env]
}
