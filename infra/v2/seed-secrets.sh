#!/usr/bin/env bash
# Copy v1's vendor keys into SSM for one v2 environment, and generate the secrets v2 owns.
#
# Values go EB -> SSM SecureString without being printed, and never enter Terraform
# state: task definitions reference the parameters by name. Run by a person with
# admin credentials (CI roles can't read v1's EB settings, by design).
#
# Sanity runs only: v1's keys share v1's vendor quotas. Load tests wait for their
# own keys or free drop-in models (infra/v2/LEDGER.md).
#
# The recording key for LiveKit Cloud is minted by a person once the environment's
# egress user exists (docs/v2-deployment.md); until then a placeholder lets the runner
# start (ECS refuses a task whose secret is missing). Never overwritten here.
#
#   infra/v2/seed-secrets.sh staging|prod           # write missing parameters
#   infra/v2/seed-secrets.sh staging|prod --force   # overwrite from v1 again
set -euo pipefail

REGION=us-east-1
env="${1:-}"
case "$env" in staging | prod) ;; *) echo "usage: $0 staging|prod [--force]" >&2; exit 2 ;; esac
PREFIX=/meetlab-v2/$env
APP=vivaprox
V1_ENV=agent-runner
FROM_V1=(OPENAI_API_KEY ELEVENLABS_API_KEY DEEPGRAM_API_KEY LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET CONSOLE_PASSWORD)
GENERATED=(BOT_RUNNER_SECRET)
PLACEHOLDER=(EGRESS_S3_KEY_ID EGRESS_S3_KEY_SECRET)
force="${2:-}"

exists() { aws ssm get-parameter --region "$REGION" --name "$PREFIX/$1" >/dev/null 2>&1; }

v1_value() {
  aws elasticbeanstalk describe-configuration-settings --region "$REGION" \
    --application-name "$APP" --environment-name "$V1_ENV" \
    --query "ConfigurationSettings[0].OptionSettings[?Namespace=='aws:elasticbeanstalk:application:environment' && OptionName=='$1'].Value | [0]" \
    --output text
}

for name in "${FROM_V1[@]}"; do
  if exists "$name" && [ "$force" != "--force" ]; then echo "keep      $PREFIX/$name"; continue; fi
  value="$(v1_value "$name")"
  if [ -z "$value" ] || [ "$value" = "None" ]; then echo "MISSING   $name is not set on v1 $V1_ENV" >&2; exit 1; fi
  aws ssm put-parameter --region "$REGION" --name "$PREFIX/$name" --type SecureString \
    --overwrite --value "$value" >/dev/null
  echo "copied    $PREFIX/$name (from v1 $V1_ENV)"
done

for name in "${GENERATED[@]}"; do
  if exists "$name"; then echo "keep      $PREFIX/$name"; continue; fi
  aws ssm put-parameter --region "$REGION" --name "$PREFIX/$name" --type SecureString \
    --value "$(openssl rand -hex 32)" >/dev/null
  echo "generated $PREFIX/$name"
done

for name in "${PLACEHOLDER[@]}"; do
  if exists "$name"; then echo "keep      $PREFIX/$name"; continue; fi
  aws ssm put-parameter --region "$REGION" --name "$PREFIX/$name" --type SecureString \
    --value "placeholder: mint the key (docs/v2-deployment.md)" >/dev/null
  echo "placeholder $PREFIX/$name (mint the real key after the first deploy)"
done
