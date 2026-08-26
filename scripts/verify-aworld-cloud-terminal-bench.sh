#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deployment_dir="${repository_root}/deploy/aworld-cloud"
environment_file="${AWORLD_CLOUD_ENV_FILE:-${deployment_dir}/.env}"
compose_file="${deployment_dir}/docker-compose.yml"

# shellcheck source=scripts/aworld-cloud-docker.sh
source "${repository_root}/scripts/aworld-cloud-docker.sh"

if [[ ! -f "${environment_file}" ]]; then
  echo "Run scripts/aworld-cloud-init.sh first" >&2
  exit 1
fi
aworld_cloud_export_docker_socket "${environment_file}"

data_directory="${AWORLD_CLOUD_DATA_DIR:-}"
if [[ -z "${data_directory}" ]]; then
  data_directory="$(
    aworld_cloud_env_value AWORLD_CLOUD_DATA_DIR "${environment_file}"
  )"
fi
if [[ -z "${data_directory}" || "${data_directory}" != /* || "${data_directory}" == "/" ]]; then
  echo "AWORLD_CLOUD_DATA_DIR must be a non-root absolute path" >&2
  exit 1
fi

compose=(docker compose --env-file "${environment_file}" -f "${compose_file}")
artifacts="${data_directory}/verification"
mkdir -p "${artifacts}"

on_error() {
  "${compose[@]}" logs --no-color aworld-cloud-server aworld-cloud-worker >&2 || true
}
trap on_error ERR

"${compose[@]}" up --build --detach --wait aworld-cloud-server aworld-cloud-worker

nonce="$(date -u +%Y%m%dT%H%M%SZ)-$$"
workspace_json="$(
  "${compose[@]}" run --rm -T aworld-cloud-cli \
    cloud workspace create \
    --name "terminal-bench-${nonce}" \
    --profile terminal-bench \
    --idempotency-key "workspace-${nonce}"
)"
workspace_id="$(
  printf '%s' "${workspace_json}" | \
    "${compose[@]}" exec -T aworld-cloud-server \
      python -c 'import json,sys; print(json.load(sys.stdin)["id"])'
)"

run_json="$(
  "${compose[@]}" run --rm -T aworld-cloud-cli \
    cloud run submit \
    --workspace-id "${workspace_id}" \
    --mode benchmark \
    --task "Run the real Terminal-Bench fix-git task and its verifier." \
    --dataset terminal-bench@2.0 \
    --task-id fix-git \
    --harness harbor \
    --idempotency-key "run-${nonce}"
)"
run_id="$(
  printf '%s' "${run_json}" | \
    "${compose[@]}" exec -T aworld-cloud-server \
      python -c 'import json,sys; print(json.load(sys.stdin)["id"])'
)"

"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run wait "${run_id}" --poll-interval 5 --wait-timeout 3600 \
  > "${artifacts}/${run_id}-result.json"
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run events "${run_id}" \
  > "${artifacts}/${run_id}-events.json"
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run files "${run_id}" \
  > "${artifacts}/${run_id}-files.json"
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run logs "${run_id}" --output-dir "${artifacts}/${run_id}-logs" \
  > "${artifacts}/${run_id}-downloads.json"
"${compose[@]}" run --rm -T aworld-cloud-cli \
  cloud run trajectory "${run_id}" \
  --output "${artifacts}/${run_id}-trajectory.atif.json" \
  > "${artifacts}/${run_id}-trajectory-download.json"
"${compose[@]}" logs --no-color aworld-cloud-server aworld-cloud-worker \
  > "${artifacts}/${run_id}-compose.log"

"${compose[@]}" exec -T aworld-cloud-server python -c '
import json
import sys

result = json.load(sys.stdin)
assert result["state"] == "succeeded", result
assert result["benchmark_outcome"]["reward"] == 1.0, result
assert result["canonical_trajectory_file_id"], result
' < "${artifacts}/${run_id}-result.json"

"${compose[@]}" exec -T aworld-cloud-server python -c '
import json
import sys

trajectory = json.load(sys.stdin)
assert trajectory["schema_version"] == "ATIF-v1.7", trajectory
assert trajectory["final_metrics"]["reward"] == 1.0, trajectory
' < "${artifacts}/${run_id}-trajectory.atif.json"

trap - ERR
echo "Terminal-Bench fix-git passed: run_id=${run_id} artifacts=${artifacts}"
