# Public entry: meet${var.hostname_suffix}.wwbp.org -> ALB (public subnets) -> tasks (private).
# One level under wwbp.org so the existing, auto-renewing *.wwbp.org certificate
# covers it; nothing about that certificate or the shared zone is managed here
# except this one record.

data "aws_acm_certificate" "wildcard" {
  domain      = "*.wwbp.org"
  statuses    = ["ISSUED"]
  most_recent = true
}

data "aws_route53_zone" "wwbp" {
  name = "wwbp.org"
}

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public HTTP/HTTPS"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }
}

resource "aws_lb" "this" {
  name               = local.name
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [for s in aws_subnet.public : s.id]
  idle_timeout       = 3600 # LiveKit signalling is a long-lived websocket to LiveKit, not here, but the console streams
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = data.aws_acm_certificate.wildcard.arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.meet.arn
  }
}

resource "aws_route53_record" "meet" {
  zone_id = data.aws_route53_zone.wwbp.zone_id
  name    = "meet${var.hostname_suffix}.wwbp.org"
  type    = "A"
  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}

output "url" {
  value = "https://${aws_route53_record.meet.name}"
}
