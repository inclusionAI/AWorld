#!/usr/bin/env python3
"""skill_runtime replay server for aworld.replay.subprocess.v1.

Binds 127.0.0.1 on the supplied port, serves fixture-derived HTTP responses
from a response index sidecar (AWORLD_REPLAY_RESPONSE_INDEX or
--response-index) whose records reference fixture files, and writes a bounded
protocol trace to the scratch directory.

Every trace record is a single JSON object with exactly the canonical fields:
direction, sequence, kind, fields, correlation.
"""
import json
import os
import sys
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


_RESPONSE_INDEX_PATH: Optional[str] = None
_OUTPUT_DIR: Optional[str] = None
_SCRATCH_DIR: Optional[str] = None
_TRACE_SEQ = 0
_RESPONSE_INDEX_CACHE: Dict[str, Any] = {}


def _next_seq() -> int:
    global _TRACE_SEQ
    _TRACE_SEQ += 1
    return _TRACE_SEQ


def _resolve_search_dirs() -> List[Path]:
    """Return candidate directories where fixture files may reside."""
    dirs: List[Path] = []
    if _RESPONSE_INDEX_PATH:
        idx = Path(_RESPONSE_INDEX_PATH).resolve()
        dirs.append(idx.parent)
        dirs.append(idx.parent / "fixtures")
        dirs.append(idx.parent.parent)
        dirs.append(idx.parent.parent / "fixtures")
    if _OUTPUT_DIR:
        d = Path(_OUTPUT_DIR)
        dirs.append(d)
        dirs.append(d / "fixtures")
        dirs.append(d.parent)
        dirs.append(d.parent / "fixtures")
    if _SCRATCH_DIR:
        d = Path(_SCRATCH_DIR)
        dirs.append(d)
        dirs.append(d / "fixtures")
        dirs.append(d.parent)
        dirs.append(d.parent / "fixtures")
    env_extra = os.environ.get("AWORLD_REPLAY_FIXTURE_DIR", "")
    if env_extra:
        dirs.append(Path(env_extra))
        dirs.append(Path(env_extra) / "fixtures")
    seen = set()
    unique: List[Path] = []
    for d in dirs:
        try:
            rd = d.resolve()
        except Exception:
            rd = d
        if rd not in seen:
            seen.add(rd)
            unique.append(rd)
    return unique


def _load_response_index() -> Optional[Any]:
    """Open the response index sidecar file and return the parsed object."""
    idx_path = _RESPONSE_INDEX_PATH
    if not idx_path:
        return None
    p = Path(idx_path)
    if not p.exists():
        return None
    cache_key = str(p)
    if cache_key in _RESPONSE_INDEX_CACHE:
        return _RESPONSE_INDEX_CACHE[cache_key]
    try:
        raw = p.read_bytes()
        result = json.loads(raw.decode("utf-8"))
        _RESPONSE_INDEX_CACHE[cache_key] = result
        return result
    except Exception:
        return None


def _fixture_rel_from_record(record: Dict[str, Any]) -> str:
    """Extract the fixture relative path from a record using known key names."""
    for key in (
        "fixture_path",
        "fixture",
        "fixture_file",
        "path",
        "response_fixture",
        "file",
    ):
        val = record.get(key, "")
        if isinstance(val, str) and val:
            return val
    return ""


def _load_fixture_bytes(fixture_rel: str) -> Optional[bytes]:
    """Load raw fixture bytes by searching candidate directories."""
    p = Path(fixture_rel)
    if p.is_absolute():
        if p.exists():
            try:
                return p.read_bytes()
            except Exception:
                return None
        return None
    for base in _resolve_search_dirs():
        candidate = base / fixture_rel
        if candidate.exists():
            try:
                return candidate.read_bytes()
            except Exception:
                continue
    return None


def _load_fixture_bytes_any(records: List[Dict[str, Any]]) -> Optional[bytes]:
    """Find the first record whose referenced fixture yields bytes on disk."""
    for record in records:
        fixture_rel = _fixture_rel_from_record(record)
        if not fixture_rel:
            continue
        raw = _load_fixture_bytes(fixture_rel)
        if raw is not None:
            return raw
    return None


def _parse_fixture(raw: bytes) -> Any:
    """Parse fixture bytes as JSON, falling back to decoded text."""
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None


def _find_non_empty(node: Any) -> Any:
    if isinstance(node, dict):
        for gateway in ("action_result", "tool_outputs"):
            if gateway in node and node[gateway]:
                found = _find_non_empty(node[gateway])
                if found is not None:
                    return found
        for gateway in (
            "content",
            "response",
            "result",
            "output",
            "body",
            "data",
            "text",
            "value",
        ):
            if gateway in node:
                val = node[gateway]
                if val is not None and val != "" and val != [] and val != {}:
                    found = _find_non_empty(val)
                    if found is not None:
                        return found
        for v in node.values():
            found = _find_non_empty(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            if item is not None and item != "" and item != [] and item != {}:
                found = _find_non_empty(item)
                if found is not None:
                    return found
    elif node is not None and node != "":
        return node
    return None


def _recorded_responses() -> List[Dict[str, Any]]:
    """Return the list of recorded-response records from the sidecar."""
    records: List[Dict[str, Any]] = []
    idx = _load_response_index()
    if idx is None:
        return records
    raw_records: Any = None
    if isinstance(idx, dict):
        raw_records = idx.get("records", [])
    elif isinstance(idx, list):
        raw_records = idx
    if isinstance(raw_records, list):
        for record in raw_records:
            if isinstance(record, dict):
                records.append(record)
            elif isinstance(record, str) and record:
                records.append({"path": record})
    return records


def _project_response_value() -> Any:
    """Project the fixture-derived data-plane value from the index sidecar."""
    for record in _recorded_responses():
        fixture_rel = _fixture_rel_from_record(record)
        if not fixture_rel:
            continue
        raw = _load_fixture_bytes(fixture_rel)
        if raw is None:
            continue
        data = _parse_fixture(raw)
        if data is None:
            continue
        found = _find_non_empty(data)
        if found is not None:
            return found
        return data
    for record in _recorded_responses():
        if "value" in record:
            val = record["value"]
            if val is not None and val != "" and val != [] and val != {}:
                projected = _find_non_empty(val)
                if projected is not None:
                    return projected
                return val
        if "content" in record:
            val = record["content"]
            if val is not None and val != "" and val != [] and val != {}:
                projected = _find_non_empty(val)
                if projected is not None:
                    return projected
                return val
    return None


def _has_fixture_data() -> bool:
    """Return True when at least one referenced fixture yields parseable bytes."""
    records = _recorded_responses()
    if records:
        if _load_fixture_bytes_any(records) is not None:
            return True
        for record in records:
            if "value" in record:
                val = record["value"]
                if val is not None and val != "" and val != [] and val != {}:
                    return True
            if "content" in record:
                val = record["content"]
                if val is not None and val != "" and val != [] and val != {}:
                    return True
    if _OUTPUT_DIR:
        fixtures_dir = Path(_OUTPUT_DIR) / "fixtures"
        if fixtures_dir.exists():
            for child in fixtures_dir.iterdir():
                if child.is_file():
                    try:
                        if child.read_bytes():
                            return True
                    except Exception:
                        continue
    if _SCRATCH_DIR:
        fixtures_dir = Path(_SCRATCH_DIR) / "fixtures"
        if fixtures_dir.exists():
            for child in fixtures_dir.iterdir():
                if child.is_file():
                    try:
                        if child.read_bytes():
                            return True
                    except Exception:
                        continue
    return False


def _sanitize_correlation(correlation: Dict[str, Any]) -> Dict[str, Any]:
    """Return correlation with sensitive fields stripped."""
    return {
        k: v
        for k, v in correlation.items()
        if k not in ("body", "payload", "credentials")
    }


def _write_trace(
    direction: str,
    kind: str,
    fields: List[str],
    correlation: Dict[str, Any],
) -> None:
    """Write a single canonical trace record."""
    global _SCRATCH_DIR
    if not _SCRATCH_DIR:
        try:
            tmp = tempfile.mkdtemp(prefix="replay_probe_")
            _SCRATCH_DIR = tmp
        except Exception:
            return
    trace_path = Path(_SCRATCH_DIR) / "protocol_trace.jsonl"
    entry: Dict[str, Any] = {
        "direction": direction,
        "sequence": _next_seq(),
        "kind": kind,
        "fields": list(fields),
        "correlation": _sanitize_correlation(correlation),
    }
    try:
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


class ReplayHandler(BaseHTTPRequestHandler):
    def _send_json(
        self, code: int, body: Any, correlation: Dict[str, Any]
    ) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        response_fields = list(body.keys()) if isinstance(body, dict) else []
        _write_trace("emitted", "http_response", response_fields, correlation)

    def _serve_data(self, correlation: Dict[str, Any]) -> None:
        value = _project_response_value()
        if value is None:
            self._send_json(404, {"error": "fixture_not_found"}, correlation)
            return
        response_body = {
            "ok": True,
            "result": value,
            "correlation": {
                "request_id": correlation.get("request_id", ""),
                "routing": correlation.get("routing", ""),
            },
        }
        self._send_json(200, response_body, correlation)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        correlation = {
            "path": self.path,
            "method": "GET",
            "request_id": self.headers.get("X-Request-ID", ""),
            "routing": self.headers.get("X-Routing-Key", ""),
        }
        _write_trace("received", "http_request", ["method", "path"], correlation)

        if parsed.path == "/healthz":
            self._send_json(200, {"status": "ready"}, correlation)
            return

        self._serve_data(correlation)

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(content_len) if content_len > 0 else b"{}"
        correlation = {
            "path": self.path,
            "method": "POST",
            "request_id": self.headers.get("X-Request-ID", ""),
            "routing": self.headers.get("X-Routing-Key", ""),
        }
        _write_trace("received", "http_request", ["method", "path"], correlation)
        self._serve_data(correlation)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _self_test_once() -> int:
    """Validate sidecar and fixture wiring without binding a socket."""
    idx = _load_response_index()
    if idx is None:
        _write_trace(
            "emitted",
            "error",
            ["error"],
            {"error": "response_index_not_found"},
        )
        print("response index sidecar not found", file=sys.stderr)
        return 1
    if not _has_fixture_data():
        _write_trace(
            "emitted",
            "error",
            ["error"],
            {"error": "no_referenced_fixture_data"},
        )
        print("no referenced fixture data available", file=sys.stderr)
        return 1
    value = _project_response_value()
    if value is None:
        _write_trace(
            "emitted",
            "error",
            ["error"],
            {"error": "fixture_value_empty"},
        )
        print("fixture-derived response value is empty", file=sys.stderr)
        return 1
    if isinstance(value, dict):
        trace_fields = list(value.keys())
    elif isinstance(value, list):
        trace_fields = ["list"]
    else:
        trace_fields = ["value"]
    _write_trace(
        "emitted",
        "http_response",
        trace_fields,
        {"probe": "once", "status": "ok"},
    )
    return 0


def main() -> int:
    global _RESPONSE_INDEX_PATH, _OUTPUT_DIR, _SCRATCH_DIR

    port = 0
    output_dir = ""
    scratch = ""
    once = False

    env_index = os.environ.get("AWORLD_REPLAY_RESPONSE_INDEX", "")
    if env_index:
        _RESPONSE_INDEX_PATH = env_index

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] == "--output-dir" and i + 1 < len(args):
            output_dir = args[i + 1]
            i += 2
        elif args[i] == "--response-index" and i + 1 < len(args):
            _RESPONSE_INDEX_PATH = args[i + 1]
            i += 2
        elif args[i] == "--scratch" and i + 1 < len(args):
            scratch = args[i + 1]
            i += 2
        elif args[i] == "--once":
            once = True
            i += 1
        else:
            i += 1

    _OUTPUT_DIR = output_dir
    _SCRATCH_DIR = scratch

    if once:
        return _self_test_once()

    if scratch:
        Path(scratch).mkdir(parents=True, exist_ok=True)

    if not _has_fixture_data():
        if scratch:
            trace_path = Path(scratch) / "protocol_trace.jsonl"
            try:
                terminal_entry = {
                    "direction": "emitted",
                    "sequence": _next_seq(),
                    "kind": "error",
                    "fields": ["error"],
                    "correlation": {"error": "missing_fixture_data"},
                }
                with open(trace_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(terminal_entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
        print("missing fixture data for replay", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", port), ReplayHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        if scratch:
            trace_path = Path(scratch) / "protocol_trace.jsonl"
            try:
                terminal_entry = {
                    "direction": "emitted",
                    "sequence": _next_seq(),
                    "kind": "error",
                    "fields": ["error"],
                    "correlation": {"error": "runtime_exception"},
                }
                with open(trace_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(terminal_entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())