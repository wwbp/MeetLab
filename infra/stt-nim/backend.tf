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
    # bucket, region, dynamodb_table provided via -backend-config
    encrypt = true
  }
}

provider "aws" {
  region = var.region
}
