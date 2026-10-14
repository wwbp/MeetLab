# Offline: mock provider, no AWS. Staging's own Parakeet NIM (the GPU speech-to-text
# v1 runs on a g6): spot, 0 to 1 instances, and switched off unless a test needs it.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "with_the_stt_nim_off_bots_use_deepgram" {
  command = apply

  variables {
    stt_cpu_enabled = false # stated, not assumed: a test session may switch it on
    stt_nim_enabled = false # the default is the on/off switch (variables.tf)
  }

  assert {
    condition     = length(aws_ecs_service.stt_nim) == 0 && length(aws_lb.stt_nim) == 0
    error_message = "off by default: no NIM service, no load balancer ($16/month)"
  }
  assert {
    condition     = aws_autoscaling_group.stt_nim.min_size == 0 && aws_autoscaling_group.stt_nim.max_size == 1
    error_message = "the GPU group is 0 instances unless the NIM runs, and never more than one on staging"
  }
  assert {
    condition = (
      contains(local.bot_environment, { name = "STT_MODEL_OVERRIDE", value = "nova-3-general" }) &&
      length([for e in local.bot_environment : e if e.name == "NEMOTRON_STT_URL"]) == 0
    )
    error_message = "with the NIM off, bots transcribe with Deepgram"
  }
}

run "switched_on_it_runs_on_one_on_demand_gpu_behind_a_private_load_balancer" {
  command = apply

  variables {
    stt_nim_enabled = true
  }

  assert {
    condition     = aws_launch_template.stt_nim.instance_type == "g6.xlarge" && length(aws_autoscaling_group.stt_nim.mixed_instances_policy) == 0
    error_message = "on-demand g6.xlarge: the smallest GPU that fits Parakeet (13.75 GB; NVIDIA's support matrix), and spot was unobtainable (placement score 1/10, 2026-10-02)"
  }
  assert {
    condition     = strcontains(data.aws_ssm_parameter.ecs_gpu_ami.name, "/gpu/") && one(aws_launch_template.stt_nim.metadata_options).http_tokens == "required"
    error_message = "ECS GPU AMI (NVIDIA driver + container toolkit), IMDSv2"
  }
  assert {
    condition     = aws_autoscaling_group.stt_nim.protect_from_scale_in && contains(aws_ecs_cluster_capacity_providers.this.capacity_providers, aws_ecs_capacity_provider.stt_nim.name)
    error_message = "its own capacity provider; ECS scales it, and never in under a running NIM"
  }
  assert {
    condition     = aws_lb.stt_nim[0].internal && aws_lb.stt_nim[0].load_balancer_type == "network" && aws_lb_listener.stt_nim[0].port == 9000
    error_message = "internal network load balancer on :9000: a stable address for bot tasks, which can't use Service Connect"
  }
  assert {
    condition = (
      [for r in aws_security_group.stt_nim_lb.ingress : [r.from_port, r.to_port, r.security_groups, try(length(r.cidr_blocks), 0)]] == [[9000, 9000, toset([aws_security_group.app.id]), 0]] &&
      [for r in aws_security_group.stt_nim.ingress : [r.from_port, r.to_port, r.security_groups, try(length(r.cidr_blocks), 0)]] == [[9000, 9000, toset([aws_security_group.stt_nim_lb.id]), 0]]
    )
    error_message = "only our app tasks reach the load balancer, and only it reaches the NIM, on :9000 alone"
  }
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[0].resourceRequirements == [{ type = "GPU", value = "1" }] &&
      startswith(jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[0].image, "nvcr.io/nim/nvidia/parakeet-0.6b-tdt:")
    )
    error_message = "the same NIM image as v1, on the GPU"
  }
  assert {
    condition = (
      strcontains(jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[0].repositoryCredentials.credentialsParameter, ":secret:meetlab-v2/staging/ngc") &&
      alltrue([for s in jsondecode(aws_ecs_task_definition.stt_nim.container_definitions)[0].secrets : strcontains(s.valueFrom, ":secret:meetlab-v2/staging/ngc")])
    )
    error_message = "the NGC key (image pull and model download) comes from the secret a person stores; never in Terraform"
  }
  assert {
    condition = (
      contains(local.bot_environment, { name = "NEMOTRON_STT_URL", value = "http://${aws_lb.stt_nim[0].dns_name}:9000" }) &&
      contains(local.bot_environment, { name = "STT_MODEL_OVERRIDE", value = "parakeet-tdt-0.6b-v2" })
    )
    error_message = "with the NIM on, bots transcribe with it, as v1 does"
  }
  assert {
    condition     = aws_ecs_service.stt_nim[0].desired_count == 1 && aws_ecs_service.stt_nim[0].health_check_grace_period_seconds >= 1800
    error_message = "one NIM; its first boot builds the model (~20 min, v1 docs), so health checks wait"
  }
}
