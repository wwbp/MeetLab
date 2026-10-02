# Shared by every staging test: the provider validates ARN shapes, and the mock's
# random strings aren't ARNs.

mock_data "aws_caller_identity" {
  defaults = { account_id = "123456789012" }
}

mock_data "aws_acm_certificate" {
  defaults = { arn = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000" }
}

mock_resource "aws_iam_instance_profile" {
  defaults = { arn = "arn:aws:iam::123456789012:instance-profile/meetlab-v2-staging-ecs-instance" }
}

mock_resource "aws_iam_role" {
  defaults = { arn = "arn:aws:iam::123456789012:role/meetlab-v2-staging-mock" }
}

mock_resource "aws_autoscaling_group" {
  defaults = { arn = "arn:aws:autoscaling:us-east-1:123456789012:autoScalingGroup:00000000-0000-0000-0000-000000000000:autoScalingGroupName/meetlab-v2-staging-ecs" }
}

mock_resource "aws_lb" {
  defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/meetlab-v2-staging/0000000000000000" }
}

mock_resource "aws_lb_target_group" {
  defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/meetlab-v2-staging-meet/0000000000000000" }
}

mock_resource "aws_launch_template" {
  defaults = { id = "lt-0123456789abcdef0" }
}

mock_resource "aws_db_instance" {
  defaults = {
    address            = "meetlab-v2-staging.abc.us-east-1.rds.amazonaws.com"
    master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!db-0000-AbCdEf", kms_key_id = "", secret_status = "active" }]
  }
}

mock_resource "aws_service_discovery_http_namespace" {
  defaults = { arn = "arn:aws:servicediscovery:us-east-1:123456789012:namespace/ns-0000000000000000" }
}

mock_resource "aws_lb_listener" {
  defaults = {
    arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/meetlab-v2-staging/0123456789abcdef/0123456789abcdef"
  }
}
