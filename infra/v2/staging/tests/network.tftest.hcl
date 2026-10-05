# Offline: mock provider, no AWS. `apply` so mocked ids are known to the asserts.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "test"
}

run "two_azs_each_with_a_public_and_a_private_subnet" {
  command = apply

  assert {
    condition     = length(distinct([for s in aws_subnet.public : s.availability_zone])) == 2
    error_message = "public subnets must span two AZs (the ALB requires it)"
  }
  assert {
    condition     = toset([for s in aws_subnet.public : s.availability_zone]) == toset([for s in aws_subnet.private : s.availability_zone])
    error_message = "every AZ with a public subnet needs a private one"
  }
}

run "subnets_fit_inside_the_vpc_and_do_not_overlap" {
  command = apply

  assert {
    condition = alltrue([
      for s in concat(values(aws_subnet.public), values(aws_subnet.private)) :
      cidrsubnet(s.cidr_block, 0, 0) == s.cidr_block && join(".", slice(split(".", s.cidr_block), 0, 2)) == join(".", slice(split(".", aws_vpc.this.cidr_block), 0, 2))
    ])
    error_message = "subnet outside the VPC range"
  }
  assert {
    condition     = length(distinct(concat([for s in aws_subnet.public : s.cidr_block], [for s in aws_subnet.private : s.cidr_block]))) == 4
    error_message = "subnet CIDRs must be distinct"
  }
}

run "vpc_does_not_overlap_v1_so_it_can_peer_later" {
  command = apply

  assert {
    condition     = !startswith(aws_vpc.this.cidr_block, "10.0.")
    error_message = "vivaprox-vpc (v1) is 10.0.0.0/16"
  }
}

run "nothing_gets_a_public_ip_by_default" {
  command = apply

  assert {
    condition     = alltrue([for s in concat(values(aws_subnet.public), values(aws_subnet.private)) : !s.map_public_ip_on_launch])
    error_message = "instances are private; only the ALB and NAT face the internet"
  }
}

run "public_routes_to_the_igw_private_to_the_nat" {
  command = apply

  assert {
    condition     = one([for r in aws_route_table.public.route : r.gateway_id if r.cidr_block == "0.0.0.0/0"]) == aws_internet_gateway.this.id
    error_message = "public default route must be the internet gateway"
  }
  assert {
    condition     = one([for r in aws_route_table.private.route : r.nat_gateway_id if r.cidr_block == "0.0.0.0/0"]) == aws_nat_gateway.this.id
    error_message = "private default route must be the NAT"
  }
  assert {
    condition     = length(aws_route_table_association.private) == 2 && length(aws_route_table_association.public) == 2
    error_message = "every subnet must be associated with its route table"
  }
}

# AWS accepts only these characters in a security group's description, and the mock provider
# doesn't check: "LiveKit's" (an apostrophe) failed D1's first apply (2026-10-04).
run "security_group_descriptions_use_only_what_aws_accepts" {
  command = plan

  variables {
    livekit_self_hosted = true
  }

  assert {
    condition = alltrue([for d in concat([
      aws_security_group.alb.description, aws_security_group.app.description, aws_security_group.db.description,
      aws_security_group.livekit.description, aws_security_group.turn_lb.description, aws_security_group.loadgen.description,
      aws_security_group.stt_nim_lb.description, aws_security_group.stt_nim.description,
      ], [for g in aws_security_group.model_lb : g.description], [for g in aws_security_group.model : g.description]) :
    can(regex("^[a-zA-Z0-9. _:/()#,@\\[\\]+=&;{}!$*-]*$", d))])
    error_message = "a security group description has a character AWS refuses (e.g. an apostrophe)"
  }
}
