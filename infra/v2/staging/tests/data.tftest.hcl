# Offline: mock provider, no AWS.

mock_provider "aws" {}

run "database_is_private_encrypted_and_hard_to_lose" {
  command = apply

  assert {
    condition     = !aws_db_instance.this.publicly_accessible && aws_db_instance.this.storage_encrypted
    error_message = "database must be private and encrypted"
  }
  assert {
    condition     = aws_db_instance.this.deletion_protection && !aws_db_instance.this.skip_final_snapshot && aws_db_instance.this.backup_retention_period >= 7
    error_message = "v1's database had deletion protection off; v2's must not"
  }
  assert {
    condition     = aws_db_instance.this.manage_master_user_password
    error_message = "master password lives in Secrets Manager, never in state or env vars"
  }
  assert {
    condition     = toset(aws_db_subnet_group.this.subnet_ids) == toset([for s in aws_subnet.private : s.id])
    error_message = "database belongs in the private subnets only"
  }
  assert {
    condition     = startswith(aws_db_instance.this.engine_version, "17")
    error_message = "same major version as v1 (17), so a data copy is a plain dump/restore"
  }
}

run "only_the_app_can_reach_the_database" {
  command = apply

  assert {
    condition = alltrue([
      for r in aws_security_group.db.ingress :
      r.from_port == 5432 && r.to_port == 5432 && try(length(r.cidr_blocks), 0) == 0 && r.security_groups == toset([aws_security_group.app.id])
    ]) && length(aws_security_group.db.ingress) == 1
    error_message = "db ingress must be 5432 from the app security group, nothing else"
  }
}

run "media_bucket_is_private" {
  command = apply

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.media.block_public_acls,
      aws_s3_bucket_public_access_block.media.block_public_policy,
      aws_s3_bucket_public_access_block.media.ignore_public_acls,
      aws_s3_bucket_public_access_block.media.restrict_public_buckets,
    ])
    error_message = "recordings of study participants must never be public"
  }
  assert {
    condition     = startswith(aws_s3_bucket.media.bucket, "meetlab-v2-")
    error_message = "bootstrap scopes S3 access to meetlab-v2-* buckets"
  }
  assert {
    condition     = one(aws_s3_bucket_ownership_controls.media.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs disabled; access is by policy only"
  }
}
