#!/usr/bin/env python3
"""Replay compiler for aworld.replay.subprocess.v1.

Reads a capability request, iterates http_resource requirements, selects
one evidence-derivation source per requirement, copies fixture bytes
byte-for-byte to the output directory as declared evidence fixtures,
and writes result.json.

The compiler writes ONLY:
  - output/result.json
  - output/fixtures/<name> (declared in result.fixtures)

It does NOT write response_index.json; the framework derives the
recorded-response sidecar after compile.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


_MAX_RESPONSE_CONTAINS = 4096


def _load_request(request_path: str) -> Dict[str, Any]:
    with open(request_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _select_source(
    evidence_ref: str, request: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    derivations = request.get("evidence_derivations", {}).get(evidence_ref, [])
    if not derivations:
        return None
    return derivations[0]


def _find_non_empty(node: Any) -> Any:
    """Depth-first search for the first non-empty scalar or container value."""
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


def _fixture_bytes_text(fixture_path: Path) -> str:
    """Return the raw fixture text used when no structured scalar is found."""
    raw = fixture_path.read_bytes()
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def _coerce_to_string(value: Any, fixture_path: Path) -> str:
    """Coerce a fixture-derived value into a non-empty string."""
    if value is None:
        return _fixture_bytes_text(fixture_path)
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except Exception:
            text = value.decode("utf-8", errors="replace")
    elif isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    if text == "":
        text = _fixture_bytes_text(fixture_path)
    return text


def _extract_response_contains(fixture_path: Path) -> str:
    """Extract a non-empty fixture-derived scalar substring.

    Selects the first non-empty value from the recorded fixture, coerces it
    to a string, and bounds it to at most _MAX_RESPONSE_CONTAINS characters.
    The runtime still returns the complete recorded response container; this
    value is only the protocol probe assertion.
    """
    raw = fixture_path.read_bytes()
    data: Any = None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = None

    selected: Any = None
    if data is not None:
        selected = _find_non_empty(data)
    if selected is None:
        selected = _fixture_bytes_text(fixture_path)

    text = _coerce_to_string(selected, fixture_path)
    if text == "":
        text = _fixture_bytes_text(fixture_path)
    if len(text) > _MAX_RESPONSE_CONTAINS:
        text = text[:_MAX_RESPONSE_CONTAINS]
    return text


def _build_result(request: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    requirements: List[Dict[str, Any]] = request.get("requirements", [])
    capability_id = "web-content-grounding-http-replay"

    handled: List[str] = []
    unhandled: List[str] = []
    evidence_refs_map: Dict[str, List[str]] = {}
    fixture_evidence_refs: Dict[str, List[str]] = {}
    fixtures: List[str] = []
    endpoint_replacements: Dict[str, str] = {}
    services: List[Dict[str, Any]] = []

    for req in requirements:
        req_id = req.get("requirement_id", "")
        if req.get("kind") != "http_resource":
            unhandled.append(req_id)
            continue

        refs = req.get("evidence_refs", [])
        selected_source: Optional[Dict[str, Any]] = None
        selected_ref: Optional[str] = None
        for ref in refs:
            src = _select_source(ref, request)
            if src is not None:
                selected_source = src
                selected_ref = ref
                break

        if selected_source is None or selected_ref is None:
            unhandled.append(req_id)
            continue

        src_path = Path(selected_source["path"])
        fixture_name = f"fixture_{len(fixtures)}.json"
        fixture_rel = f"fixtures/{fixture_name}"
        fixture_out = output_dir / "fixtures" / fixture_name
        fixture_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, fixture_out)

        response_contains = _extract_response_contains(fixture_out)
        if not response_contains:
            response_contains = _fixture_bytes_text(fixture_out)[:_MAX_RESPONSE_CONTAINS]

        handled.append(req_id)
        evidence_refs_map[req_id] = [selected_ref]
        fixture_evidence_refs[fixture_rel] = [selected_ref]
        fixtures.append(fixture_rel)

        service_id = f"service_{len(services)}"
        endpoint_replacements[req_id] = service_id

        services.append(
            {
                "service_id": service_id,
                "requirement_id": req_id,
                "transport": "skill_runtime",
                "response_fixture": fixture_rel,
                "runtime_entrypoint": "replay/runtime.py",
                "readiness": {
                    "kind": "http",
                    "path": "/healthz",
                    "timeout_seconds": 5,
                },
                "protocol_probes": [
                    {
                        "kind": "http",
                        "path": "/",
                        "timeout_seconds": 10,
                        "response_contains": response_contains,
                    }
                ],
            }
        )

    result = {
        "schema_version": "aworld.replay.capability_result.v1",
        "capability_id": capability_id,
        "deterministic": True,
        "handled_requirements": handled,
        "unhandled_requirements": unhandled,
        "evidence_refs": evidence_refs_map,
        "fixture_evidence_refs": fixture_evidence_refs,
        "fixtures": fixtures,
        "endpoint_replacements": endpoint_replacements,
        "services": services,
    }
    return result


def main() -> int:
    if "--request" not in sys.argv or "--output" not in sys.argv:
        print(
            "Usage: compiler.py --request <request-json> --output <output-dir>",
            file=sys.stderr,
        )
        return 2

    req_idx = sys.argv.index("--request")
    out_idx = sys.argv.index("--output")
    request_path = sys.argv[req_idx + 1]
    output_dir = Path(sys.argv[out_idx + 1])
    output_dir.mkdir(parents=True, exist_ok=True)

    request = _load_request(request_path)
    result = _build_result(request, output_dir)

    result_path = output_dir / "result.json"
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())