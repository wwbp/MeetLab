# The image repositories outlive any one stack, so infra/bootstrap owns them (release images
# never expire there); the stack only reads them. agent-runner is the API's image; bot is the
# bot's (bot tasks and the load generator).

data "aws_ecr_repository" "this" {
  for_each = toset(["meet", "agent-runner", "bot", "stt-cpu"])
  name     = "meetlab-v2/${each.key}"
}

# Owned by staging's stack until 2026-10-06: forget them, never delete them.
removed {
  from = aws_ecr_repository.this
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_ecr_lifecycle_policy.this
  lifecycle {
    destroy = false
  }
}

output "ecr_repository_urls" {
  value = { for k, r in data.aws_ecr_repository.this : k => r.repository_url }
}
