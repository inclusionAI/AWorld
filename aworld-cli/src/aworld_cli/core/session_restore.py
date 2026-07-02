from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .session_store import CliSessionRecord, CliSessionStore
from .session_transcript import CliSessionTranscript


@dataclass(frozen=True)
class SessionRestoreResult:
    record: CliSessionRecord
    message: str
    warning: str | None = None


class SessionRestoreError(Exception):
    """Raised when a stored session cannot be restored to an executor."""


def resolve_session_record(
    *,
    session_store: CliSessionStore,
    session_id: str | None,
    cwd: str,
    use_latest: bool = False,
    include_all_cwds: bool = False,
    include_non_interactive: bool = False,
) -> CliSessionRecord | None:
    if session_id:
        return session_store.get(session_id)
    if use_latest:
        return session_store.latest(
            cwd=cwd,
            include_all_cwds=include_all_cwds,
            include_non_interactive=include_non_interactive,
        )
    return None


def restore_session_to_executor(
    *,
    record: CliSessionRecord,
    executor_instance: Any,
    session_store: CliSessionStore,
    current_agent_name: str | None = None,
    current_cwd: str | None = None,
    require_same_agent: bool = True,
) -> SessionRestoreResult:
    if executor_instance is None:
        raise SessionRestoreError("No executor instance available.")

    if (
        require_same_agent
        and record.agent_name
        and current_agent_name
        and record.agent_name != current_agent_name
    ):
        raise SessionRestoreError(
            f"Session {record.session_id} belongs to agent {record.agent_name}. "
            "Switch to that agent before restoring."
        )

    executor_instance.session_id = record.session_id
    try:
        transcript = CliSessionTranscript(root=session_store.root)
        replay = transcript.build_replay(record.session_id)
        executor_instance._aworld_cli_restored_transcript = replay
        executor_instance._aworld_cli_restored_messages = transcript.render_for_openai_messages(record.session_id)
    except Exception:
        executor_instance._aworld_cli_restored_transcript = None
        executor_instance._aworld_cli_restored_messages = []

    if hasattr(executor_instance, "_start_tool_logging"):
        try:
            executor_instance._start_tool_logging()
        except Exception:
            pass

    runtime = getattr(executor_instance, "_base_runtime", None)
    if runtime is not None and hasattr(runtime, "reset_hud_session"):
        try:
            runtime.reset_hud_session(record.session_id)
        except Exception:
            pass

    session_store.touch(record.session_id)
    warnings = []
    if current_cwd:
        normalized_current = str(Path(current_cwd).expanduser().resolve())
        if record.cwd != normalized_current:
            warnings.append(
                f"Session {record.session_id} belongs to {record.cwd}; current cwd is {normalized_current}. "
                "Continuing because the session id was explicit."
            )
    context_warning = session_store.context_warning(record)
    if context_warning:
        warnings.append(context_warning)
    return SessionRestoreResult(
        record=record,
        message=f"Restored to session: {record.session_id}",
        warning="\n".join(warnings) if warnings else None,
    )
