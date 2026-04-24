from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Sequence

from .validation import run_phase1_validation_against_stdio_command
from .validation_assets import (
    expand_env_placeholders,
    list_validation_config_fields,
    load_validation_template,
    list_validation_template_names,
    validate_validation_config_shape,
    validation_config_schema_path,
    validation_template_metadata,
)
from .validation_profiles import list_phase1_validation_profiles, resolve_phase1_validation_profile


def parse_env_assignments(assignments: Sequence[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for assignment in assignments or []:
        if "=" not in assignment:
            raise ValueError(f"Invalid --env assignment: {assignment!r}. Expected KEY=VALUE.")
        key, value = assignment.split("=", 1)
        if not key:
            raise ValueError(f"Invalid --env assignment: {assignment!r}. Key must not be empty.")
        parsed[key] = value
    return parsed


def _parse_json_object(raw: str | None, *, option_name: str) -> dict[str, Any]:
    if not raw:
        return {}

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{option_name} must decode to an object.")
    return payload


def parse_env_json(raw: str | None) -> dict[str, str]:
    payload = _parse_json_object(raw, option_name="--env-json")
    return {str(key): str(value) for key, value in payload.items()}


def parse_session_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {"cwd": ".", "mcpServers": []}

    return _parse_json_object(raw, option_name="--session-params-json")


def parse_startup_timeout_seconds(raw: float | None) -> float | None:
    if raw is None:
        return None
    if raw <= 0:
        raise ValueError("--startup-timeout-seconds must be greater than 0.")
    return raw


def parse_startup_retries(raw: int) -> int:
    if raw < 0:
        raise ValueError("--startup-retries must be >= 0.")
    return raw


def load_validate_stdio_host_config(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--config-file must contain a JSON object.")
    validated = validate_validation_config_shape(payload)

    topology_name = validated.get("topology")
    if topology_name is not None:
        base_template = load_validation_template(
            str(topology_name),
            expand_placeholders=False,
        )
        merged_payload = {
            **base_template,
            **validated,
            "env": {
                **(
                    base_template.get("env")
                    if isinstance(base_template.get("env"), dict)
                    else {}
                ),
                **(validated.get("env") if isinstance(validated.get("env"), dict) else {}),
            },
        }
    else:
        merged_payload = validated

    return expand_env_placeholders(
        merged_payload,
        dict(os.environ),
    )


def resolve_validate_stdio_host_request(
    *,
    config: dict[str, Any],
    topology: str | None,
    command: str | None,
    cwd: str | Path,
    env_assignments: Sequence[str] | None,
    env_json: str | None,
    profile_name: str,
    session_params_json: str | None,
    startup_timeout_seconds: float | None,
    startup_retries: int,
) -> dict[str, Any]:
    if config and topology is not None:
        raise ValueError("--config-file and --topology cannot be used together.")

    template_env = {
        **dict(os.environ),
        **parse_env_json(env_json),
        **parse_env_assignments(env_assignments),
    }
    if topology is not None:
        config = load_validation_template(
            topology,
            expand_placeholders=True,
            env=template_env,
        )

    config_env = config.get("env")
    if config_env is not None and not isinstance(config_env, dict):
        raise ValueError("Validation config field 'env' must be an object.")

    request_command = command or config.get("command")
    if not request_command:
        raise ValueError("validate-stdio-host requires a command from --command or --config-file.")

    request_cwd = cwd if str(cwd) != "." else config.get("cwd", cwd)
    request_profile = profile_name if profile_name != "self-test" else config.get("profile", profile_name)
    request_session_params = (
        parse_session_params(session_params_json)
        if session_params_json
        else config.get("sessionParams", {"cwd": ".", "mcpServers": []})
    )
    if not isinstance(request_session_params, dict):
        raise ValueError("Validation config field 'sessionParams' must be an object.")

    request_env = {
        **dict(os.environ),
        **{str(key): str(value) for key, value in (config_env or {}).items()},
        **template_env,
    }
    request_startup_timeout_seconds = (
        parse_startup_timeout_seconds(startup_timeout_seconds)
        if startup_timeout_seconds is not None
        else parse_startup_timeout_seconds(config.get("startupTimeoutSeconds"))
    )
    request_startup_retries = (
        parse_startup_retries(startup_retries)
        if startup_retries != 0
        else parse_startup_retries(int(config.get("startupRetries", 0)))
    )
    return {
        "command": request_command,
        "cwd": request_cwd,
        "env": request_env,
        "profile_name": request_profile,
        "session_params": request_session_params,
        "startup_timeout_seconds": request_startup_timeout_seconds,
        "startup_retries": request_startup_retries,
    }


async def run_validate_stdio_host(
    *,
    command: str | None,
    config_file: str | Path | None,
    topology: str | None,
    cwd: str | Path,
    env_assignments: Sequence[str] | None,
    env_json: str | None,
    profile_name: str,
    session_params_json: str | None,
    startup_timeout_seconds: float | None,
    startup_retries: int,
) -> int:
    try:
        request = resolve_validate_stdio_host_request(
            config=load_validate_stdio_host_config(config_file),
            topology=topology,
            command=command,
            cwd=cwd,
            env_assignments=env_assignments,
            env_json=env_json,
            profile_name=profile_name,
            session_params_json=session_params_json,
            startup_timeout_seconds=startup_timeout_seconds,
            startup_retries=startup_retries,
        )
        payload = await run_phase1_validation_against_stdio_command(
            command=shlex.split(str(request["command"])),
            cwd=request["cwd"],
            env=request["env"],
            profile=resolve_phase1_validation_profile(str(request["profile_name"])),
            session_params=request["session_params"],
            startup_timeout_seconds=request["startup_timeout_seconds"],
            startup_retries=int(request["startup_retries"]),
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "summary": {"passed": 0, "failed": 0, "skipped": 0},
            "cases": [],
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
        exit_code = 2
    else:
        exit_code = 0 if payload["ok"] else 1

    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return exit_code


def build_validate_stdio_host_help() -> dict[str, Any]:
    return {
        "profiles": list_phase1_validation_profiles(),
        "defaultSessionParams": {"cwd": ".", "mcpServers": []},
        "configFileFields": [
            "topology",
            "command",
            "cwd",
            "profile",
            "sessionParams",
            "env",
            "startupTimeoutSeconds",
            "startupRetries",
        ],
        "defaultStartupRetries": 0,
        "defaultStartupTimeoutSeconds": None,
        "topologies": validation_template_metadata(),
        "configAllowedFields": list_validation_config_fields(),
        "configSchemaPath": validation_config_schema_path(),
        "validateHostTopologyNames": list_validation_template_names(),
    }


def render_validation_config(
    *,
    topology: str,
    expand_placeholders_flag: bool,
    env_assignments: Sequence[str] | None,
) -> dict[str, Any]:
    env = {**dict(os.environ), **parse_env_assignments(env_assignments)}
    return load_validation_template(
        topology,
        expand_placeholders=expand_placeholders_flag,
        env=env,
    )


def write_rendered_validation_config(
    payload: dict[str, Any],
    *,
    output_file: str | Path | None,
) -> None:
    if output_file is None:
        return
    Path(output_file).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
