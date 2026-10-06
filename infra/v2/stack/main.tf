# The v2 stack (staging or prod, by `env`): applied only by .github/workflows/infra-v2.yml when a PR into
# v2 merges. Every resource carries Project=meetlab-v2 so it is findable in the shared
# lab account and so bootstrap's IAM scoping can key on it.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  # The state file is the environment's, given at init:
  #   terraform init -backend-config=key=v2/<staging|prod>/terraform.tfstate
  backend "s3" {
    bucket       = "meetlab-tfstate-848180123498"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

locals {
  name = "meetlab-v2-${var.env}" # every resource name starts with it
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { Project = "meetlab-v2", Environment = var.env, ManagedBy = "terraform/infra/v2/${var.env}" }
  }
}
