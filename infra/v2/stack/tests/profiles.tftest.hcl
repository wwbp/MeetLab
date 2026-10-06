# Offline: mock provider, no AWS. Each environment's sizes are a profile in code
# (profiles.tf), so `env = prod` alone is production. The load-test switches still
# override any of them for one run.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "test"
}

# The user's minimum (2026-10-06): production holds 20 rooms at any time, with nobody
# pressing Prepare for study. Numbers from the load tests (docs/v2-infrastructure.md).
run "production_holds_20_rooms_unprepared_and_grows_to_100" {
  command = plan

  variables {
    env             = "prod"
    hostname_suffix = "-v2"
  }

  assert {
    condition     = aws_autoscaling_group.bots.min_size * 3 >= 20
    error_message = "bot machines always warm for 20 rooms (3 sessions each, BOTS_PER_INSTANCE)"
  }
  assert {
    condition     = aws_autoscaling_group.bots.max_size * 3 >= 100
    error_message = "Prepare for study can grow the pool to 100 rooms (the spike's size)"
  }
  assert {
    condition     = contains(["c6i.large", "c6i.xlarge"], aws_launch_template.livekit.instance_type)
    error_message = "LiveKit: c6i.large ran 60 rooms at 57% CPU (L6), c6i.xlarge 100 at 51% (spike)"
  }
  assert {
    condition     = aws_db_instance.this.multi_az && aws_db_instance.this.instance_class == "db.t4g.medium"
    error_message = "the database across two zones (user's decision); 100 rooms used 135 connections, medium allows ~400"
  }
  assert {
    condition     = local.stt_nim_enabled && length(local.model_services) == 0
    error_message = "like v1: Parakeet NIM always on, OpenAI and ElevenLabs (no self-hosted model GPUs)"
  }
  assert {
    condition     = local.egress_count >= 1
    error_message = "every session is recorded: a recording machine is always up (sized for 20 rooms once measured)"
  }
}

run "staging_keeps_its_small_sizes" {
  command = plan

  assert {
    condition = alltrue([
      aws_autoscaling_group.bots.min_size == 0, aws_autoscaling_group.bots.max_size == 2,
      !aws_db_instance.this.multi_az, aws_db_instance.this.instance_class == "db.t4g.small",
      !local.stt_nim_enabled, length(local.model_services) == 0, local.egress_count == 0,
    ])
    error_message = "staging between test runs: nothing warm, the smallest sizes"
  }
}

run "a_load_test_switch_still_overrides_the_profile" {
  command = plan

  variables {
    bot_pool_max      = 40
    db_instance_class = "db.t3.medium"
  }

  assert {
    condition     = aws_autoscaling_group.bots.max_size == 40 && aws_db_instance.this.instance_class == "db.t3.medium"
    error_message = "the session switches (load tests) win over the profile"
  }
}

# Recordings are study data: an overwrite or delete keeps the earlier version.
run "recordings_are_versioned" {
  command = plan

  assert {
    condition     = one(aws_s3_bucket_versioning.media.versioning_configuration).status == "Enabled"
    error_message = "the media bucket keeps earlier versions"
  }
}
