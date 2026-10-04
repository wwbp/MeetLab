# Offline: mock provider, no AWS. Self-hosted LiveKit (load-test readiness L2): our own
# livekit-server, switched on by livekit_self_hosted; LiveKit Cloud otherwise.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "switched_off_everything_uses_livekit_cloud" {
  command = apply

  variables {
    livekit_self_hosted = false # the default is the switch (variables.tf)
  }

  assert {
    condition     = length(aws_ecs_service.livekit) == 0 && length(aws_lb_listener_rule.livekit) == 0 && length(aws_route53_record.livekit) == 0
    error_message = "no self-hosted LiveKit unless switched on"
  }
  assert {
    condition = contains([for s in jsondecode(aws_ecs_task_definition.bot.container_definitions)[0].secrets : s.valueFrom],
    "arn:aws:ssm:us-east-1:123456789012:parameter/meetlab-v2/staging/LIVEKIT_API_KEY")
    error_message = "bots use LiveKit Cloud's key"
  }
  assert {
    condition     = output.livekit.video
    error_message = "LiveKit Cloud records video"
  }
}

run "switched_on_it_runs_our_livekit_server" {
  command = apply

  variables {
    livekit_self_hosted   = true
    livekit_instance_type = "c6i.large" # the size between test sessions (tests/scale.tftest.hcl)
  }

  assert {
    condition = (
      aws_launch_template.livekit.instance_type == "c6i.large" &&
      one(aws_launch_template.livekit.network_interfaces).associate_public_ip_address == "true" &&
      toset(aws_autoscaling_group.livekit.vpc_zone_identifier) == toset([for s in aws_subnet.public : s.id]) &&
      aws_autoscaling_group.livekit.max_size == 1
    )
    error_message = "one small machine with a public IP: media goes straight to it (the standard LiveKit shape)"
  }
  assert {
    condition = (
      toset([for r in aws_security_group.livekit.ingress : "${r.protocol}/${r.from_port}" if contains(coalesce(r.cidr_blocks, []), "0.0.0.0/0")]) == toset(["tcp/7881", "udp/7882"]) &&
      anytrue([for r in aws_security_group.livekit.ingress : r.from_port == 7880 && contains(coalesce(r.security_groups, []), aws_security_group.alb.id)])
    )
    error_message = "media ports open (every join needs a signed token); signalling only through the load balancer's TLS"
  }
  assert {
    condition = (
      aws_ecs_task_definition.livekit.network_mode == "host" &&
      jsondecode(aws_ecs_task_definition.livekit.container_definitions)[0].image == "livekit/livekit-server:v1.12.0"
    )
    error_message = "host networking, the version local development runs"
  }
  assert {
    condition = (
      toset([for s in jsondecode(aws_ecs_task_definition.livekit.container_definitions)[0].secrets : s.valueFrom]) ==
      toset(["arn:aws:ssm:us-east-1:123456789012:parameter/meetlab-v2/staging/SELFHOSTED_LIVEKIT_API_KEY",
      "arn:aws:ssm:us-east-1:123456789012:parameter/meetlab-v2/staging/SELFHOSTED_LIVEKIT_API_SECRET"])
    )
    error_message = "the server's key comes from the parameters a person minted, never from Terraform"
  }
  assert {
    condition     = one(one(aws_lb_listener_rule.livekit[0].condition).host_header).values == toset(["livekit-staging.wwbp.org"]) && aws_lb_target_group.livekit[0].port == 7880
    error_message = "wss://livekit-staging.wwbp.org through the load balancer's certificate"
  }
  assert {
    condition = alltrue([for td in [aws_ecs_task_definition.bot, aws_ecs_task_definition.runner_app, aws_ecs_task_definition.meet_app] :
      contains([for s in jsondecode(td.container_definitions)[0].secrets : s.valueFrom], "arn:aws:ssm:us-east-1:123456789012:parameter/meetlab-v2/staging/SELFHOSTED_LIVEKIT_API_KEY") &&
      contains([for e in jsondecode(td.container_definitions)[0].environment : "${e.name}=${e.value}"], "LIVEKIT_URL=wss://livekit-staging.wwbp.org")
    ])
    error_message = "bots, the runner and meet all switch to our LiveKit together"
  }
  assert {
    condition     = output.livekit == { url = "wss://livekit-staging.wwbp.org", key_parameter = "SELFHOSTED_LIVEKIT_API_KEY", secret_parameter = "SELFHOSTED_LIVEKIT_API_SECRET", video = false }
    error_message = "the live tests learn which LiveKit to use, and that there is no video recording yet"
  }
}

# D1: participants behind strict firewalls (only web traffic allowed) reach LiveKit through
# its built-in TURN server over TLS on 443, which looks like any HTTPS connection.
run "turn_over_tls_on_443" {
  command = apply

  variables {
    livekit_self_hosted = true
  }

  assert {
    condition = alltrue([for line in ["turn:", "enabled: true", "domain: turn-staging.wwbp.org", "tls_port: 5349", "external_tls: true"] :
    strcontains(join("", jsondecode(aws_ecs_task_definition.livekit.container_definitions)[0].command), line)])
    error_message = "LiveKit's TURN server on, its TLS ended by the load balancer (external_tls)"
  }
  assert {
    condition = (
      aws_lb.turn[0].load_balancer_type == "network" && !aws_lb.turn[0].internal &&
      aws_lb_listener.turn[0].port == 443 && aws_lb_listener.turn[0].protocol == "TLS" &&
      aws_lb_listener.turn[0].certificate_arn == data.aws_acm_certificate.wildcard.arn &&
      aws_lb_target_group.turn[0].port == 5349 && aws_lb_target_group.turn[0].protocol == "TCP"
    )
    error_message = "turn-staging.wwbp.org:443: TLS with the *.wwbp.org certificate, then plain TCP to LiveKit's TURN port"
  }
  assert {
    condition = anytrue([for r in aws_security_group.livekit.ingress :
    r.protocol == "tcp" && r.from_port == 5349 && contains(coalesce(r.security_groups, []), aws_security_group.turn_lb.id) && length(coalesce(r.cidr_blocks, [])) == 0])
    error_message = "the TURN port only from the load balancer, never straight from the internet"
  }
  assert {
    condition     = aws_route53_record.turn[0].name == "turn-staging.wwbp.org" && contains([for lb in aws_ecs_service.livekit[0].load_balancer : lb.container_port], 5349)
    error_message = "the name points at the load balancer, which sends to the LiveKit task"
  }
}

run "no_turn_without_our_livekit" {
  command = apply

  variables {
    livekit_self_hosted = false
  }

  assert {
    condition     = length(aws_lb.turn) == 0 && length(aws_route53_record.turn) == 0
    error_message = "LiveKit Cloud has its own TURN: nothing of ours"
  }
}
