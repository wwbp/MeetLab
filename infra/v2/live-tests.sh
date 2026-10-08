#!/usr/bin/env bash
# The live acceptance tests against one deployed environment, as CI runs them after a
# deploy (infra-v2.yml for staging, release-v2.yml for production):
#
#   infra/v2/live-tests.sh <staging|prod> '<terraform output -json livekit>' [scenario ...]
#
# Reads the environment's secrets from SSM (masked in CI logs, never printed), checks the
# deployed roles' permissions, then runs agent-runner/tests/acceptance_staging.py.
set -euo pipefail

env="$1" lk="$2"
shift 2
export MEETLAB_ENV="$env"

get() { aws ssm get-parameter --region us-east-1 --with-decryption --name "/meetlab-v2/$env/$1" --query Parameter.Value --output text; }
secret() {
  local v
  v="$(get "$2")"
  [ -n "${GITHUB_ACTIONS:-}" ] && echo "::add-mask::$v"
  export "$1=$v"
}

# Seconds, before any bot starts: every AWS call the code makes, simulated against the
# deployed roles (and calls they must never make).
uv run --no-project --with boto3 python agent-runner/tests/permission_contract.py

secret CONSOLE_PASSWORD CONSOLE_PASSWORD
secret LIVEKIT_API_KEY "$(jq -r .key_parameter <<<"$lk")"
secret LIVEKIT_API_SECRET "$(jq -r .secret_parameter <<<"$lk")"
url="$(jq -r '.url // empty' <<<"$lk")" # our own LiveKit; LiveKit Cloud's URL is a secret
if [ -n "$url" ]; then export LIVEKIT_URL="$url"; else secret LIVEKIT_URL LIVEKIT_URL; fi
[ "$(jq -r .video <<<"$lk")" = true ] || export NO_VIDEO=1

# Chromium for turn_relay: a participant's browser, relay-only. Browser only, no
# --with-deps: the runner image has its libraries, and apt hung on Azure's Ubuntu mirror
# for the whole job twice on 2026-10-07. In CI a stall fails in 10 min.
${GITHUB_ACTIONS:+timeout 600} uvx --with playwright playwright install chromium # macOS has no timeout
uv run --no-project --with livekit --with livekit-api --with boto3 --with playwright \
  python agent-runner/tests/acceptance_staging.py "$@"
