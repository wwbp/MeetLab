#!/usr/bin/env bash
# Verify that production's console surface is actually behind auth.
#
# Run this AFTER any deploy that touches meet/middleware.ts. On 2026-08-05 an
# unauthenticated caller could enumerate every session and download participant
# audio and video — see docs/distillation-audit.md (Iteration 3). middleware.test.ts
# guards the allow-list in CI; this checks the thing that actually matters, which is
# what production returns.
#
# Usage:
#   scripts/verify-prod-auth.sh                      # defaults to the prod meet CNAME
#   HOST=meeting-client.example.com scripts/verify-prod-auth.sh
#
# Exit 0 = every protected path rejects an anonymous caller and every public path
# still works. Exit 1 = something is exposed, or a participant-facing path broke.
set -uo pipefail

HOST="${HOST:-$(aws elasticbeanstalk describe-environments \
  --region "${AWS_REGION:-us-east-1}" --environment-names meeting-client \
  --query 'Environments[0].CNAME' --output text 2>/dev/null)}"
SCHEME="${SCHEME:-http}"

if [ -z "$HOST" ] || [ "$HOST" = "None" ]; then
  echo "Could not resolve the meet host. Set HOST=..." >&2
  exit 1
fi

echo "Checking $SCHEME://$HOST"
echo

fail=0

# Anonymous callers must be redirected to login (3xx) or refused (401/403).
#
# 404 deliberately does NOT count as a pass. Auth must reject the request *before*
# the handler looks anything up, so an unauthenticated caller can never distinguish
# "no such meeting" from "not allowed" — and more practically, a 404 for a
# non-existent id tells us nothing about what a real id would return. Treating 404
# as success is how you write a check that passes against an exposed system.
# 405 likewise means the router reached a real handler without an auth challenge.
check_protected() {
  local path="$1"
  local code
  code=$(curl -s -o /dev/null --max-time 20 -w '%{http_code}' "$SCHEME://$HOST$path")
  case "$code" in
    301|302|303|307|308|401|403)
      printf '  ✅ %-52s %s (rejected)\n' "$path" "$code" ;;
    404|405)
      printf '  ❌ %-52s %s  reached the handler without an auth challenge\n' "$path" "$code"
      fail=1 ;;
    *)
      printf '  ❌ %-52s %s  EXPOSED\n' "$path" "$code"; fail=1 ;;
  esac
}

# Participant-facing paths must keep working without a session.
check_public() {
  local path="$1"
  local code
  code=$(curl -s -o /dev/null --max-time 20 -w '%{http_code}' "$SCHEME://$HOST$path")
  # 4xx from missing params is fine; 5xx or an auth redirect is not.
  case "$code" in
    5*|301|302|303|307|308)
      printf '  ❌ %-52s %s  BROKEN for participants\n' "$path" "$code"; fail=1 ;;
    *)
      printf '  ✅ %-52s %s\n' "$path" "$code" ;;
  esac
}

echo "Console surface — must reject anonymous callers:"
for p in / /config /meetings /start-links /api/meetings /api/meetings/reconcile \
         /api/concierge/rooms /api/console/config; do
  check_protected "$p"
done

# The exact chain that was exploitable. If /api/meetings is closed we cannot mint a
# real id, so probe with a syntactically valid but non-existent one: the point is
# that auth rejects it *before* any lookup happens.
echo
echo "Recording download chain (the 2026-08-05 exposure):"
FAKE=00000000-0000-0000-0000-000000000000
check_protected "/api/meetings/$FAKE/audio-tracks/download"
check_protected "/api/meetings/$FAKE/files/$FAKE/download"
check_protected "/api/meetings/$FAKE/transcript"

echo
echo "Participant surface — must stay reachable:"
for p in /api/health /api/connection-details /login; do
  check_public "$p"
done

echo
if [ "$fail" -eq 0 ]; then
  echo "PASS — console is behind auth, participant paths still reachable."
else
  echo "FAIL — see ❌ above." >&2
fi
exit "$fail"
