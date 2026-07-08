terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state — bootstrap the bucket + lock table once (see README).
  # Values are supplied at `terraform init` via -backend-config in the deploy workflow
  # so this file stays free of account-specific ids.
  backend "s3" {
    key = "stt-nim/terraform.tfstate"
    # bucket + region provided via -backend-config at init.
    encrypt = true
    # S3-native state locking (Terraform >= 1.10) — no DynamoDB lock table needed.
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
}
