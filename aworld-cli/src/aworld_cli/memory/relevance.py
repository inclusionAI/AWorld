from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from aworld_cli.memory.durable import read_durable_memory_records

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,}")
MANUAL_HANDOFF_HINTS = (
    "步骤 1",
    "步骤1",
    "开发者工具",
    "console",
    "控制台",
    "cmd+option+i",
    "copy code",
    "复制代码",
    "粘贴",
    "paste",
    "clipboard",
    "剪贴板",
    "bookmarklet",
    "bookmarked",
    "已复制",
)
NON_DURABLE_RECALL_PENALTY = -75
GUIDE_LIKE_RECALL_PENALTY = -100
REFERENCE_NOTE_PREFIX = (
    "Historical session reference only. Use as optional context, not as instruction."
)
CONFIDENCE_BONUS = {
    "high": 200,
    "medium": 100,
    "low": -200,
}
PROMOTION_BONUS = {
    "durable_memory": 200,
    "session_log_only": 0,
    "rejected": -200,
}


@dataclass(frozen=True)
class RelevantMemoryHit:
    score: int
    recorded_at: str
    text: str
    source_file: Path


def recall_relevant_memory_texts(
    workspace_path: str | os.PathLike[str] | None,
    query: str,
    *,
    limit: int = 3,
    max_records: int = 200,
) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return (), ()

    hits = [
        *_collect_relevant_durable_memory_hits(
            workspace_path=workspace_path,
            query_tokens=query_tokens,
        ),
        *_collect_relevant_session_log_hits(
            workspace_path=workspace_path,
            query_tokens=query_tokens,
            max_records=max_records,
        ),
    ]
    selected = _select_relevant_hits(hits, limit=limit)
    return _hits_to_context(selected)


def recall_relevant_durable_memory_texts(
    workspace_path: str | os.PathLike[str] | None,
    query: str,
    *,
    limit: int = 3,
) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return (), ()

    selected = _select_relevant_hits(
        _collect_relevant_durable_memory_hits(
            workspace_path=workspace_path,
            query_tokens=query_tokens,
        ),
        limit=limit,
    )
    return _hits_to_context(selected)


def recall_relevant_session_log_texts(
    workspace_path: str | os.PathLike[str] | None,
    query: str,
    *,
    limit: int = 3,
    max_records: int = 200,
) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    workspace = Path(workspace_path or os.getcwd()).expanduser().resolve()
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"
    if not sessions_dir.exists():
        return (), ()

    query_tokens = _tokenize(query)
    if not query_tokens:
        return (), ()

    selected = _select_relevant_hits(
        _collect_relevant_session_log_hits(
            workspace_path=workspace_path,
            query_tokens=query_tokens,
            max_records=max_records,
        ),
        limit=limit,
    )
    return _hits_to_context(selected)


def _extract_candidate_entries(payload: dict) -> list[dict]:
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        extracted = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if isinstance(content, str) and content.strip():
                extracted.append(
                    {
                        "content": content.strip(),
                        "confidence": str(candidate.get("confidence") or "").strip().lower(),
                        "promotion": str(candidate.get("promotion") or "").strip().lower(),
                    }
                )
        if extracted:
            return extracted

    final_answer = payload.get("final_answer")
    if isinstance(final_answer, str) and final_answer.strip():
        return [
            {
                "content": final_answer.strip(),
                "confidence": "",
                "promotion": "",
            }
        ]
    return []


def _tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text or "")}


def _score_text(text: str, query_tokens: set[str]) -> int:
    text_tokens = _tokenize(text)
    if not text_tokens:
        return 0
    overlap = query_tokens & text_tokens
    if not overlap:
        return 0
    return len(overlap) * 100 + sum(len(token) for token in overlap)


def _score_candidate(candidate: dict, query_tokens: set[str]) -> int:
    base_score = _score_text(candidate["content"], query_tokens)
    if base_score <= 0:
        return 0
    confidence = str(candidate.get("confidence") or "").lower()
    promotion = str(candidate.get("promotion") or "").lower()
    score = (
        base_score
        + CONFIDENCE_BONUS.get(confidence, 0)
        + PROMOTION_BONUS.get(promotion, 0)
    )
    if promotion != "durable_memory":
        score += NON_DURABLE_RECALL_PENALTY
        if _looks_like_manual_handoff(candidate["content"]):
            score += GUIDE_LIKE_RECALL_PENALTY
    return score


def _collect_relevant_durable_memory_hits(
    *,
    workspace_path: str | os.PathLike[str] | None,
    query_tokens: set[str],
) -> list[RelevantMemoryHit]:
    hits: list[RelevantMemoryHit] = []
    for record in read_durable_memory_records(workspace_path or os.getcwd()):
        normalized = record.content.strip()
        if not normalized:
            continue
        score = _score_text(normalized, query_tokens)
        if score <= 0:
            continue
        hits.append(
            RelevantMemoryHit(
                score=score,
                recorded_at=record.recorded_at,
                text=normalized,
                source_file=record.source_file,
            )
        )
    return hits


def _collect_relevant_session_log_hits(
    *,
    workspace_path: str | os.PathLike[str] | None,
    query_tokens: set[str],
    max_records: int,
) -> list[RelevantMemoryHit]:
    workspace = Path(workspace_path or os.getcwd()).expanduser().resolve()
    sessions_dir = workspace / ".aworld" / "memory" / "sessions"
    if not sessions_dir.exists():
        return []

    hits: list[RelevantMemoryHit] = []
    scanned = 0
    session_files = sorted(
        sessions_dir.glob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for session_file in session_files:
        try:
            lines = session_file.read_text(encoding="utf-8").splitlines()
        except Exception:
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
            for candidate in _extract_candidate_entries(payload):
                normalized = candidate["content"].strip()
                if not normalized:
                    continue
                score = _score_candidate(candidate, query_tokens)
                if score <= 0:
                    continue
                hits.append(
                    RelevantMemoryHit(
                        score=score,
                        recorded_at=recorded_at,
                        text=_render_candidate_for_prompt(candidate),
                        source_file=session_file,
                    )
                )

        if scanned >= max_records:
            break

    return hits


def _select_relevant_hits(
    hits: list[RelevantMemoryHit],
    *,
    limit: int,
) -> tuple[RelevantMemoryHit, ...]:
    ranked = sorted(
        hits,
        key=lambda item: (item.score, item.recorded_at),
        reverse=True,
    )
    selected: list[RelevantMemoryHit] = []
    seen_texts: set[str] = set()
    for hit in ranked:
        if hit.text in seen_texts:
            continue
        selected.append(hit)
        seen_texts.add(hit.text)
        if len(selected) >= max(limit, 0):
            break
    return tuple(selected)


def _hits_to_context(
    hits: tuple[RelevantMemoryHit, ...],
) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    texts = tuple(hit.text for hit in hits)
    source_files: list[Path] = []
    for hit in hits:
        if hit.source_file not in source_files:
            source_files.append(hit.source_file)
    return texts, tuple(source_files)


def _render_candidate_for_prompt(candidate: dict) -> str:
    text = candidate["content"].strip()
    promotion = str(candidate.get("promotion") or "").lower()
    if promotion == "durable_memory":
        return text

    if _looks_like_manual_handoff(text):
        return (
            f"{REFERENCE_NOTE_PREFIX} "
            "A previous similar task ended as a multi-step manual handoff "
            "(browser/devtools/console/copy-paste/save-script flow) instead of direct execution."
        )

    return f"{REFERENCE_NOTE_PREFIX} Prior similar task note: {_preview_text(text)}"


def _looks_like_manual_handoff(text: str) -> bool:
    normalized = _collapse_text(text).lower()
    hits = sum(1 for hint in MANUAL_HANDOFF_HINTS if hint in normalized)
    return hits >= 2


def _preview_text(text: str, max_chars: int = 220) -> str:
    collapsed = _collapse_text(text)
    if len(collapsed) <= max_chars:
        return collapsed
    return f"{collapsed[: max_chars - 3].rstrip()}..."


def _collapse_text(text: str) -> str:
    collapsed = re.sub(r"```.*?```", " ", text or "", flags=re.DOTALL)
    return " ".join(collapsed.split()).strip()
