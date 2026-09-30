# Offline: mock provider, no AWS.

mock_provider "aws" {}

run "one_repository_per_image_under_the_meetlab_v2_prefix" {
  command = apply

  assert {
    condition     = toset([for r in aws_ecr_repository.this : r.name]) == toset(["meetlab-v2/meet", "meetlab-v2/agent-runner"])
    error_message = "meet, and agent-runner (which the bot task reuses with another command)"
  }
}

run "tags_are_immutable_so_a_sha_always_means_one_image" {
  command = apply

  assert {
    condition     = alltrue([for r in aws_ecr_repository.this : r.image_tag_mutability == "IMMUTABLE"])
    error_message = "a deployed tag must never be overwritten"
  }
  assert {
    condition     = alltrue([for r in aws_ecr_repository.this : one(r.image_scanning_configuration).scan_on_push])
    error_message = "scan every pushed image"
  }
}

run "old_images_expire" {
  command = apply

  assert {
    condition     = length(aws_ecr_lifecycle_policy.this) == length(aws_ecr_repository.this)
    error_message = "every repository needs a lifecycle policy, or storage grows forever"
  }
}
