"""Read-only cross-session tools over an explicitly scoped session store."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Collection

from .function import Tool

_SCOPE_KEY = object()


class _SessionReader:
    def __init__(self, store, allowed_session_ids=None):
        self.store = store
        self.allowed = None if allowed_session_ids is None else frozenset(allowed_session_ids)

    async def sessions(self):
        return tuple(session for session in await self.store.list_sessions()
                     if self.allowed is None or session.id in self.allowed)

    async def read(self, session_id):
        for session in await self.sessions():
            if session.id == session_id:
                return session
        raise LookupError(f"Session unavailable in this tool scope: {session_id}")


def _bind_session_tools(context, store):
    context._resource(_SCOPE_KEY, lambda: _SessionReader(store))


def _missing_scope():
    raise RuntimeError("Session tools need a bound session store")


def _positive(arguments, name, default, maximum=None):
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return min(value, maximum) if maximum else value


def session_tools(store=None, *, allowed_session_ids: Collection[str] | None = None) -> tuple[Tool, ...]:
    """Default: read the invoking session's host store, never a global directory.

    An explicit store/allowlist can narrow or replace that scope. An allowlist
    requires an explicit store. Reading never submits input or executes agents.
    """
    if store is None and allowed_session_ids is not None:
        raise ValueError("An allowlist requires an explicit session store")
    explicit = None if store is None else _SessionReader(store, allowed_session_ids)

    def reader(context):
        return explicit if explicit is not None else context.resource(_SCOPE_KEY, _missing_scope)

    async def read(arguments, context):
        session_id = arguments.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be non-empty text")
        offset, limit = _positive(arguments, "offset", 1), _positive(arguments, "limit", 50, 200)
        session = await reader(context).read(session_id)
        snapshot, history = await session.snapshot(), await session.history()
        page = history[offset - 1:offset - 1 + limit]
        more = offset - 1 + len(page) < len(history)
        return {"session_id": session.id, "metadata": deepcopy(dict(snapshot.metadata)),
                "active_run_id": snapshot.active_run_id, "total_entries": len(history),
                "entries": [{"run_id": entry.run_id, "kind": entry.kind, "data": _visible_data(entry)} for entry in page],
                "next_offset": offset + len(page) if more else None}

    async def search(arguments, context):
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty text")
        return await query_sessions({**arguments, "query": {"text": query}}, context)

    async def query_sessions(arguments, context):
        query = arguments.get("query")
        if not isinstance(query, dict):
            raise ValueError("query must be an object of session filters")
        if set(query) - {"session_ids", "metadata", "state", "text"}:
            raise ValueError("Unknown session query filter")
        ids, metadata_filter, state = query.get("session_ids"), query.get("metadata", {}), query.get("state")
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids)):
            raise ValueError("session_ids must be an array of non-empty IDs")
        if not isinstance(metadata_filter, dict):
            raise ValueError("metadata must be an object of exact-match filters")
        if state not in (None, "active", "idle"):
            raise ValueError("state must be active or idle")
        text_query = query.get("text", "")
        if not isinstance(text_query, str):
            raise ValueError("text must be a string")
        offset, limit = _positive(arguments, "offset", 1), _positive(arguments, "limit", 20, 100)
        term, matches = text_query.casefold(), []
        for session in await reader(context).sessions():
            if ids is not None and session.id not in ids:
                continue
            snapshot, history = await session.snapshot(), await session.history()
            metadata = deepcopy(dict(snapshot.metadata))
            if any(key not in metadata or metadata[key] != value for key, value in metadata_filter.items()):
                continue
            if state is not None and (snapshot.active_run_id is not None) != (state == "active"):
                continue
            texts = [(index + 1, entry.kind, _search_text(_visible_data(entry))) for index, entry in enumerate(history)]
            found = next(((index, kind, text) for index, kind, text in texts if term and term in text.casefold()), None)
            if term and found is None and term not in _search_text(metadata).casefold() and term not in session.id.casefold():
                continue
            snippet = ""
            if found:
                text = found[2]
                position = text.casefold().find(term)
                snippet = text[max(0, position - 100):position + 400]
            matches.append({"session_id": session.id, "metadata": metadata,
                            "active_run_id": snapshot.active_run_id,
                            "matched_offset": found[0] if found else None,
                            "matched_kind": found[1] if found else ("metadata" if term else None), "snippet": snippet})
        page = matches[offset - 1:offset - 1 + limit]
        more = offset - 1 + len(page) < len(matches)
        return {"matches": page, "total_matches": len(matches),
                "next_offset": offset + len(page) if more else None}

    pagination = {"offset": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1}}
    return (
        Tool("read_session", "Read confirmed history and metadata by session_id in this host store. offset starts at 1; up to 200 entries. Never executes the session.",
             {"type": "object", "properties": {"session_id": {"type": "string"}, **pagination}, "required": ["session_id"]}, read),
        Tool("search_sessions", "Case-insensitive text search of history, metadata and IDs in this host store. Up to 100 matches; use read_session for details.",
             {"type": "object", "properties": {"query": {"type": "string"}, **pagination}, "required": ["query"]}, search),
        Tool("session_query", "Query visible sessions by combined filters. Empty query lists visible sessions. Returns metadata, IDs and match snippets; read_session retrieves history.",
             {"type": "object", "properties": {"query": {"type": "object", "properties": {
                 "session_ids": {"type": "array", "items": {"type": "string"}},
                 "metadata": {"type": "object"}, "state": {"type": "string", "enum": ["active", "idle"]},
                 "text": {"type": "string"}}, "additionalProperties": False}, **pagination}, "required": ["query"]}, query_sessions),
    )


def _visible_data(entry):
    if entry.kind == "assistant" and isinstance(entry.data, dict):
        return {key: value for key, value in entry.data.items()
                if key not in ("reasoning_content", "reasoning_identity")}
    return entry.data


def _search_text(data):
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, default=str)
