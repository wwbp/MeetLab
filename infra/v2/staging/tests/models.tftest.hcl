# Offline: mock provider, no AWS. Our own models (load-test readiness L3): Qwen on vLLM and
# Kokoro speech, each its own GPU service, switched on by model_services; off by default.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "switched_off_bots_use_the_vendors" {
  command = apply

  variables {
    model_services = [] # the default is the switch (variables.tf)
    tts_replicas   = 1
  }

  assert {
    condition     = length(aws_ecs_service.model) == 0 && length(aws_lb.model) == 0
    error_message = "off by default: no GPU service, no load balancer"
  }
  assert {
    condition     = length([for e in local.bot_environment : e if contains(["SELFHOSTED_LLM_URL", "SELFHOSTED_LLM_MODEL", "KOKORO_TTS_URL"], e.name)]) == 0
    error_message = "with our models off, bots know only the vendors"
  }
  assert {
    condition     = alltrue([for g in aws_autoscaling_group.model : g.min_size == 0 && g.max_size == 1])
    error_message = "each GPU group is 0 machines unless its model runs, never more than one on staging"
  }
}

run "switched_on_each_model_runs_on_its_own_gpu_behind_a_private_load_balancer" {
  command = apply

  variables {
    model_services = ["llm", "tts"]
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.model["llm"].container_definitions)[0].image == "vllm/vllm-openai:v0.30.0" &&
      contains(jsondecode(aws_ecs_task_definition.model["llm"].container_definitions)[0].command, "Qwen/Qwen2.5-7B-Instruct") &&
      jsondecode(aws_ecs_task_definition.model["tts"].container_definitions)[0].image == "ghcr.io/remsky/kokoro-fastapi-gpu:v0.9.0"
    )
    error_message = "Qwen2.5-7B-Instruct on vLLM, Kokoro on Kokoro-FastAPI, both pinned"
  }
  assert {
    condition = (
      contains(jsondecode(aws_ecs_task_definition.model["llm"].container_definitions)[0].command, "--quantization") &&
      contains(jsondecode(aws_ecs_task_definition.model["llm"].container_definitions)[0].command, "fp8")
    )
    error_message = "8-bit weights: an L4's 300 GB/s caps 16-bit Qwen-7B near 20 tokens/s, and the first sentence waited 653 ms p50 (load test 2026-10-03)"
  }
  assert {
    condition = alltrue([for k, td in aws_ecs_task_definition.model :
    jsondecode(td.container_definitions)[0].resourceRequirements == [{ type = "GPU", value = "1" }]])
    error_message = "each on its own GPU"
  }
  assert {
    condition = alltrue([for k, lt in aws_launch_template.model :
    lt.instance_type == "g6.xlarge" && lt.image_id == data.aws_ssm_parameter.ecs_gpu_ami.value])
    error_message = "g6.xlarge (one L4, 24 GB: Qwen 7B in bf16 is ~15 GB) on the ECS GPU AMI"
  }
  assert {
    condition = (
      aws_lb.model["llm"].internal && aws_lb.model["llm"].load_balancer_type == "network" &&
      aws_lb_listener.model["llm"].port == 8000 && aws_lb_listener.model["tts"].port == 8880
    )
    error_message = "internal network load balancers: a stable address for bot tasks, which can't use Service Connect"
  }
  assert {
    condition = alltrue([for k, sg in aws_security_group.model_lb :
      [for r in sg.ingress : [r.from_port, r.security_groups, try(length(r.cidr_blocks), 0)]] == [[local.models[k].port, toset([aws_security_group.app.id]), 0]] &&
      [for r in aws_security_group.model[k].ingress : [r.from_port, r.security_groups, try(length(r.cidr_blocks), 0)]] == [[local.models[k].port, toset([sg.id]), 0]]
    ])
    error_message = "only our app tasks reach a load balancer, and only it reaches the model, on the model's port alone"
  }
  assert {
    condition = (
      contains(local.bot_environment, { name = "SELFHOSTED_LLM_URL", value = "http://${aws_lb.model["llm"].dns_name}:8000/v1" }) &&
      contains(local.bot_environment, { name = "SELFHOSTED_LLM_MODEL", value = "Qwen/Qwen2.5-7B-Instruct" }) &&
      contains(local.bot_environment, { name = "KOKORO_TTS_URL", value = "http://${aws_lb.model["tts"].dns_name}:8880/v1" })
    )
    error_message = "bots learn where our models are; a room's config chooses them (bot.py)"
  }
  assert {
    condition     = alltrue([for k, s in aws_ecs_service.model : contains(aws_ecs_cluster_capacity_providers.this.capacity_providers, one(s.capacity_provider_strategy).capacity_provider)])
    error_message = "each on its own capacity provider, registered with the cluster"
  }
}

run "one_alone" {
  command = apply

  variables {
    model_services = ["tts"]
  }

  assert {
    condition     = keys(aws_ecs_service.model) == ["tts"] && length([for e in local.bot_environment : e if e.name == "SELFHOSTED_LLM_URL"]) == 0
    error_message = "each switches on alone: Kokoro without paying for the LLM's GPU"
  }
}

# One machine each (max_size 1): a rolling deploy starts the new task before stopping the
# old, which can never be placed, so the deploy waits forever (FP8, 2026-10-03). Stop
# first, then start: minutes of downtime on staging instead of a stuck deploy.
run "single_machine_services_replace_their_task_instead_of_rolling" {
  command = apply

  variables {
    model_services  = ["llm", "tts"]
    stt_nim_enabled = true
  }

  assert {
    condition = alltrue([for s in concat(values(aws_ecs_service.model), aws_ecs_service.stt_nim, aws_ecs_service.livekit) :
    s.deployment_minimum_healthy_percent == 0 && s.deployment_maximum_percent == 100])
    error_message = "a one-machine service stops its old task before starting the new one"
  }
  assert {
    condition = alltrue([for s in concat(values(aws_ecs_service.model), aws_ecs_service.stt_nim, aws_ecs_service.livekit) :
    s.availability_zone_rebalancing == "DISABLED"])
    error_message = "no zone rebalancing: ECS refuses it with maximum 100% (#143's apply, 2026-10-03), it also starts before it stops, and one task has nothing to balance"
  }
  assert {
    condition = alltrue([for s in concat(values(aws_ecs_service.model), aws_ecs_service.stt_nim, aws_ecs_service.livekit) :
    one(s.deployment_circuit_breaker).enable && one(s.deployment_circuit_breaker).rollback])
    error_message = "a deploy that cannot start ends and rolls back, instead of retrying every 30 min for ever"
  }
}

# Stop-first makes every change a restart: the model must come back from the machine's
# disk in minutes, not download (Qwen, ~15 GB) or rebuild (the NIM, ~20 min) again.
# A Docker volume ECS creates on first use and keeps after the task stops, filled from the
# image's own directory (ownership included, so the container's user can write it).
run "models_restart_from_the_machines_disk" {
  command = apply

  variables {
    model_services  = ["llm", "tts"]
    stt_nim_enabled = true
  }

  assert {
    condition = alltrue([for c in [
      { td = aws_ecs_task_definition.model["llm"], path = "/root/.cache/huggingface" },
      { td = aws_ecs_task_definition.stt_nim, path = "/opt/nim/.cache" },
      ] : anytrue([for v in c.td.volume : v.name == one(jsondecode(c.td.container_definitions)[0].mountPoints).sourceVolume &&
      one(v.docker_volume_configuration).scope == "shared" && one(v.docker_volume_configuration).autoprovision]) &&
    one(jsondecode(c.td.container_definitions)[0].mountPoints).containerPath == c.path])
    error_message = "Qwen's weights and the NIM's built model stay on the machine across restarts"
  }
}

# The NIM runs as a non-root user and could not write a fresh volume ("Permission denied
# (os error 13)", 3 failed starts, rolled back, 2026-10-03). NVIDIA: the cache must be made
# writable (chmod 777). An init container does it before the NIM starts.
run "the_nim_can_write_its_cache" {
  command = apply

  variables {
    stt_nim_enabled = true
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[1].name == "cache-permissions" &&
      !jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[1].essential &&
      strcontains(join(" ", jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[1].command), "chmod 777 /cache") &&
      one(jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[1].mountPoints) == { sourceVolume = "stt-nim-cache", containerPath = "/cache" } &&
      jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[0].dependsOn == [{ containerName = "cache-permissions", condition = "SUCCESS" }]
    )
    error_message = "a one-shot container opens the cache volume before the NIM starts, and the NIM waits for it to succeed"
  }
}
