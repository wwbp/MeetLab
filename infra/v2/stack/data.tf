# Postgres and the media bucket. The app security group is defined here because it is
# what the database trusts; ECS tasks join it in a later PR.

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "meet, control API and bot tasks"
  vpc_id      = aws_vpc.this.id
  # Bridge networking: ECS maps container ports to the ephemeral range on the host.
  # The ALB reaches meet there, and Service Connect proxies reach each other there,
  # across instances (self).
  ingress {
    from_port       = 32768
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    self            = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Postgres, from the app only"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = [for s in aws_subnet.private : s.id]
}

# Sized by environment (profiles.tf): staging db.t4g.small in one zone; production
# db.t4g.medium across two zones.
resource "aws_db_instance" "this" {
  identifier                  = local.name
  engine                      = "postgres"
  engine_version              = "17"
  instance_class              = local.db_instance_class
  multi_az                    = local.db_multi_az
  apply_immediately           = true # a size change applies on merge, not next week (a few minutes' restart)
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = "meetlab"
  username                    = "meetlab"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.this.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  publicly_accessible         = false
  backup_retention_period     = 7
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${local.name}-final"
}

resource "aws_s3_bucket" "media" {
  bucket = "${local.name}-media-${data.aws_caller_identity.current.account_id}"
}

# Recordings are study data: an overwrite or a delete keeps the earlier version.
resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
