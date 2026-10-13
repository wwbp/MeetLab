# The v1 -> v2 data copy (docs/v1-to-v2-migration.md): started by a person with
# `aws ecs run-task`, never by a deploy. SOURCE_URL (SSM MIGRATION_SOURCE_URL) is a copy of v1
# restored from its snapshot next to this database, behind the same firewall; v1 itself is in
# another network and never reachable from here. Per run, TARGET_DB names the database to fill:
#   copy    -> drops and recreates TARGET_DB, then pg_dump | pg_restore from the source
#   migrate -> only if the copy succeeded: alembic upgrade head, remove our programmatic runs,
#              check every other row arrived (migration_check.py SOURCE - --real --prune)
# Filling this environment's own database (the cutover) also needs CONFIRM=replace-<its name>.

resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/meetlab-v2/${var.env}/migrate"
  retention_in_days = 90
}

locals {
  migrate_logs = {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.migrate.name
      awslogs-region        = "us-east-1"
      awslogs-stream-prefix = "migrate"
    }
  }
  migrate_db = [
    { name = "DB_HOST", value = aws_db_instance.this.address },
    { name = "DB_USER", value = aws_db_instance.this.username },
    { name = "DB_NAME", value = aws_db_instance.this.db_name },
  ]
  migrate_source = { name = "SOURCE_URL", valueFrom = "${local.parameters}/MIGRATION_SOURCE_URL" }
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  execution_role_arn       = aws_iam_role.execution.arn # pulls, SSM and the database secret; no task role
  container_definitions = jsonencode([
    {
      name              = "copy"
      image             = "public.ecr.aws/docker/library/postgres:17-alpine" # pg_dump/pg_restore, same major as RDS
      essential         = false
      cpu               = 256
      memoryReservation = 256
      environment       = local.migrate_db
      secrets           = [local.migrate_source, { name = "PGPASSWORD", valueFrom = "${local.db_secret}:password::" }]
      command = ["sh", "-euc", <<-EOT
        set -o pipefail
        : "$${TARGET_DB:?set TARGET_DB}"
        if [ "$TARGET_DB" = "$DB_NAME" ] && [ "$${CONFIRM:-}" != "replace-$DB_NAME" ]; then
          echo "refusing to replace $DB_NAME without CONFIRM=replace-$DB_NAME"; exit 1
        fi
        export PGHOST="$DB_HOST" PGUSER="$DB_USER" PGSSLMODE=require
        psql -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$TARGET_DB\""
        pg_dump -Fc --no-owner --no-acl "$SOURCE_URL" | pg_restore --no-owner --no-acl --exit-on-error -d "$TARGET_DB"
        echo "copied into $TARGET_DB"
      EOT
      ]
      logConfiguration = local.migrate_logs
      mountPoints      = []
      portMappings     = []
      systemControls   = []
      volumesFrom      = []
    },
    {
      name              = "migrate"
      image             = "${data.aws_ecr_repository.this["agent-runner"].repository_url}:${var.image_tag}"
      essential         = true
      dependsOn         = [{ containerName = "copy", condition = "SUCCESS" }]
      cpu               = 256
      memoryReservation = 512
      environment       = local.migrate_db
      secrets           = [local.migrate_source, { name = "DB_PASSWORD", valueFrom = "${local.db_secret}:password::" }]
      command = ["sh", "-euc", <<-EOT
        export DB_NAME="$${TARGET_DB:?set TARGET_DB}"
        alembic upgrade head
        python migration_check.py "$SOURCE_URL" - --real --prune
      EOT
      ]
      logConfiguration = local.migrate_logs
      mountPoints      = []
      portMappings     = []
      systemControls   = []
      volumesFrom      = []
    },
  ])
}
