#!/usr/bin/env bash
# The v1 -> v2 data copy, step by step (docs/v1-to-v2-migration.md). Run by a person with admin
# credentials, each step on the user's yes, and logged in infra/v2/LEDGER.md. v1 is only read:
# its snapshot is restored next to the environment's database, and the copy reads that.
#
#   infra/v2/migrate.sh source <env> <snapshot-name>   snapshot v1, restore it as the source, store its URL in SSM
#   infra/v2/migrate.sh run    <env> <target-db> [confirm]   the migrate task (copy, upgrade, prune, check)
#   infra/v2/migrate.sh clean  <env>                    delete the restored source and its SSM URL
#
# A rehearsal fills a throwaway database (e.g. meetlab_rehearsal); the cutover fills the
# environment's own (meetlab) and needs `confirm`, with meet and the runner stopped first.
set -euo pipefail

REGION=us-east-1
step="${1:-}" env="${2:-}"
case "$env" in staging | prod) ;; *) echo "usage: $0 source|run|clean staging|prod ..." >&2; exit 2 ;; esac
name="meetlab-v2-$env"
src="$name-migration-src"
param="/meetlab-v2/$env/MIGRATION_SOURCE_URL"
aws() { command aws --region "$REGION" "$@"; }

case "$step" in
source)
  snap="${3:?snapshot name, e.g. meetlab-rehearsal-$(date -u +%Y%m%d)}"
  aws rds create-db-snapshot --db-instance-identifier meetlab --db-snapshot-identifier "$snap" >/dev/null
  aws rds wait db-snapshot-available --db-snapshot-identifier "$snap"
  echo "v1 snapshot $snap available"
  sg=$(aws rds describe-db-instances --db-instance-identifier "$name" --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)
  aws rds restore-db-instance-from-db-snapshot --db-instance-identifier "$src" --db-snapshot-identifier "$snap" \
    --db-instance-class db.t4g.small --db-subnet-group-name "$name" --vpc-security-group-ids "$sg" \
    --no-multi-az --no-publicly-accessible --no-deletion-protection \
    --tags Key=Project,Value=meetlab-v2 Key=Environment,Value="$env" Key=Purpose,Value=migration-source >/dev/null
  aws rds wait db-instance-available --db-instance-identifier "$src"
  host=$(aws rds describe-db-instances --db-instance-identifier "$src" --query 'DBInstances[0].Endpoint.Address' --output text)
  # v1's own URL (same user and password: the snapshot carries them), pointed at the copy. Never printed.
  v1=$(aws elasticbeanstalk describe-configuration-settings --application-name vivaprox --environment-name agent-runner \
    --query "ConfigurationSettings[0].OptionSettings[?OptionName=='DATABASE_URL'].Value | [0]" --output text)
  url=$(python3 -c 'import sys, urllib.parse as u
p = u.urlsplit(sys.argv[1].replace("postgresql+asyncpg://", "postgresql://"))
print(u.urlunsplit(("postgresql", f"{p.username}:{p.password}@{sys.argv[2]}:5432", p.path or "/postgres", "sslmode=require", "")))' "$v1" "$host")
  aws ssm put-parameter --name "$param" --type SecureString --overwrite --value "$url" >/dev/null
  echo "source $src ready ($host); its URL is in $param"
  ;;
run)
  target="${3:?target database, e.g. meetlab_rehearsal}" confirm=""
  [ "${4:-}" = confirm ] && confirm="replace-$target"
  envs=$(python3 -c 'import json, sys; e = [{"name": "TARGET_DB", "value": sys.argv[1]}, {"name": "CONFIRM", "value": sys.argv[2]}]
print(json.dumps({"containerOverrides": [{"name": n, "environment": e} for n in ("copy", "migrate")]}))' "$target" "$confirm")
  task=$(aws ecs run-task --cluster "$name" --task-definition "$name-migrate" --started-by migration \
    --capacity-provider-strategy capacityProvider="$name-services",weight=1 --overrides "$envs" \
    --query 'tasks[0].taskArn' --output text)
  echo "task $task"
  aws ecs wait tasks-stopped --cluster "$name" --tasks "$task" || aws ecs wait tasks-stopped --cluster "$name" --tasks "$task"
  id="${task##*/}"
  for c in copy migrate; do
    echo "== $c"
    aws logs get-log-events --log-group-name "/meetlab-v2/$env/migrate" --log-stream-name "migrate/$c/$id" \
      --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' | tail -40 || true
  done
  code=$(aws ecs describe-tasks --cluster "$name" --tasks "$task" --query 'tasks[0].containers[?name==`migrate`].exitCode | [0]' --output text)
  echo "migrate exit code: $code"
  [ "$code" = 0 ]
  ;;
clean)
  aws rds delete-db-instance --db-instance-identifier "$src" --skip-final-snapshot >/dev/null # v1's snapshot is the backup
  aws ssm delete-parameter --name "$param" || true
  echo "deleting $src; $param removed"
  ;;
*) echo "usage: $0 source|run|clean staging|prod ..." >&2; exit 2 ;;
esac
