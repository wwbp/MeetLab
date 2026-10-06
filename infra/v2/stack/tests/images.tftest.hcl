# Offline: mock provider, no AWS. The image repositories belong to infra/v2/bootstrap
# (shared by both environments, release images kept there); the stack only reads them.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "test"
}

run "the_stack_reads_the_shared_repositories_and_owns_none" {
  command = plan

  assert {
    condition     = toset([for r in data.aws_ecr_repository.this : r.name]) == toset(["meetlab-v2/meet", "meetlab-v2/agent-runner"])
    error_message = "one repository per image, the shared meetlab-v2/ ones"
  }
}
