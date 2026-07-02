from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TRANSCRIPT_RELATIVE_DIR = Path(".aworld") / "sessions" / "transcripts"
MODEL_CONTEXT_MAX_CHARS = 12000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_session_filename(session_id: str) -> str:
    safe_id = "".join(c for c in str(session_id) if c.isalnum() or c in ("-", "_")).strip()
    return safe_id or "default"


@dataclass(frozen=True)
class TranscriptReplay:
    session_id: str
    rendered_text: str
    source: str


class CliSessionTranscript:
    """Durable CLI-visible transcript used to repaint a resumed terminal."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        history_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else Path.cwd().resolve()
        self.history_path = (
            Path(history_path).expanduser().resolve()
            if history_path is not None
            else Path.home() / ".aworld" / "cli_history.jsonl"
        )

    def path_for(self, session_id: str) -> Path:
        return self.root / TRANSCRIPT_RELATIVE_DIR / f"{_safe_session_filename(session_id)}.jsonl"

    def record_turn(
        self,
        *,
        session_id: str,
        user_input: str,
        assistant_output: str,
        agent_name: str,
        task_id: str | None = None,
    ) -> None:
        if not session_id:
            return
        if not str(assistant_output or "").strip():
            return
        now = _utc_now_iso()
        events = [
            {
                "recorded_at": now,
                "event": "user",
                "session_id": session_id,
                "task_id": task_id,
                "content": str(user_input or ""),
            },
            {
                "recorded_at": now,
                "event": "assistant",
                "session_id": session_id,
                "task_id": task_id,
                "agent_name": agent_name or "Assistant",
                "content": str(assistant_output or ""),
            },
        ]
        path = self.path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n")

    def build_replay(self, session_id: str) -> TranscriptReplay | None:
        rendered = self.render_for_terminal(session_id)
        if not rendered:
            return None
        source = "transcript" if self.path_for(session_id).exists() else "legacy"
        return TranscriptReplay(session_id=session_id, rendered_text=rendered, source=source)

    def render_for_terminal(self, session_id: str) -> str:
        transcript_events = self._read_transcript_events(session_id)
        legacy_events = self._recover_legacy_events(session_id)
        events = self._dedupe_events(legacy_events + transcript_events)
        if events and transcript_events:
            return self._render_events(events, title="Previous session transcript")
        if events:
            return self._render_events(
                events,
                title="Recovered from session history and memory",
            )
        return ""

    def render_for_openai_messages(
        self,
        session_id: str,
        *,
        max_chars: int = MODEL_CONTEXT_MAX_CHARS,
    ) -> list[dict[str, str]]:
        events = self._dedupe_events(
            self._recover_legacy_events(session_id) + self._read_transcript_events(session_id)
        )
        messages: list[dict[str, str]] = []
        total_chars = 0
        for event in reversed(events):
            kind = str(event.get("event") or "")
            if kind not in ("user", "assistant"):
                continue
            content = str(event.get("content") or "").strip()
            if not content:
                continue
            total_chars += len(content)
            if total_chars > max_chars and messages:
                break
            messages.append({"role": kind, "content": content})
        return list(reversed(messages))

    @staticmethod
    def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        rich_content_keys = {
            (str(event.get("event") or ""), str(event.get("content") or "").strip())
            for event in events
            if str(event.get("content") or "").strip()
            and (str(event.get("task_id") or "") or str(event.get("recorded_at") or ""))
        }
        dangling_task_ids = {
            str(event.get("task_id") or "")
            for event in events
            if str(event.get("event") or "") == "assistant"
            and not str(event.get("content") or "").strip()
            and str(event.get("task_id") or "")
        }
        for event in events:
            kind = str(event.get("event") or "")
            content = str(event.get("content") or "").strip()
            if not content:
                continue
            if kind == "user" and str(event.get("task_id") or "") in dangling_task_ids:
                continue
            if (
                not str(event.get("task_id") or "")
                and not str(event.get("recorded_at") or "")
                and (kind, content) in rich_content_keys
            ):
                continue
            key = (
                kind,
                content,
                str(event.get("task_id") or ""),
                str(event.get("recorded_at") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        return deduped

    def _read_transcript_events(self, session_id: str) -> list[dict[str, Any]]:
        path = self.path_for(session_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("session_id") == session_id:
                    events.append(event)
        except OSError:
            return []
        return events

    def _recover_legacy_events(self, session_id: str) -> list[dict[str, Any]]:
        users = self._read_legacy_user_inputs(session_id)
        answers = self._read_session_memory_answers(session_id)
        events: list[dict[str, Any]] = []
        used_user_indexes: set[int] = set()
        previous_answer_ts = -1
        for answer_ts, answer in answers:
            user_index = self._find_user_for_answer(
                users,
                used_user_indexes=used_user_indexes,
                previous_answer_ts=previous_answer_ts,
                answer_ts=answer_ts,
            )
            if user_index is not None:
                used_user_indexes.add(user_index)
                events.append(
                    {
                        "event": "user",
                        "session_id": session_id,
                        "content": users[user_index][1],
                    }
                )
            events.append(
                {
                    "event": "assistant",
                    "session_id": session_id,
                    "agent_name": "Aworld",
                    "content": answer,
                }
            )
            previous_answer_ts = answer_ts
        for index, (timestamp, prompt) in enumerate(users):
            if index in used_user_indexes:
                continue
            if timestamp <= previous_answer_ts:
                continue
            events.append(
                {
                    "event": "user",
                    "session_id": session_id,
                    "content": prompt,
                }
            )
        return events

    @staticmethod
    def _find_user_for_answer(
        users: list[tuple[int, str]],
        *,
        used_user_indexes: set[int],
        previous_answer_ts: int,
        answer_ts: int,
    ) -> int | None:
        candidates = [
            index
            for index, (timestamp, _) in enumerate(users)
            if index not in used_user_indexes and previous_answer_ts < timestamp <= answer_ts
        ]
        if candidates:
            return candidates[-1]
        for index, _ in enumerate(users):
            if index not in used_user_indexes:
                return index
        return None

    def _read_legacy_user_inputs(self, session_id: str) -> list[tuple[int, str]]:
        if not self.history_path.exists():
            return []
        prompts: list[tuple[int, str]] = []
        try:
            for line in self.history_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("sessionId") != session_id:
                    continue
                display = str(record.get("display") or "").strip()
                if display:
                    prompts.append((int(record.get("timestamp") or 0), display))
        except OSError:
            return []
        return sorted(prompts, key=lambda item: item[0])

    def _read_session_memory_answers(self, session_id: str) -> list[tuple[int, str]]:
        answers: list[tuple[int, str]] = []
        for path in self._session_memory_paths(session_id):
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("session_id") != session_id:
                        continue
                    if record.get("event") != "task_completed":
                        continue
                    answer = str(record.get("final_answer") or "").strip()
                    if answer:
                        answers.append((self._timestamp_ms(record.get("recorded_at")), answer))
            except OSError:
                continue
            if answers:
                break
        return sorted(answers, key=lambda item: item[0])

    @staticmethod
    def _timestamp_ms(value: Any) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if not value:
            return 0
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)

    def _session_memory_paths(self, session_id: str) -> list[Path]:
        filename = f"{_safe_session_filename(session_id)}.jsonl"
        paths = [self.root / ".aworld" / "memory" / "sessions" / filename]
        runtime_root = os.environ.get("AWORLD_MEMORY_ROOT")
        if runtime_root:
            paths.append(Path(runtime_root).expanduser() / "sessions" / filename)
        paths.append(Path.home() / ".aworld" / "memory" / "sessions" / filename)
        return paths

    @staticmethod
    def _render_events(events: list[dict[str, Any]], *, title: str) -> str:
        lines = [f"── {title} ──"]
        for event in events:
            kind = event.get("event")
            content = str(event.get("content") or "").strip()
            if not content:
                continue
            if kind == "user":
                lines.append(f"You: {content}")
            elif kind == "assistant":
                agent_name = str(event.get("agent_name") or "Assistant").strip() or "Assistant"
                lines.append(f"{agent_name}:")
                lines.append(content)
            elif kind == "system":
                lines.append(content)
            lines.append("")
        return "\n".join(lines).rstrip()
