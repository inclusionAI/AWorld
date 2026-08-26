#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deployment_dir="${repository_root}/deploy/aworld-cloud"
environment_file="${AWORLD_CLOUD_ENV_FILE:-${deployment_dir}/.env}"

# shellcheck source=scripts/aworld-cloud-docker.sh
source "${repository_root}/scripts/aworld-cloud-docker.sh"

if [[ ! -f "${environment_file}" ]]; then
  mkdir -p "$(dirname "${environment_file}")"
  cp "${deployment_dir}/.env.example" "${environment_file}"
  echo "Created ${environment_file}"
fi

legacy_data_directory=/var/tmp/aworld-cloud
repo_data_directory="${repository_root}/.aworld-cloud"
data_directory="${AWORLD_CLOUD_DATA_DIR:-}"
persist_data_directory=false
data_directory_migrated=false
if [[ -n "${data_directory}" ]]; then
  persist_data_directory=true
else
  data_directory="$(
    aworld_cloud_env_value AWORLD_CLOUD_DATA_DIR "${environment_file}"
  )"
  if [[ -z "${data_directory}" ]]; then
    data_directory="${legacy_data_directory}"
    if [[ "$(uname -s)" == "Darwin" ]]; then
      data_directory="${repo_data_directory}"
    fi
    persist_data_directory=true
  elif [[ "$(uname -s)" == "Darwin" && "${data_directory}" == "${legacy_data_directory}" ]]; then
    data_directory="${repo_data_directory}"
    persist_data_directory=true
    data_directory_migrated=true
  fi
fi

if [[ "${data_directory}" != /* || "${data_directory}" == "/" ]]; then
  echo "AWORLD_CLOUD_DATA_DIR must be a non-root absolute path" >&2
  exit 1
fi
if [[ "${persist_data_directory}" == true ]]; then
  aworld_cloud_set_env_value \
    "${environment_file}" \
    AWORLD_CLOUD_DATA_DIR \
    "${data_directory}"
fi
if [[ "${data_directory_migrated}" == true ]]; then
  echo "Migrated legacy macOS data directory to ${data_directory}"
fi

aworld_cloud_export_docker_socket "${environment_file}"
aworld_cloud_set_env_value \
  "${environment_file}" \
  AWORLD_CLOUD_DOCKER_SOCKET \
  "${AWORLD_CLOUD_DOCKER_SOCKET}"

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
echo "Docker socket: ${AWORLD_CLOUD_DOCKER_SOCKET}"
