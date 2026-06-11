#!/usr/bin/env bash
# Query MeetLab production metrics in Grafana Cloud from the CLI.
#
# Usage:
#   scripts/grafana-prom.sh 'count({__name__=~"meetlab.*"})'
#   scripts/grafana-prom.sh 'histogram_quantile(0.5, rate(meetlab_stt_latency_ms_bucket[1h]))'
#   scripts/grafana-prom.sh --range '1h' 'rate(meetlab_utterances_total[5m])*60'
#   scripts/grafana-prom.sh --labels                  # list all meetlab metric names
#
# Auth: needs a Grafana Cloud Access Policy token with the metrics:read scope.
# Create at: https://grafana.com/orgs/sabhay/access-policies
#   (realm: sabhay / prod-us-east-3, scope: metrics:read -> Add token)
# Grafana web UI (dashboards): https://sabhay.grafana.net
# Provide the token via either:
#   export GRAFANA_READ_TOKEN=glc_...
# or a file:  ~/.config/meetlab/grafana-read-token   (chmod 600)
#
# The token in agent-runner/.env.runner is WRITE-ONLY (OTLP push) and will fail
# with "invalid scope requested" -- do not reuse it here.
set -euo pipefail

PROM_URL="https://prometheus-prod-66-prod-us-east-3.grafana.net/api/prom"
PROM_USER="3238458"  # Grafana Cloud Prometheus instance ID for the meetlab-prod stack
TOKEN_FILE="$HOME/.config/meetlab/grafana-read-token"

token="${GRAFANA_READ_TOKEN:-}"
if [ -z "$token" ] && [ -f "$TOKEN_FILE" ]; then
  token="$(cat "$TOKEN_FILE")"
fi
if [ -z "$token" ]; then
  echo "No token. Set GRAFANA_READ_TOKEN or write the token to $TOKEN_FILE" >&2
  echo "(create one: Grafana Cloud portal -> Access Policies -> scope metrics:read)" >&2
  exit 1
fi

jq_or_cat() { if command -v jq >/dev/null; then jq .; else cat; fi; }

case "${1:-}" in
  --labels)
    curl -sf -u "$PROM_USER:$token" \
      "$PROM_URL/api/v1/label/__name__/values" \
      --data-urlencode 'match[]={__name__=~"meetlab.*"}' -G | jq_or_cat
    ;;
  --range)
    range="${2:?usage: --range <duration e.g. 1h> '<query>'}"
    query="${3:?missing query}"
    end=$(date -u +%s); start=$((end - $(echo "$range" | sed 's/h/*3600/;s/m/*60/;s/d/*86400/' | bc)))
    curl -sf -u "$PROM_USER:$token" "$PROM_URL/api/v1/query_range" \
      --data-urlencode "query=$query" \
      --data-urlencode "start=$start" --data-urlencode "end=$end" \
      --data-urlencode "step=60" | jq_or_cat
    ;;
  *)
    query="${1:?usage: grafana-prom.sh [--labels | --range <dur> <query> | <query>]}"
    curl -sf -u "$PROM_USER:$token" "$PROM_URL/api/v1/query" \
      --data-urlencode "query=$query" | jq_or_cat
    ;;
esac
