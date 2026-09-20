#!/usr/bin/env python3
import argparse
import json
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

SCHEMA = "aworld.replay.capability_result.v1"

GATEWAY_KEYS = (
    "action_result",
    "tool_outputs",
    "content",
    "response",
    "result",
    "output",
    "body",
    "data",
)


def _safe_rel(path_str):
    p = Path(path_str)
    parts = [part for part in p.parts if part not in ("", "/")]
    rel = Path(*parts) if parts else Path(path_str).name
    return str(rel)


def _load_fixture(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return raw.decode("utf-8", errors="replace")


def _project_value(data, max_depth=32):
    if max_depth <= 0:
        return None
    if isinstance(data, dict):
        for key in GATEWAY_KEYS:
            if key in data and data[key] is not None:
                inner = _project_value(data[key], max_depth - 1)
                if inner is not None:
                    return inner
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) and v not in ("", 0, False):
                return v
        return None
    if isinstance(data, list):
        for item in data[:8]:
            inner = _project_value(item, max_depth - 1)
            if inner is not None:
                return inner
        return None
    if isinstance(data, (str, int, float, bool)) and data not in ("", 0, False):
        return data
    if data is None:
        return None
    return str(data)[:8192]


def _project_container(data, max_depth=32):
    if max_depth <= 0:
        return data
    if isinstance(data, dict):
        for key in GATEWAY_KEYS:
            if key in data and data[key] is not None:
                return _project_container(data[key], max_depth - 1)
        return data
    if isinstance(data, list):
        for item in data[:8]:
            inner = _project_container(item, max_depth - 1)
            if isinstance(inner, dict):
                return inner
        return data
    return data


def _derive_response_contains(fixture_path):
    data = _load_fixture(fixture_path)
    val = _project_value(data)
    if val is None:
        val = str(data)[:4096]
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    return str(val)[:8192]


def _derive_task_entry_path(identifier):
    """Return a canonical compiler probe path for a requirement URL.

    The framework's typed HTTP binder safely restores a non-root trailing-slash
    path after parsing the generic root probe. Emitting that path directly here
    would fail the compile-result canonical-path gate before typed binding.
    """

    if not isinstance(identifier, str) or not identifier.strip():
        return "/"
    parsed = urlsplit(identifier)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "/"
    path = parsed.path or "/"
    decoded_path = unquote(path)
    if "\\" in decoded_path or any(
        part == ".." for part in PurePosixPath(decoded_path).parts
    ):
        # Preserve unsafe spellings so the framework's canonical-path gate
        # rejects them; never hide traversal behind the generic root binder.
        return path
    if path != "/" and path.endswith("/"):
        return "/"
    return path


def _derive_readiness_path(task_entry_path):
    return "/_readiness" if task_entry_path == "/healthz" else "/healthz"


def _select_source(evidence_refs, derivations):
    """Prefer the evidence source with the richest recorded-response index."""

    sources = [
        source
        for evidence_ref in evidence_refs
        for source in derivations.get(evidence_ref, [])
        if isinstance(source, dict)
        and isinstance(source.get("path"), str)
        and bool(source["path"])
    ]
    if not sources:
        return None

    recorded_sources = [
        source
        for source in sources
        if isinstance(source.get("response_index_path"), str)
        and bool(source["response_index_path"].strip())
        and isinstance(source.get("response_record_count"), int)
        and not isinstance(source.get("response_record_count"), bool)
        and source["response_record_count"] > 0
    ]
    if recorded_sources:
        return max(
            recorded_sources,
            key=lambda source: (
                source["response_record_count"],
                source.get("byte_length", 0)
                if isinstance(source.get("byte_length"), int)
                and not isinstance(source.get("byte_length"), bool)
                else 0,
            ),
        )
    return sources[0]


def compile_request(request, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = output_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    derivations = request.get("evidence_derivations", {}) or {}
    handled = []
    unhandled = []
    evidence_refs_map = {}
    fixture_evidence_refs = {}
    fixtures = []
    endpoint_replacements = {}
    services = []

    for req in request.get("requirements", []):
        rid = req.get("requirement_id")
        refs = req.get("evidence_refs", [])
        identifier = req.get("identifier", "")
        status = req.get("status", "")

        src = _select_source(refs, derivations)
        if src is None:
            unhandled.append(rid)
            continue
        src_path = src.get("path")
        fixture_name = "fixture_{}.bin".format(len(fixtures))
        fixture_rel = "fixtures/{}".format(fixture_name)
        fixture_abs = fixtures_dir / fixture_name

        with open(src_path, "rb") as f_in:
            data = f_in.read()
        with open(fixture_abs, "wb") as f_out:
            f_out.write(data)

        fixtures.append(fixture_rel)
        fixture_evidence_refs[fixture_rel] = list(refs)
        handled.append(rid)
        evidence_refs_map[rid] = list(refs)

        service_id = "service_{}".format(len(services))
        endpoint_replacements[identifier] = service_id

        if status == "runtime_required":
            transport = "skill_runtime"
            runtime_entrypoint = "replay/runtime.py"
        else:
            transport = "http_fixture"
            runtime_entrypoint = None

        response_contains = _derive_response_contains(str(fixture_abs))
        task_entry_path = _derive_task_entry_path(identifier)
        readiness_path = _derive_readiness_path(task_entry_path)

        service = {
            "service_id": service_id,
            "requirement_id": rid,
            "transport": transport,
            "response_fixture": fixture_rel,
        }
        if runtime_entrypoint:
            service["runtime_entrypoint"] = runtime_entrypoint
            service["task_entry_path"] = task_entry_path
        service["protocol_probes"] = [
            {
                "kind": "http",
                "path": task_entry_path,
                "timeout_seconds": 5,
                "response_contains": response_contains,
            }
        ]
        service["readiness"] = {
            "kind": "http",
            "path": readiness_path,
            "timeout_seconds": 5,
        }
        services.append(service)

    return {
        "schema_version": SCHEMA,
        "capability_id": "agent-browser-replay",
        "deterministic": True,
        "handled_requirements": handled,
        "unhandled_requirements": unhandled,
        "evidence_refs": evidence_refs_map,
        "fixture_evidence_refs": fixture_evidence_refs,
        "fixtures": fixtures,
        "endpoint_replacements": endpoint_replacements,
        "services": services,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.request, "r", encoding="utf-8") as f:
        request = json.load(f)

    result = compile_request(request, args.output)

    with open(Path(args.output) / "result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
