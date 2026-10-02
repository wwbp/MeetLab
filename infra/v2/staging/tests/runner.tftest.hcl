# Offline: mock provider, no AWS.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "the_runner_is_private" {
  command = apply

  assert {
    condition     = length(aws_ecs_service.runner.load_balancer) == 0
    error_message = "agent-runner has no public entry; meet proxies the console and bot API to it"
  }
  assert {
    condition     = one(one(aws_ecs_service.runner.service_connect_configuration).service).discovery_name == "agent-runner"
    error_message = "meet finds the runner by name through Service Connect"
  }
}

run "meet_reaches_the_runner_by_name" {
  command = apply

  assert {
    condition     = one(aws_ecs_service.meet.service_connect_configuration).enabled
    error_message = "meet must join the Service Connect namespace as a client"
  }
  assert {
    condition = contains([for e in jsondecode(aws_ecs_task_definition.meet_app.container_definitions)[0].environment : e.value if e.name == "BOT_RUNNER_URL"],
    "http://agent-runner:7860/")
    error_message = "BOT_RUNNER_URL points at the Service Connect name"
  }
}

run "secrets_are_injected_never_written_into_task_definitions" {
  command = apply

  assert {
    condition = alltrue([for td in [aws_ecs_task_definition.meet_app, aws_ecs_task_definition.runner_app] :
    alltrue([for e in jsondecode(td.container_definitions)[0].environment : !contains(["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "CONSOLE_PASSWORD", "BOT_RUNNER_SECRET", "DB_PASSWORD", "DATABASE_URL"], e.name)])])
    error_message = "a secret is a plain environment value in a task definition"
  }
  assert {
    condition = alltrue(flatten([for td in [aws_ecs_task_definition.meet_app, aws_ecs_task_definition.runner_app] :
      [for s in jsondecode(td.container_definitions)[0].secrets :
    strcontains(s.valueFrom, ":parameter/meetlab-v2/staging/") || startswith(s.valueFrom, aws_db_instance.this.master_user_secret[0].secret_arn)]]))
    error_message = "secrets come from /meetlab-v2/staging/* or the RDS-managed secret only"
  }
}

run "the_runner_reaches_the_database_by_parts" {
  command = apply

  assert {
    condition = contains([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : e.value if e.name == "DB_HOST"],
    aws_db_instance.this.address)
    error_message = "DB_HOST is the RDS endpoint (db/url.py builds the URL)"
  }
  assert {
    condition = contains([for s in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].secrets : s.valueFrom if s.name == "DB_PASSWORD"],
    "${aws_db_instance.this.master_user_secret[0].secret_arn}:password::")
    error_message = "DB_PASSWORD is the password key of the RDS-managed secret"
  }
}

run "the_execution_role_reads_only_staging_secrets" {
  command = apply

  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.execution_secrets.policy).Statement :
      alltrue([for r in flatten([s.Resource]) : strcontains(r, ":parameter/meetlab-v2/staging/") || r == aws_db_instance.this.master_user_secret[0].secret_arn])
    if s.Action != "kms:Decrypt"])
    error_message = "the execution role reads staging parameters and the DB secret, nothing else"
  }
}

run "runner_deploys_safely" {
  command = apply

  assert {
    condition     = aws_ecs_service.runner.wait_for_steady_state && one(aws_ecs_service.runner.deployment_circuit_breaker).rollback
    error_message = "a runner that never becomes healthy fails the apply and rolls back"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].image == "${aws_ecr_repository.this["agent-runner"].repository_url}:0123abc"
    error_message = "the runner runs the SHA the pipeline just pushed"
  }
}

# ecs:DeregisterTaskDefinition can't be scoped to a resource, so granting it would let
# CI deregister any project's task definitions in the shared account. Old revisions
# stay registered instead (ECS allows a million per family).
run "task_definition_revisions_are_never_deregistered" {
  command = apply

  assert {
    condition     = aws_ecs_task_definition.meet_app.skip_destroy && aws_ecs_task_definition.runner_app.skip_destroy
    error_message = "a task definition replace would call DeregisterTaskDefinition, which CI must not hold"
  }
}

# The DB default STT is the Parakeet NIM. Staging has no NIM yet, and without one the
# bot posts to a bare /v1/audio/transcriptions, every transcription fails and it stays
# silent (sanity run, 2026-09-30). Until the staging NIM lands, force Deepgram.
run "staging_bots_can_hear_without_a_nim" {
  command = apply

  variables {
    stt_nim_enabled = false # the default is the on/off switch (variables.tf)
  }

  assert {
    condition = contains([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : e.value if e.name == "STT_MODEL_OVERRIDE"],
    "nova-3-general")
    error_message = "no NIM on staging: STT must be overridden to Deepgram"
  }
}

# heartbeat.py fails a session silent for 30 s; the reconcile loop must look often
# enough for that to mean ~30 s, not the 120 s default.
run "silent_bots_are_noticed_within_seconds" {
  command = apply

  assert {
    condition = contains([for e in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].environment : "${e.name}=${e.value}"],
    "CONVERSATION_RECONCILE_INTERVAL_SECONDS=10")
    error_message = "reconcile every 10 s on staging"
  }
}

# Video egress (8b): the runner sends LiveKit a write-only key with each recording
# request, so the runner, and only the runner, holds it.
run "only_the_runner_gets_the_egress_key" {
  command = apply

  assert {
    condition = alltrue([for n in ["EGRESS_S3_KEY_ID", "EGRESS_S3_KEY_SECRET"] :
    contains([for s in jsondecode(aws_ecs_task_definition.runner_app.container_definitions)[0].secrets : s.valueFrom if s.name == n], "${local.parameters}/${n}")])
    error_message = "the runner requests egress, so it holds the key, from SSM"
  }
  assert {
    condition     = !anytrue([for s in jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].secrets : startswith(s.name, "EGRESS_")])
    error_message = "bots never need the egress key"
  }
}
