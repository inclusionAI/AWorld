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

aworld_cloud_docker_socket_works() {
  local candidate="$1"
  if [[ "${candidate}" != /* ]] || ! command -v docker >/dev/null 2>&1; then
    return 1
  fi

  docker run --rm --network none \
    --mount "type=bind,source=${candidate},target=/var/run/docker.sock" \
    docker:27.5.1-cli \
    version --format '{{.Server.Version}}' >/dev/null 2>&1
}

aworld_cloud_detect_docker_socket() {
  local environment_file="$1"
  local explicit="${AWORLD_CLOUD_DOCKER_SOCKET:-}"
  local persisted=""
  local docker_host="${DOCKER_HOST:-}"
  local context_endpoint=""
  local candidate=""
  local -a candidates=(/var/run/docker.sock)

  if [[ -n "${explicit}" ]]; then
    if [[ "${explicit}" != /* ]]; then
      echo "AWORLD_CLOUD_DOCKER_SOCKET must be an absolute path" >&2
      return 1
    fi
    if ! aworld_cloud_docker_socket_works "${explicit}"; then
      echo "AWORLD_CLOUD_DOCKER_SOCKET cannot be mounted and reached by Docker: ${explicit}" >&2
      return 1
    fi
    printf '%s\n' "${explicit}"
    return 0
  fi

  persisted="$(
    aworld_cloud_env_value AWORLD_CLOUD_DOCKER_SOCKET "${environment_file}"
  )"
  if [[ -n "${persisted}" ]]; then
    if aworld_cloud_docker_socket_works "${persisted}"; then
      printf '%s\n' "${persisted}"
      return 0
    fi
    echo "Ignoring unusable persisted Docker socket: ${persisted}" >&2
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker CLI is required to validate the worker socket mount" >&2
    return 1
  fi

  if [[ -n "${docker_host}" ]]; then
    candidate="$(aworld_cloud_unix_socket_from_endpoint "${docker_host}" || true)"
    if [[ -n "${candidate}" ]]; then
      candidates+=("${candidate}")
    fi
  fi

  context_endpoint="$(
    docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null \
      | head -1 || true
  )"
  candidate="$(
    aworld_cloud_unix_socket_from_endpoint "${context_endpoint}" || true
  )"
  if [[ -n "${candidate}" ]]; then
    candidates+=("${candidate}")
  fi

  if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    candidates+=("${XDG_RUNTIME_DIR}/docker.sock")
  fi
  if [[ -n "${HOME:-}" ]]; then
    candidates+=(
      "${HOME}/.docker/run/docker.sock"
      "${HOME}/.docker/desktop/docker.sock"
    )
  fi
  for candidate in "${candidates[@]}"; do
    if aworld_cloud_docker_socket_works "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  echo "No Docker socket candidate could be mounted and reached from a container" >&2
  echo "Set AWORLD_CLOUD_DOCKER_SOCKET to a daemon-visible Unix socket path" >&2
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
