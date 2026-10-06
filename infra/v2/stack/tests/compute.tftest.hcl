# Offline: mock provider, no AWS.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "every_role_is_capped_by_the_boundary" {
  command = apply

  assert {
    condition = alltrue([for r in [aws_iam_role.instance, aws_iam_role.execution] :
    r.permissions_boundary == "arn:aws:iam::123456789012:policy/meetlab-v2-boundary"])
    error_message = "bootstrap only lets CI create roles that carry meetlab-v2-boundary"
  }
  assert {
    condition     = alltrue([for r in [aws_iam_role.instance, aws_iam_role.execution] : startswith(r.name, "meetlab-v2-staging-")])
    error_message = "bootstrap only lets CI manage meetlab-v2-staging-* roles"
  }
}

run "instances_are_private_and_require_imdsv2" {
  command = apply

  assert {
    condition     = one(aws_launch_template.ecs.metadata_options).http_tokens == "required"
    error_message = "IMDSv2 only: a container must not read the instance role via a plain GET"
  }
  assert {
    condition     = toset(aws_autoscaling_group.ecs.vpc_zone_identifier) == toset([for s in aws_subnet.private : s.id])
    error_message = "instances live in the private subnets"
  }
}

run "scale_in_never_kills_a_running_task" {
  command = apply

  assert {
    condition     = aws_autoscaling_group.ecs.protect_from_scale_in
    error_message = "ECS managed termination protection needs instance scale-in protection"
  }
  assert {
    condition     = one(aws_ecs_capacity_provider.ec2.auto_scaling_group_provider).managed_termination_protection == "ENABLED"
    error_message = "a scale-in must wait until the instance's tasks have stopped"
  }
}

run "the_edge_is_https_only" {
  command = apply

  assert {
    condition     = one(aws_lb_listener.http.default_action).type == "redirect" && one(one(aws_lb_listener.http.default_action).redirect).protocol == "HTTPS"
    error_message = "port 80 only redirects to HTTPS"
  }
  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && startswith(aws_lb_listener.https.ssl_policy, "ELBSecurityPolicy-TLS13")
    error_message = "HTTPS with a TLS 1.3 policy"
  }
  assert {
    condition     = toset(aws_lb.this.subnets) == toset([for s in aws_subnet.public : s.id])
    error_message = "the load balancer lives in the public subnets"
  }
}

# Bridge mode: Service Connect proxies talk to each other on host ports, across
# instances (meet -> agent-runner found them on different hosts on 2026-09-30, and
# every call hung). So app tasks accept each other and the ALB, and nothing else.
run "only_the_load_balancer_and_other_app_tasks_reach_the_app" {
  command = apply

  assert {
    condition = length(aws_security_group.app.ingress) == 1 && alltrue([for r in aws_security_group.app.ingress :
    r.security_groups == toset([aws_security_group.alb.id]) && r.self && try(length(r.cidr_blocks), 0) == 0])
    error_message = "app ingress: the ALB and the app group itself, on the ephemeral range; no CIDRs"
  }
}

run "meet_deploys_safely" {
  command = apply

  assert {
    condition     = aws_ecs_service.meet.wait_for_steady_state
    error_message = "the apply must fail if meet never becomes healthy"
  }
  assert {
    condition     = one(aws_ecs_service.meet.deployment_circuit_breaker).rollback
    error_message = "a failing deploy rolls back by itself"
  }
  assert {
    condition     = one(aws_lb_target_group.meet.health_check).path == "/api/health"
    error_message = "meet's health route"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.meet_app.container_definitions)[0].image == "${data.aws_ecr_repository.this["meet"].repository_url}:0123abc"
    error_message = "meet runs the image the pipeline just pushed, by SHA"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.meet_app.container_definitions)[0].cpu >= 512
    error_message = "meet reserves its measured peak: ~315 CPU units when 100 rooms joined at once (spike, 2026-10-05)"
  }
}

run "staging_has_its_own_name_and_logs_expire" {
  command = apply

  assert {
    condition     = aws_route53_record.meet.name == "meet-staging.wwbp.org"
    error_message = "one level under wwbp.org, so the existing *.wwbp.org certificate covers it"
  }
  assert {
    condition     = aws_cloudwatch_log_group.meet.retention_in_days > 0 && startswith(aws_cloudwatch_log_group.meet.name, "/meetlab-v2/")
    error_message = "logs expire, and live under the prefix CI may manage"
  }
}

run "per_task_metrics_are_off_unless_a_test_needs_them" {
  command = apply

  variables {
    container_insights = false # stated, not assumed: a test session may switch it on
  }

  assert {
    condition     = one([for s in aws_ecs_cluster.this.setting : s.value if s.name == "containerInsights"]) == "disabled"
    error_message = "Container Insights is billed per task; off by default"
  }
}

run "a_load_test_can_turn_per_task_metrics_on" {
  command = apply

  variables {
    container_insights = true
  }

  assert {
    condition     = one([for s in aws_ecs_cluster.this.setting : s.value if s.name == "containerInsights"]) == "enabled"
    error_message = "sessions per machine needs each bot's CPU and memory"
  }
}
