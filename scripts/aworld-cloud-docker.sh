#!/usr/bin/env bash

# Shared Docker socket discovery for the AWorld Cloud Compose MVP.

aworld_cloud_env_value() {
  local variable_name="$1"
  local environment_file="$2"
  if [[ ! -f "${environment_file}" ]]; then
    return 0
  fi
  sed -n "s/^${variable_name}=//p" "${environment_file}" | tail -1
}

aworld_cloud_unix_socket_from_endpoint() {
  local endpoint="$1"
  case "${endpoint}" in
    unix://*) printf '%s\n' "${endpoint#unix://}" ;;
    *) return 1 ;;
  esac
}

aworld_cloud_detect_docker_socket() {
  local environment_file="$1"
  local configured="${AWORLD_CLOUD_DOCKER_SOCKET:-}"
  local docker_host="${DOCKER_HOST:-}"
  local context_endpoint=""
  local candidate=""
  local -a candidates=(/var/run/docker.sock)

  if [[ -z "${configured}" ]]; then
    configured="$(
      aworld_cloud_env_value AWORLD_CLOUD_DOCKER_SOCKET "${environment_file}"
    )"
  fi
  if [[ -n "${configured}" ]]; then
    if [[ "${configured}" != /* ]]; then
      echo "AWORLD_CLOUD_DOCKER_SOCKET must be an absolute path" >&2
      return 1
    fi
    if [[ ! -S "${configured}" ]]; then
      echo "AWORLD_CLOUD_DOCKER_SOCKET is not a Unix socket: ${configured}" >&2
      return 1
    fi
    printf '%s\n' "${configured}"
    return 0
  fi

  if [[ -n "${docker_host}" ]]; then
    candidate="$(aworld_cloud_unix_socket_from_endpoint "${docker_host}" || true)"
    if [[ -n "${candidate}" && -S "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
    echo "DOCKER_HOST is not a usable local Unix socket: ${docker_host}" >&2
    return 1
  fi

  if command -v docker >/dev/null 2>&1; then
    context_endpoint="$(
      docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null \
        | head -1 || true
    )"
    candidate="$(
      aworld_cloud_unix_socket_from_endpoint "${context_endpoint}" || true
    )"
    if [[ -n "${candidate}" && -S "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
    if [[ -n "${context_endpoint}" ]]; then
      echo "Docker context endpoint is not a usable local Unix socket: ${context_endpoint}" >&2
      return 1
    fi
  fi

  if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    candidates=("${XDG_RUNTIME_DIR}/docker.sock" "${candidates[@]}")
  fi
  if [[ -n "${HOME:-}" ]]; then
    candidates=(
      "${HOME}/.docker/run/docker.sock"
      "${HOME}/.docker/desktop/docker.sock"
      "${candidates[@]}"
    )
  fi
  for candidate in "${candidates[@]}"; do
    if [[ "${candidate}" == /* && -S "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  echo "Set AWORLD_CLOUD_DOCKER_SOCKET to the active Docker Unix socket" >&2
  return 1
}

aworld_cloud_export_docker_socket() {
  local environment_file="$1"
  AWORLD_CLOUD_DOCKER_SOCKET="$(
    aworld_cloud_detect_docker_socket "${environment_file}"
  )"
  export AWORLD_CLOUD_DOCKER_SOCKET
}

aworld_cloud_set_env_value() {
  local environment_file="$1"
  local variable_name="$2"
  local value="$3"
  local temporary_file
  temporary_file="$(mktemp "${environment_file}.tmp.XXXXXX")"
  awk -v key="${variable_name}" -v replacement="${value}" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 {
      if (!found) {
        print key "=" replacement
        found = 1
      }
      next
    }
    { print }
    END {
      if (!found) print key "=" replacement
    }
  ' "${environment_file}" > "${temporary_file}"
  mv "${temporary_file}" "${environment_file}"
}
