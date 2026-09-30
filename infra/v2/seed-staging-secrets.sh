#!/usr/bin/env bash
# Copy v1's vendor keys into SSM for v2 staging, and generate the secrets v2 owns.
#
# Values go EB -> SSM SecureString without being printed, and never enter Terraform
# state: task definitions reference the parameters by name. Run by a person with
# admin credentials (CI roles can't read v1's EB settings, by design).
#
# Sanity runs only: v1's keys share v1's vendor quotas. Load tests wait for their
# own keys or free drop-in models (infra/v2/LEDGER.md).
#
#   infra/v2/seed-staging-secrets.sh           # write missing parameters
#   infra/v2/seed-staging-secrets.sh --force   # overwrite from v1 again
set -euo pipefail

REGION=us-east-1
PREFIX=/meetlab-v2/staging
APP=vivaprox
V1_ENV=agent-runner
FROM_V1=(OPENAI_API_KEY ELEVENLABS_API_KEY DEEPGRAM_API_KEY LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET CONSOLE_PASSWORD)
GENERATED=(BOT_RUNNER_SECRET)
force="${1:-}"

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
