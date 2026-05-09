from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CACHE_USAGE_KEYS = {
    "cache_hit_tokens",
    "cache_write_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
}


@dataclass(frozen=True)
class CacheRequestObservation:
    recorded_at: str
    request_id: str | None
    provider_request_id: str | None
    session_id: str | None
    task_id: str | None
    provider_name: str | None
    model: str | None
    cache_hit_tokens: int
    cache_write_tokens: int
    cached_tokens: int


@dataclass(frozen=True)
class CachePrefixCandidate:
    occurrences: int
    request_ids: tuple[str, ...]
    providers: tuple[str, ...]
    models: tuple[str, ...]
    avg_cache_hit_tokens: int
    avg_cache_write_tokens: int
    preview: str


@dataclass(frozen=True)
class CacheObservabilitySummary:
    sessions_dir: Path
    session_files: tuple[Path, ...]
    session_id: str | None
    task_id: str | None
    total_llm_calls: int
    calls_with_cache_usage: int
    total_cache_hit_tokens: int
    total_cache_write_tokens: int
    by_model: dict[str, int]
    recent_requests: tuple[CacheRequestObservation, ...]
    prefix_candidates: tuple[CachePrefixCandidate, ...]


def session_logs_dir(workspace_path: str | os.PathLike[str]) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "sessions"


def summarize_cache_observability(
    workspace_path: str | os.PathLike[str],
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    max_records: int = 500,
    recent_limit: int = 5,
    prefix_message_limit: int = 3,
    top_prefixes: int = 5,
) -> CacheObservabilitySummary:
    sessions_dir = session_logs_dir(workspace_path)
    if not sessions_dir.exists():
        return CacheObservabilitySummary(
            sessions_dir=sessions_dir,
            session_files=(),
            session_id=_coerce_text(session_id),
            task_id=_coerce_text(task_id),
            total_llm_calls=0,
            calls_with_cache_usage=0,
            total_cache_hit_tokens=0,
            total_cache_write_tokens=0,
            by_model={},
            recent_requests=(),
            prefix_candidates=(),
        )

    session_files = tuple(
        sorted(
            sessions_dir.glob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    )

    total_llm_calls = 0
    calls_with_cache_usage = 0
    total_cache_hit_tokens = 0
    total_cache_write_tokens = 0
    by_model: Counter[str] = Counter()
    recent_requests: list[CacheRequestObservation] = []
    prefix_buckets: dict[str, dict[str, Any]] = {}
    scanned = 0
    normalized_session_id = _coerce_text(session_id)
    normalized_task_id = _coerce_text(task_id)

    for session_file in session_files:
        try:
            lines = session_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for line in reversed(lines):
            if scanned >= max_records:
                break
            scanned += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            recorded_at = str(payload.get("recorded_at") or "")
            llm_calls = payload.get("llm_calls")
            if not isinstance(llm_calls, list):
                continue

            payload_session_id = _coerce_text(payload.get("session_id"))
            payload_task_id = _coerce_text(payload.get("task_id"))

            for llm_call in reversed(llm_calls):
                if not isinstance(llm_call, dict):
                    continue

                llm_call_session_id = _coerce_text(llm_call.get("session_id")) or payload_session_id
                llm_call_task_id = _coerce_text(llm_call.get("task_id")) or payload_task_id
                if normalized_session_id is not None and llm_call_session_id != normalized_session_id:
                    continue
                if normalized_task_id is not None and llm_call_task_id != normalized_task_id:
                    continue
                total_llm_calls += 1

                provider_name = _coerce_text(llm_call.get("provider_name"))
                model = _coerce_text(llm_call.get("model"))
                if model:
                    by_model[model] += 1

                usage_raw = llm_call.get("usage_raw")
                if not isinstance(usage_raw, dict):
                    usage_raw = {}
                cache_hit_tokens = _coerce_int(usage_raw.get("cache_hit_tokens"))
                cache_write_tokens = _coerce_int(usage_raw.get("cache_write_tokens"))
                cached_tokens = _coerce_int(
                    (usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens")
                    if isinstance(usage_raw.get("prompt_tokens_details"), dict)
                    else 0
                )
                has_cache_usage = (
                    cache_hit_tokens > 0
                    or cache_write_tokens > 0
                    or cached_tokens > 0
                    or any(key in usage_raw for key in CACHE_USAGE_KEYS)
                )
                if has_cache_usage:
                    calls_with_cache_usage += 1
                total_cache_hit_tokens += cache_hit_tokens
                total_cache_write_tokens += cache_write_tokens

                recent_requests.append(
                    CacheRequestObservation(
                        recorded_at=recorded_at,
                        request_id=_coerce_text(llm_call.get("request_id")),
                        provider_request_id=_coerce_text(llm_call.get("provider_request_id")),
                        session_id=llm_call_session_id,
                        task_id=llm_call_task_id,
                        provider_name=provider_name,
                        model=model,
                        cache_hit_tokens=cache_hit_tokens,
                        cache_write_tokens=cache_write_tokens,
                        cached_tokens=cached_tokens,
                    )
                )

                messages = _normalized_messages(llm_call.get("request"))
                if not messages:
                    continue
                for prefix_size in range(1, min(len(messages), prefix_message_limit) + 1):
                    prefix = messages[:prefix_size]
                    key = json.dumps(
                        {
                            "provider_name": provider_name,
                            "model": model,
                            "messages": prefix,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    bucket = prefix_buckets.setdefault(
                        key,
                        {
                            "messages": prefix,
                            "request_ids": set(),
                            "providers": set(),
                            "models": set(),
                            "cache_hit_tokens": 0,
                            "cache_write_tokens": 0,
                        },
                    )
                    request_id = _coerce_text(llm_call.get("request_id"))
                    if request_id:
                        bucket["request_ids"].add(request_id)
                    if provider_name:
                        bucket["providers"].add(provider_name)
                    if model:
                        bucket["models"].add(model)
                    bucket["cache_hit_tokens"] += cache_hit_tokens
                    bucket["cache_write_tokens"] += cache_write_tokens

        if scanned >= max_records:
            break

    recent_requests.sort(key=lambda item: item.recorded_at, reverse=True)
    prefix_candidates: list[CachePrefixCandidate] = []
    for bucket in prefix_buckets.values():
        request_ids = tuple(sorted(bucket["request_ids"]))
        if len(request_ids) < 2:
            continue
        preview = _prefix_preview(bucket["messages"])
        if not preview:
            continue
        occurrences = len(request_ids)
        prefix_candidates.append(
            CachePrefixCandidate(
                occurrences=occurrences,
                request_ids=request_ids,
                providers=tuple(sorted(bucket["providers"])),
                models=tuple(sorted(bucket["models"])),
                avg_cache_hit_tokens=int(bucket["cache_hit_tokens"] / occurrences),
                avg_cache_write_tokens=int(bucket["cache_write_tokens"] / occurrences),
                preview=preview,
            )
        )

    prefix_candidates.sort(
        key=lambda item: (
            item.occurrences,
            len(item.preview),
            item.avg_cache_hit_tokens,
            item.avg_cache_write_tokens,
        ),
        reverse=True,
    )
    deduped_candidates: list[CachePrefixCandidate] = []
    seen_request_groups: set[tuple[str, ...]] = set()
    for item in prefix_candidates:
        if item.request_ids in seen_request_groups:
            continue
        seen_request_groups.add(item.request_ids)
        deduped_candidates.append(item)

    return CacheObservabilitySummary(
        sessions_dir=sessions_dir,
        session_files=session_files,
        session_id=normalized_session_id,
        task_id=normalized_task_id,
        total_llm_calls=total_llm_calls,
        calls_with_cache_usage=calls_with_cache_usage,
        total_cache_hit_tokens=total_cache_hit_tokens,
        total_cache_write_tokens=total_cache_write_tokens,
        by_model=dict(sorted(by_model.items())),
        recent_requests=tuple(recent_requests[: max(recent_limit, 0)]),
        prefix_candidates=tuple(deduped_candidates[: max(top_prefixes, 0)]),
    )


def format_cache_observability_summary(summary: CacheObservabilitySummary) -> str:
    if summary.total_llm_calls == 0:
        return (
            "No cache observability data found.\n"
            f"Expected session logs under: {summary.sessions_dir}"
        )

    lines = [
        "Cache observability summary",
        "Read-only analysis from workspace session logs.",
        f"Session log directory: {summary.sessions_dir}",
        f"Session log files: {len(summary.session_files)}",
        f"LLM calls analyzed: {summary.total_llm_calls}",
        f"Calls with cache usage: {summary.calls_with_cache_usage}",
        f"Total cache hit tokens: {summary.total_cache_hit_tokens}",
        f"Total cache write tokens: {summary.total_cache_write_tokens}",
    ]
    if summary.session_id is not None:
        lines.append(f"Scoped session_id: {summary.session_id}")
    if summary.task_id is not None:
        lines.append(f"Scoped task_id: {summary.task_id}")

    if summary.by_model:
        lines.append("Models:")
        for model, count in summary.by_model.items():
            lines.append(f"- {model}: {count}")

    if summary.recent_requests:
        lines.append("Recent request-linked cache observations:")
        for item in summary.recent_requests:
            lines.append(
                "- "
                f"{item.request_id or 'unknown-request'}"
                f" provider={item.provider_request_id or 'n/a'}"
                f" provider_name={item.provider_name or 'unknown'}"
                f" model={item.model or 'unknown'}"
                f" session={item.session_id or 'unknown'}"
                f" task={item.task_id or 'unknown'}"
                f" cache_hit={item.cache_hit_tokens}"
                f" cache_write={item.cache_write_tokens}"
                f" cached_tokens={item.cached_tokens}"
            )

    if summary.prefix_candidates:
        lines.append("Stable cacheable prefix candidates:")
        for index, item in enumerate(summary.prefix_candidates, start=1):
            lines.append(
                f"{index}. occurrences={item.occurrences} "
                f"avg_cache_hit={item.avg_cache_hit_tokens} "
                f"avg_cache_write={item.avg_cache_write_tokens} "
                f"providers={', '.join(item.providers) or 'unknown'} "
                f"models={', '.join(item.models) or 'unknown'}"
            )
            lines.append(f"   request_ids={', '.join(item.request_ids)}")
            lines.append(f"   preview={item.preview}")
    else:
        lines.append("Stable cacheable prefix candidates: none yet")

    return "\n".join(lines)


def _normalized_messages(request: Any) -> list[dict[str, Any]]:
    if not isinstance(request, dict):
        return []
    messages = request.get("messages")
    if not isinstance(messages, list):
        return []

    normalized: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        item: dict[str, Any] = {}
        role = _coerce_text(message.get("role"))
        if role:
            item["role"] = role
        content = message.get("content")
        if isinstance(content, str):
            item["content"] = " ".join(content.split())
        elif content is not None:
            item["content"] = json.dumps(content, ensure_ascii=False, sort_keys=True)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            item["tool_calls"] = [
                _coerce_text((tool_call.get("function") or {}).get("name"))
                for tool_call in tool_calls
                if isinstance(tool_call, dict)
            ]
        if item:
            normalized.append(item)
    return normalized


def _prefix_preview(messages: list[dict[str, Any]]) -> str:
    parts = []
    for message in messages[:2]:
        role = _coerce_text(message.get("role")) or "unknown"
        content = _coerce_text(message.get("content")) or ""
        snippet = content[:100]
        if len(content) > 100:
            snippet = f"{snippet}..."
        parts.append(f"[{role}] {snippet}".strip())
    return " | ".join(parts)


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value)


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
