# Offline: mock provider, no AWS. Each environment's sizes are a profile in code
# (profiles.tf), so `env = prod` alone is production. The load-test switches still
# override any of them for one run.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "test"
}

# Production is sized to use, not to a peak (user's decision after the cost audit, 2026-10-06:
# v1's busiest month was 31 session-hours). Scheduled studies get their capacity from Prepare
# for study; anything bigger is a profile change, every limit having been measured.
run "production_is_lean_and_scales_on_request" {
  command = plan

  variables {
    env             = "prod"
    hostname_suffix = "-v2"
  }

  assert {
    condition     = aws_autoscaling_group.bots.min_size == 1 && aws_autoscaling_group.bots.max_size * 3 >= 100
    error_message = "one warm bot machine (3 rooms at once, unprepared); Prepare grows the pool to 100 rooms"
  }
  assert {
    condition     = !local.livekit_self_hosted && local.egress_count == 0
    error_message = "media and video recording on LiveKit Cloud (Ship: 100 recordings at once): no LiveKit machine or recorder of our own"
  }
  assert {
    condition     = aws_db_instance.this.multi_az && aws_db_instance.this.instance_class == "db.t4g.small"
    error_message = "a standby in a second zone (no data loss); t4g.small took 165 connections at 102 rooms (B3)"
  }
  assert {
    condition     = !local.stt_nim_enabled && length(local.model_services) == 0
    error_message = "no GPU kept on: speech-to-text GPU only when a study needs it (switch point measured); paid LLM and voice"
  }
}

run "staging_keeps_nothing_warm" {
  command = plan

  assert {
    condition     = aws_autoscaling_group.bots.min_size == 0 && !aws_db_instance.this.multi_az
    error_message = "staging: no always-warm bot machines, one database zone"
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

# Right-sizing bots is a sweep (docs/load-testing.md): a bot's reservation, how many share a
# machine and the machine type are profile values, and the runner packs by the same number.
run "bot_size_and_packing_come_from_the_profile" {
  command = apply

  variables {
    bot_cpu           = 256
    bot_memory        = 640
    bots_per_instance = 6
    bot_instance_type = "c7i.large"
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].cpu == 256 &&
      jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].memoryReservation == 640 &&
      aws_launch_template.bots.instance_type == "c7i.large" &&
      contains(local.runner_environment, { name = "BOTS_PER_INSTANCE", value = "6" })
    )
    error_message = "the sweep sets a bot's size, the machine, and how many the runner packs on one"
  }
}

run "todays_bot_sizes_stay_the_default" {
  command = apply

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].cpu == 512 &&
      aws_launch_template.bots.instance_type == "c6i.large" &&
      contains(local.runner_environment, { name = "BOTS_PER_INSTANCE", value = "3" })
    )
    error_message = "until the sweep says otherwise: 512 units, c6i.large, 3 per machine"
  }
}
