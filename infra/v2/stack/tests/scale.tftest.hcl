# Offline: mock provider, no AWS. Headroom for big load tests (B3, 100 rooms): three
# switches raised only for a test session, so staging's always-on cost is unchanged.
# The 60-room run's limits: LiveKit 57% CPU, 101 of ~180 database connections, the
# voice's first audio 116 -> 260 ms (2026-10-04).

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "between_sessions_staging_stays_small" {
  command = apply

  variables { # the defaults are the switches (variables.tf); a session raises them by PR
    livekit_self_hosted   = true
    model_services        = ["llm", "tts"]
    livekit_instance_type = "c6i.large"
    db_instance_class     = "db.t4g.small"
    tts_replicas          = 1
  }

  assert {
    condition     = aws_launch_template.livekit.instance_type == "c6i.large" && aws_db_instance.this.instance_class == "db.t4g.small"
    error_message = "LiveKit c6i.large and the database db.t4g.small unless a test raises them"
  }
  assert {
    condition     = aws_autoscaling_group.model["tts"].max_size == 1 && aws_ecs_service.model["tts"].desired_count == 1
    error_message = "one voice GPU unless a test asks for more"
  }
  assert {
    condition     = aws_db_instance.this.apply_immediately
    error_message = "a database size change applies when merged, not in next week's maintenance window"
  }
}

run "a_load_test_session_raises_them" {
  command = apply

  variables {
    livekit_self_hosted   = true
    model_services        = ["llm", "tts"]
    livekit_instance_type = "c6i.xlarge"
    db_instance_class     = "db.t4g.medium"
    tts_replicas          = 2
  }

  assert {
    condition     = aws_launch_template.livekit.instance_type == "c6i.xlarge" && aws_db_instance.this.instance_class == "db.t4g.medium"
    error_message = "a bigger LiveKit machine and database for the session"
  }
  assert {
    condition = (
      one(aws_autoscaling_group.livekit.instance_refresh).strategy == "Rolling" &&
      one(one(aws_autoscaling_group.livekit.instance_refresh).preferences).scale_in_protected_instances == "Refresh" &&
      one(one(aws_autoscaling_group.livekit.instance_refresh).preferences).min_healthy_percentage == 0
    )
    error_message = "a new machine type replaces the running LiveKit machine (protected from scale-in), not only machines launched later"
  }
  assert {
    condition = (
      aws_autoscaling_group.model["tts"].max_size == 2 && aws_ecs_service.model["tts"].desired_count == 2 &&
      aws_autoscaling_group.model["llm"].max_size == 1 && aws_ecs_service.model["llm"].desired_count == 1
    )
    error_message = "two voice GPUs behind the voice's load balancer (one task per machine: its host port is fixed); the LLM stays one"
  }
}
