from __future__ import annotations

import json
import os
import re
from pathlib import Path

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,}")
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

    ranked: list[tuple[int, str, str, Path]] = []
    seen_texts: set[str] = set()
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
                if not normalized or normalized in seen_texts:
                    continue
                score = _score_candidate(candidate, query_tokens)
                if score <= 0:
                    continue
                ranked.append((score, recorded_at, normalized, session_file))
                seen_texts.add(normalized)

        if scanned >= max_records:
            break

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = ranked[: max(limit, 0)]

    texts = tuple(item[2] for item in selected)
    source_files: list[Path] = []
    for _, _, _, source_file in selected:
        if source_file not in source_files:
            source_files.append(source_file)
    return texts, tuple(source_files)


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
    return (
        base_score
        + CONFIDENCE_BONUS.get(confidence, 0)
        + PROMOTION_BONUS.get(promotion, 0)
    )
