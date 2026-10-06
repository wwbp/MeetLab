# Offline: mock provider, no AWS. Speech-to-text on CPU (stt_cpu.tf, the stt-cpu image):
# Parakeet int8 behind the endpoint bots already use for the GPU NIM. Off: Deepgram. On:
# bots use it. The GPU, when on for a big study, wins (switch point: docs/load-testing.md).

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "off_bots_use_deepgram_and_nothing_runs" {
  command = plan

  assert {
    condition     = length(aws_ecs_service.stt_cpu) == 0 && length(aws_lb.stt_cpu) == 0
    error_message = "off costs nothing"
  }
  assert {
    condition     = contains(local.bot_environment, { name = "STT_MODEL_OVERRIDE", value = "nova-3-general" })
    error_message = "no speech server of ours: Deepgram"
  }
}

run "on_bots_transcribe_on_the_cpu_server_behind_a_private_load_balancer" {
  command = apply

  variables {
    stt_cpu_enabled = true
  }

  assert {
    condition     = length(aws_ecs_service.stt_cpu) == 1 && aws_lb.stt_cpu[0].internal && aws_lb.stt_cpu[0].load_balancer_type == "network"
    error_message = "one service, reached by bots (one-off tasks) through an internal load balancer"
  }
  assert {
    condition = (
      contains(local.bot_environment, { name = "NEMOTRON_STT_URL", value = "http://${aws_lb.stt_cpu[0].dns_name}:8000" }) &&
      contains(local.bot_environment, { name = "STT_MODEL_OVERRIDE", value = "parakeet-tdt-0.6b-v2" })
    )
    error_message = "bots send their speech to the CPU server, as Parakeet"
  }
  assert {
    condition     = one(aws_lb_target_group.stt_cpu[0].health_check).path == "/health"
    error_message = "ready only once the model is loaded"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.stt_cpu.container_definitions)[0].image == "${data.aws_ecr_repository.this["stt-cpu"].repository_url}:0123abc"
    error_message = "the image built from this commit, from the shared repository"
  }
}

run "the_gpu_wins_when_a_study_switches_it_on" {
  command = apply

  variables {
    stt_cpu_enabled = true
    stt_nim_enabled = true
  }

  assert {
    condition     = contains(local.bot_environment, { name = "NEMOTRON_STT_URL", value = "http://${aws_lb.stt_nim[0].dns_name}:9000" })
    error_message = "past the switch point, bots use the GPU"
  }
}

run "production_keeps_the_cpu_server_on_and_no_gpu" {
  command = plan

  variables {
    env             = "prod"
    hostname_suffix = "-v2"
  }

  assert {
    condition     = local.stt_cpu_enabled && !local.stt_nim_enabled
    error_message = "unscheduled sessions need speech-to-text that keeps audio with us, at CPU prices"
  }
}
