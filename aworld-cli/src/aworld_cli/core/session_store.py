from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSION_INDEX_RELATIVE_PATH = Path(".aworld") / "sessions" / "index.json"
LEGACY_SESSION_HISTORY_RELATIVE_PATH = Path(".aworld") / "workspaces" / ".session_history.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_cwd(cwd: str | os.PathLike[str] | None) -> str:
    return str(Path(cwd or os.getcwd()).expanduser().resolve())


def default_session_store_root() -> Path:
    override = os.environ.get("AWORLD_CLI_SESSION_STORE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd().resolve()


@contextmanager
def _locked_index_file(index_path: Path):
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = index_path.with_name(f"{index_path.name}.lock")
    with lock_path.open("a+b") as lock_handle:
        try:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            fcntl = None
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@dataclass
class CliSessionRecord:
    session_id: str
    created_at: str
    updated_at: str
    cwd: str
    agent_name: str
    mode: str
    source_type: str | None = None
    source_location: str | None = None
    title: str | None = None
    last_prompt: str | None = None
    last_task_id: str | None = None
    turn_count: int = 0
    archived: bool = False
    deleted_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cwd = normalize_cwd(self.cwd)
        self.mode = self.mode or "interactive"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cwd": self.cwd,
            "agent_name": self.agent_name,
            "mode": self.mode,
            "source_type": self.source_type,
            "source_location": self.source_location,
            "title": self.title,
            "last_prompt": self.last_prompt,
            "last_task_id": self.last_task_id,
            "turn_count": self.turn_count,
            "archived": self.archived,
            "deleted_at": self.deleted_at,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CliSessionRecord":
        now = utc_now_iso()
        return cls(
            session_id=str(data["session_id"]),
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or data.get("last_used_at") or now),
            cwd=str(data.get("cwd") or os.getcwd()),
            agent_name=str(data.get("agent_name") or "Aworld"),
            mode=str(data.get("mode") or "interactive"),
            source_type=data.get("source_type"),
            source_location=data.get("source_location"),
            title=data.get("title"),
            last_prompt=data.get("last_prompt"),
            last_task_id=data.get("last_task_id"),
            turn_count=int(data.get("turn_count") or 0),
            archived=bool(data.get("archived") or False),
            deleted_at=data.get("deleted_at"),
            metadata=dict(data.get("metadata") or {}),
        )


class CliSessionStore:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else default_session_store_root()
        self.index_path = self.root / SESSION_INDEX_RELATIVE_PATH
        self._records: dict[str, CliSessionRecord] = {}
        self._load()

    def _load(self) -> None:
        records = self._read_index_records()
        if records:
            self._records = records
            return
        if self.index_path.exists():
            self._records = {}
            return

        self._import_legacy_if_available()

    def _read_index_records(self) -> dict[str, CliSessionRecord]:
        if not self.index_path.exists():
            return {}
        records: dict[str, CliSessionRecord] = {}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            for raw in payload.get("sessions", []):
                record = CliSessionRecord.from_dict(raw)
                if not record.deleted_at:
                    records[record.session_id] = record
        except Exception:
            return {}
        return records

    def _import_legacy_if_available(self) -> None:
        legacy_path = self.root / LEGACY_SESSION_HISTORY_RELATIVE_PATH
        if not legacy_path.exists():
            return
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            return

        for session_id, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            created_at = str(raw.get("created_at") or utc_now_iso())
            updated_at = str(raw.get("last_used_at") or raw.get("updated_at") or created_at)
            record = CliSessionRecord(
                session_id=str(raw.get("session_id") or session_id),
                created_at=created_at,
                updated_at=updated_at,
                cwd=str(self.root),
                agent_name=str(raw.get("agent_name") or "Aworld"),
                mode=str(raw.get("mode") or "interactive"),
                metadata={"imported_from": str(LEGACY_SESSION_HISTORY_RELATIVE_PATH)},
            )
            self._records[record.session_id] = record
        if self._records:
            self._save()

    def _save(self, dirty_session_ids: set[str] | None = None) -> None:
        dirty_session_ids = set(dirty_session_ids or self._records.keys())
        with _locked_index_file(self.index_path):
            merged_records = self._read_index_records()
            for session_id in dirty_session_ids:
                record = self._records.get(session_id)
                if record is None:
                    continue
                if record.deleted_at:
                    merged_records.pop(session_id, None)
                else:
                    merged_records[session_id] = record
            payload = {
                "version": 1,
                "updated_at": utc_now_iso(),
                "sessions": [
                    record.to_dict()
                    for record in sorted(merged_records.values(), key=lambda item: item.updated_at)
                ],
            }
            temp_path = self.index_path.with_name(
                f"{self.index_path.name}.{os.getpid()}.{id(self)}.tmp"
            )
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, self.index_path)
            self._records = merged_records

    def upsert_session(self, record: CliSessionRecord) -> CliSessionRecord:
        self._records[record.session_id] = record
        self._save({record.session_id})
        return record

    def ensure_session(
        self,
        *,
        session_id: str,
        cwd: str | None,
        agent_name: str,
        mode: str = "interactive",
        source_type: str | None = None,
        source_location: str | None = None,
    ) -> CliSessionRecord:
        existing = self._records.get(session_id)
        now = utc_now_iso()
        if existing is not None:
            existing.cwd = normalize_cwd(cwd or existing.cwd)
            existing.agent_name = agent_name or existing.agent_name
            existing.mode = mode or existing.mode
            existing.source_type = source_type if source_type is not None else existing.source_type
            existing.source_location = (
                source_location if source_location is not None else existing.source_location
            )
            existing.updated_at = now
            self._save({existing.session_id})
            return existing

        return self.upsert_session(
            CliSessionRecord(
                session_id=session_id,
                created_at=now,
                updated_at=now,
                cwd=normalize_cwd(cwd),
                agent_name=agent_name or "Aworld",
                mode=mode or "interactive",
                source_type=source_type,
                source_location=source_location,
            )
        )

    def record_turn(
        self,
        *,
        session_id: str,
        cwd: str,
        agent_name: str,
        mode: str,
        prompt: str,
        task_id: str | None,
        source_type: str | None,
        source_location: str | None,
    ) -> CliSessionRecord:
        now = utc_now_iso()
        record = self._records.get(session_id)
        if record is None:
            record = CliSessionRecord(
                session_id=session_id,
                created_at=now,
                updated_at=now,
                cwd=normalize_cwd(cwd),
                agent_name=agent_name or "Aworld",
                mode=mode or "interactive",
                source_type=source_type,
                source_location=source_location,
            )
        record.updated_at = now
        record.cwd = normalize_cwd(cwd)
        record.agent_name = agent_name or record.agent_name
        record.mode = mode or record.mode
        record.source_type = source_type if source_type is not None else record.source_type
        record.source_location = source_location if source_location is not None else record.source_location
        record.last_prompt = self._prompt_preview(prompt)
        record.last_task_id = task_id
        record.turn_count += 1
        self._records[record.session_id] = record
        self._save({record.session_id})
        return record

    def touch(self, session_id: str) -> CliSessionRecord | None:
        record = self._records.get(session_id)
        if record is None:
            return None
        record.updated_at = utc_now_iso()
        self._save({record.session_id})
        return record

    def get(self, session_id: str) -> CliSessionRecord | None:
        return self._records.get(session_id)

    def list(
        self,
        *,
        cwd: str | None,
        include_all_cwds: bool = False,
        include_non_interactive: bool = False,
        include_archived: bool = False,
    ) -> list[CliSessionRecord]:
        normalized = normalize_cwd(cwd) if cwd else None
        records = []
        for record in self._records.values():
            if record.deleted_at:
                continue
            if record.archived and not include_archived:
                continue
            if record.mode != "interactive" and not include_non_interactive:
                continue
            if not include_all_cwds and normalized is not None and record.cwd != normalized:
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    def latest(
        self,
        *,
        cwd: str | None,
        include_all_cwds: bool = False,
        include_non_interactive: bool = False,
    ) -> CliSessionRecord | None:
        records = self.list(
            cwd=cwd,
            include_all_cwds=include_all_cwds,
            include_non_interactive=include_non_interactive,
        )
        return records[0] if records else None

    def context_warning(self, record: CliSessionRecord) -> str | None:
        artifact = (record.metadata or {}).get("context_artifact")
        artifacts = [artifact] if artifact else []
        if not artifacts and record.turn_count > 0:
            artifacts = self._default_context_artifacts(record.session_id)
        if not artifacts:
            return None
        for candidate in artifacts:
            artifact_path = Path(candidate)
            if not artifact_path.is_absolute():
                artifact_path = self.root / artifact_path
            if artifact_path.exists():
                return None
        artifact = artifacts[0]
        return (
            f"Session {record.session_id} is missing context artifact {artifact}; "
            "resume will continue with limited context."
        )

    @staticmethod
    def _safe_session_filename(session_id: str) -> str:
        safe_id = "".join(c for c in str(session_id) if c.isalnum() or c in ("-", "_")).strip()
        return safe_id or "default"

    def _default_context_artifacts(self, session_id: str) -> list[str]:
        safe_id = self._safe_session_filename(session_id)
        artifacts = [str(Path(".aworld") / "memory" / "sessions" / f"{safe_id}.jsonl")]
        runtime_root = os.environ.get("AWORLD_MEMORY_ROOT")
        if runtime_root:
            artifacts.append(str(Path(runtime_root).expanduser() / "sessions" / f"{safe_id}.jsonl"))
        else:
            artifacts.append(str(Path.home() / ".aworld" / "memory" / "sessions" / f"{safe_id}.jsonl"))
        return artifacts

    @staticmethod
    def _prompt_preview(prompt: str, limit: int = 200) -> str:
        normalized = " ".join(str(prompt or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."
