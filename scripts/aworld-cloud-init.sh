#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deployment_dir="${repository_root}/deploy/aworld-cloud"
environment_file="${deployment_dir}/.env"

if [[ ! -f "${environment_file}" ]]; then
  cp "${deployment_dir}/.env.example" "${environment_file}"
  echo "Created ${environment_file}"
fi

data_directory="$(sed -n 's/^AWORLD_CLOUD_DATA_DIR=//p' "${environment_file}" | tail -1)"
if [[ -z "${data_directory}" || "${data_directory}" != /* || "${data_directory}" == "/" ]]; then
  echo "AWORLD_CLOUD_DATA_DIR must be a non-root absolute path" >&2
  exit 1
fi

mkdir -p \
  "${data_directory}/cache" \
  "${data_directory}/home" \
  "${data_directory}/runs" \
  "${data_directory}/verification" \
  "${data_directory}/workspace-repos" \
  "${data_directory}/workspaces"

docker compose \
  --env-file "${environment_file}" \
  -f "${deployment_dir}/docker-compose.yml" \
  config --quiet

echo "AWorld Cloud data directory is ready: ${data_directory}"
