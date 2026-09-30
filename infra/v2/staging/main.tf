# The v2 staging stack: applied only by .github/workflows/infra-v2.yml when a PR into
# v2 merges. Every resource carries Project=meetlab-v2 so it is findable in the shared
# lab account and so bootstrap's IAM scoping can key on it.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    bucket       = "meetlab-tfstate-848180123498"
    key          = "v2/staging/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { Project = "meetlab-v2", Environment = "staging", ManagedBy = "terraform/infra/v2/staging" }
  }
}
