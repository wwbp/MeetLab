#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <output-zip-path>" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_path="$1"

if [[ "${output_path}" != /* ]]; then
  output_path="${repo_root}/${output_path}"
fi

mkdir -p "$(dirname "${output_path}")"
rm -f "${output_path}"

cd "${repo_root}/agent-runner"

zip -r "${output_path}" . \
  -x '.git/*' \
  -x '.DS_Store' \
  -x '.env*' \
  -x '.venv/*' \
  -x '__pycache__/*' \
  -x '*/__pycache__/*' \
  -x '*.pyc' \
  -x '*.pyo' \
  -x '*.pyd' \
  -x '.pytest_cache/*' \
  -x '.mypy_cache/*' \
  -x '.ruff_cache/*' \
  -x 'dist/*' \
  -x 'build/*'
