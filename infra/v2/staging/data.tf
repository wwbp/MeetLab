# Postgres and the media bucket. The app security group is defined here because it is
# what the database trusts; ECS tasks join it in a later PR.

resource "aws_security_group" "app" {
  name        = "meetlab-v2-staging-app"
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
  name        = "meetlab-v2-staging-db"
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
  name       = "meetlab-v2-staging"
  subnet_ids = [for s in aws_subnet.private : s.id]
}

# ponytail: t4g.small, single AZ. Enough for staging (100 sessions heartbeating every
# 10 s is ~10 writes/s); size up and turn on multi_az before this carries a study.
resource "aws_db_instance" "this" {
  identifier                  = "meetlab-v2-staging"
  engine                      = "postgres"
  engine_version              = "17"
  instance_class              = var.db_instance_class # db.t4g.small; raised for big load tests
  apply_immediately           = true                  # a size change applies on merge, not next week (a few minutes' restart)
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
  final_snapshot_identifier   = "meetlab-v2-staging-final"
}

resource "aws_s3_bucket" "media" {
  bucket = "meetlab-v2-staging-media-848180123498"
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
