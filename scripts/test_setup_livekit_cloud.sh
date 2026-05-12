#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$ROOT_DIR/scripts/setup_livekit_cloud.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR/agent-runner" "$TMP_DIR/web-client" "$TMP_DIR/meet"
cp "$ROOT_DIR/agent-runner/.env.runner.example" "$TMP_DIR/agent-runner/.env.runner.example"
cp "$ROOT_DIR/web-client/.env.web.example" "$TMP_DIR/web-client/.env.web.example"
cp "$ROOT_DIR/meet/.env.local.example" "$TMP_DIR/meet/.env.local.example"

assert_line() {
  local file="$1"
  local expected="$2"
  if ! rg -q "^${expected}\$" "$file"; then
    echo "Assertion failed in $file: expected line '$expected'" >&2
    exit 1
  fi
}

assert_single_key() {
  local file="$1"
  local key="$2"
  local count
  count="$(rg -c "^${key}=" "$file")"
  if [[ "$count" != "1" ]]; then
    echo "Assertion failed in $file: expected one '${key}=' line, got $count" >&2
    exit 1
  fi
}

"$SCRIPT_PATH" setup \
  --root-dir "$TMP_DIR" \
  --url "https://example.livekit.cloud/" \
  --api-key "cloud_key" \
  --api-secret "cloud_secret"

assert_line "$TMP_DIR/agent-runner/.env.runner" "LIVEKIT_URL=wss://example.livekit.cloud"
assert_line "$TMP_DIR/agent-runner/.env.runner" "LIVEKIT_API_KEY=cloud_key"
assert_line "$TMP_DIR/agent-runner/.env.runner" "LIVEKIT_API_SECRET=cloud_secret"
assert_line "$TMP_DIR/web-client/.env.web" "LIVEKIT_URL=wss://example.livekit.cloud"
assert_line "$TMP_DIR/web-client/.env.web" "LIVEKIT_URL_INTERNAL=wss://example.livekit.cloud"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_URL_PUBLIC=wss://example.livekit.cloud"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_URL_INTERNAL=wss://example.livekit.cloud"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_URL=wss://example.livekit.cloud"

assert_single_key "$TMP_DIR/agent-runner/.env.runner" "LIVEKIT_URL"
assert_single_key "$TMP_DIR/web-client/.env.web" "LIVEKIT_URL"
assert_single_key "$TMP_DIR/web-client/.env.web" "LIVEKIT_URL_INTERNAL"
assert_single_key "$TMP_DIR/meet/.env.local" "LIVEKIT_URL_PUBLIC"
assert_single_key "$TMP_DIR/meet/.env.local" "LIVEKIT_URL_INTERNAL"
assert_single_key "$TMP_DIR/meet/.env.local" "LIVEKIT_URL"

"$SCRIPT_PATH" revert \
  --root-dir "$TMP_DIR"

assert_line "$TMP_DIR/agent-runner/.env.runner" "LIVEKIT_URL=ws://transport-server:7880"
assert_line "$TMP_DIR/agent-runner/.env.runner" "LIVEKIT_API_KEY=devkey"
assert_line "$TMP_DIR/agent-runner/.env.runner" "LIVEKIT_API_SECRET=secret"
assert_line "$TMP_DIR/web-client/.env.web" "LIVEKIT_URL=ws://localhost:7880"
assert_line "$TMP_DIR/web-client/.env.web" "LIVEKIT_URL_INTERNAL=ws://transport-server:7880"
assert_line "$TMP_DIR/web-client/.env.web" "LIVEKIT_API_KEY=devkey"
assert_line "$TMP_DIR/web-client/.env.web" "LIVEKIT_API_SECRET=secret"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_URL_PUBLIC=ws://localhost:7880"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_URL_INTERNAL=ws://transport-server:7880"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_URL=ws://localhost:7880"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_API_KEY=devkey"
assert_line "$TMP_DIR/meet/.env.local" "LIVEKIT_API_SECRET=secret"

echo "setup_livekit_cloud.sh tests passed"
