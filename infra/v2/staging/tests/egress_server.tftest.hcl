# Offline: mock provider, no AWS. Video recording on our own LiveKit (the user's decision,
# 2026-10-05): LiveKit's egress server records rooms to our S3 with its own task role (no key
# anywhere), reached by LiveKit through Redis. Off (0 machines) between studies and tests.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag           = "0123abc"
  livekit_self_hosted = true
}

run "livekit_and_egress_meet_through_redis" {
  command = apply

  assert {
    condition     = strcontains(join("", jsondecode(aws_ecs_task_definition.livekit.container_definitions)[0].command), "redis:\\n  address: ${aws_elasticache_cluster.redis[0].cache_nodes[0].address}:6379")
    error_message = "LiveKit hands recordings to egress through Redis (no Redis: every egress call fails)"
  }
  assert {
    condition = (
      aws_elasticache_cluster.redis[0].node_type == "cache.t4g.micro" &&
      # Named, not left to AWS: unnamed, AWS checks parametergroup:* and the apply role may only
      # use default.redis7 (bootstrap). #182-#184's applies were refused for exactly that.
      aws_elasticache_cluster.redis[0].parameter_group_name == "default.redis7" &&
      anytrue([for r in aws_security_group.redis.ingress : r.from_port == 6379 &&
      toset(coalesce(r.security_groups, [])) == toset([aws_security_group.livekit.id, aws_security_group.egress.id])])
    )
    error_message = "the smallest Redis, reachable only from LiveKit and egress"
  }
}

run "egress_uploads_with_its_own_role_and_no_key" {
  command = apply

  variables {
    egress_count = 1
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.egress.container_definitions)[0].image == "livekit/egress:v1.13.0" &&
      contains(jsondecode(aws_ecs_task_definition.egress.container_definitions)[0].linuxParameters.capabilities.add, "SYS_ADMIN")
    )
    error_message = "the egress version local development runs; Chrome needs SYS_ADMIN (LiveKit's docs)"
  }
  assert {
    condition = (
      strcontains(join("", jsondecode(aws_ecs_task_definition.egress.container_definitions)[0].command), "bucket: ${aws_s3_bucket.media.bucket}") &&
      !strcontains(join("", jsondecode(aws_ecs_task_definition.egress.container_definitions)[0].command), "access_key")
    )
    error_message = "egress's own config names the bucket and holds no key: it uploads with its task role"
  }
  assert {
    condition = alltrue([for st in jsondecode(aws_iam_role_policy.egress_recordings.policy).Statement :
      toset(st.Action) == toset(["s3:PutObject", "s3:AbortMultipartUpload"]) &&
    st.Resource == "${aws_s3_bucket.media.arn}/recordings/*"])
    error_message = "the egress role can only add recordings"
  }
  assert {
    condition     = aws_ecs_service.egress[0].desired_count == 1 && output.livekit.video
    error_message = "switched on: one egress machine, and the live tests record video"
  }
  assert {
    condition     = !anytrue([for s in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].secrets : startswith(s.name, "EGRESS_")])
    error_message = "with our own egress the runner holds no egress key"
  }
}

run "off_between_studies" {
  command = apply

  assert {
    condition     = aws_ecs_service.egress[0].desired_count == 0 && !output.livekit.video
    error_message = "no egress machine unless switched on; the live tests then skip video"
  }
}
