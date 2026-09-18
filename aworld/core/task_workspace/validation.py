"""Executed, hash-bound checks for task artifacts; no reward inference.

The caller supplies candidate/input mappings from its own task scope. Returned
receipts are plain data: a trusted session/store must register them itself,
never accept a model-supplied receipt as proof of execution.
"""

from __future__ import annotations

import ast
import asyncio
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Mapping, Sequence

from ._process import ProcessLimits, isolated_env, run_bounded

SCHEMA = "aworld.validation/v1"
KINDS = {
    "exists",
    "regular_file",
    "nonempty",
    "file_size",
    "sha256",
    "text",
    "json",
    "csv",
    "table",
    "numeric",
    "compare",
    "preserve",
    "source_syntax",
    "command",
}
CHECK_SCHEMA = {
    "type": "object",
    "required": ["id", "kind"],
    "properties": {
        "id": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,128}$"},
        "kind": {"enum": sorted(KINDS)},
        "path": {"type": "string"},
        "source": {
            "type": "object",
            "description": "Public source quote/span or explicit caller provenance; included in definition hash.",
        },
    },
}


def describe_validation() -> dict:
    """Compact schema/help payload for the native workbench inspect operation."""
    return {
        "schema_version": "aworld.check-definitions/v1",
        "check_schema": CHECK_SCHEMA,
        "path_semantics": "path is an exact logical key in the caller-bound candidate mapping; non-command checks require it",
        "kinds": {
            "exists": {},
            "regular_file": {},
            "nonempty": {},
            "file_size": {
                "options": ["min_bytes", "max_bytes"],
                "metrics": ["size_bytes"],
            },
            "sha256": {
                "required": ["expected"],
                "expected": "64 lowercase hex characters",
            },
            "text": {"options": ["encoding"]},
            "json": {
                "options": ["root_type", "required_keys"],
                "root_type": ["object", "array", "string", "number", "boolean", "null"],
            },
            "csv": {
                "options": [
                    "delimiter",
                    "encoding",
                    "required_columns",
                    "exact_columns",
                    "min_rows",
                    "max_rows",
                    "rows_equal",
                    "primary_key",
                    "same_rows_as",
                    "input_format",
                ],
                "metrics": ["row_count", "column_count", "duplicate_key_count"],
            },
            "table": {
                "options": [
                    "format",
                    "records_pointer",
                    "required_columns",
                    "exact_columns",
                    "min_rows",
                    "max_rows",
                    "rows_equal",
                    "primary_key",
                    "same_rows_as",
                    "input_format",
                ]
            },
            "numeric": {
                "options": [
                    "format",
                    "records_pointer",
                    "columns",
                    "min_value",
                    "max_value",
                    "sum_equals",
                    "abs_tolerance",
                    "rel_tolerance",
                ],
                "notes": "Finite numeric cells; sum_equals applies independently to each row across selected columns.",
                "metrics": [
                    "finite_count",
                    "invalid_value_count",
                    "min_value",
                    "max_value",
                    "max_sum_error",
                ],
            },
            "compare": {
                "required": ["input", "columns"],
                "options": [
                    "format",
                    "input_format",
                    "records_pointer",
                    "input_records_pointer",
                    "primary_key",
                    "key_mode",
                    "allow_extra_rows",
                    "abs_tolerance",
                    "rel_tolerance",
                ],
                "metrics": [
                    "compared_count",
                    "max_abs_error",
                    "max_rel_error",
                    "mismatch_count",
                    "missing_keys",
                    "extra_keys",
                ],
            },
            "preserve": {
                "required": ["input", "columns"],
                "options": [
                    "format",
                    "input_format",
                    "primary_key",
                    "key_mode",
                    "allow_extra_rows",
                ],
                "notes": "Exact field equality against separately read input, keyed or positional.",
                "metrics": [
                    "matched_rows",
                    "missing_keys",
                    "extra_keys",
                    "changed_values",
                ],
            },
            "source_syntax": {
                "required": ["language"],
                "language": ["python", "json"],
                "notes": "Parses without executing source.",
            },
            "command": {
                "required": ["argv"],
                "options": [
                    "env",
                    "checker_files",
                    "timeout_seconds",
                    "negative_controls",
                ],
                "placeholders": ["{artifact:logical-key}", "{input:logical-key}"],
                "report": {
                    "schema_version": "aworld.check-report/v1",
                    "checks": [{"id": "assertion", "passed": True}],
                    "metrics": {"measured_metric": 0.0},
                },
                "negative_controls": [
                    {
                        "replacements": {
                            "output-key": "deliberately-wrong-input-fixture-key"
                        },
                        "expected_return_codes": [0, 1],
                    }
                ],
                "notes": "Exit 0 alone or missing/ineffective controls is unknown. Requires nonempty structured assertions and rejection of each negative fixture. Existing caller command contracts are separate.",
            },
        },
        "metrics": "Each finite executed metric is namespaced as <check.id>.<metric>; caller-provided success/metrics are rejected.",
        "receipt_schema": SCHEMA,
        "statuses": ["passed", "failed", "error", "unknown"],
    }


@dataclass(frozen=True)
class ValidationLimits:
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_rows: int = 100000
    max_columns: int = 4096
    max_cells: int = 2000000
    max_checks: int = 128
    process: ProcessLimits = ProcessLimits()

    def __post_init__(self):
        for key in (
            "max_file_bytes",
            "max_total_bytes",
            "max_rows",
            "max_columns",
            "max_cells",
            "max_checks",
        ):
            value = getattr(self, key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{key} must be a positive integer")


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def definition_hash(checks: Sequence[dict]) -> str:
    return hashlib.sha256(canonical(checks)).hexdigest()


def _read(path: Path, limit: int) -> tuple[dict, bytes | None]:
    try:
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                return {"state": "not_regular", "size": before.st_size}, None
            if Path(path).is_symlink():
                return {"state": "symlink"}, None
            if before.st_size > limit:
                return {"state": "too_large", "size": before.st_size}, None
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(data) > limit or (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                return {"state": "changed_during_read"}, None
            return {
                "state": "regular",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }, data
    except FileNotFoundError:
        return {"state": "missing"}, None
    except OSError as error:
        return {"state": "unreadable", "error_type": type(error).__name__}, None


def snapshot_bindings(
    files: Mapping[str, Path], limits: ValidationLimits | None = None
) -> dict:
    """Public content fingerprint helper; unreadable inputs never look verified."""
    limits = limits or ValidationLimits()
    return {
        name: _read(Path(path), limits.max_file_bytes)[0]
        for name, path in files.items()
    }


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite JSON literal")

    return json.loads(
        data.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=constant
    )


def _pointer(value, pointer: str = ""):
    if not pointer:
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("records_pointer must be a JSON pointer")
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _columns(value, *, allow_empty=False) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(v, str) or not v for v in value)
        or len(set(value)) != len(value)
        or (not value and not allow_empty)
    ):
        raise ValueError("columns must be a list of distinct nonempty strings")
    return value


def _rows(
    data: bytes, spec: dict, path: str, limits: ValidationLimits
) -> tuple[list[str], list[dict]]:
    fmt = spec.get("format") or ("csv" if path.lower().endswith(".csv") else "json")
    rows = []
    if fmt == "csv":
        delimiter = spec.get("delimiter", ",")
        if not isinstance(delimiter, str) or len(delimiter) != 1:
            raise ValueError("CSV delimiter must be one character")
        reader = csv.reader(
            io.StringIO(data.decode(spec.get("encoding", "utf-8-sig")), newline=""),
            delimiter=delimiter,
            strict=True,
        )
        columns = next(reader, [])
        _columns(columns, allow_empty=True)
        if len(columns) > limits.max_columns:
            raise ValueError("table exceeds column operation bound")
        for values in reader:
            if not values:
                continue
            if len(values) != len(columns):
                raise ValueError("CSV record width differs from its header")
            rows.append(dict(zip(columns, values)))
            if (
                len(rows) > limits.max_rows
                or len(rows) * len(columns) > limits.max_cells
            ):
                raise ValueError("table exceeds row/cell operation bounds")
    elif fmt == "json":
        value = _pointer(_json(data), spec.get("records_pointer", ""))
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("JSON table must be records, values, or a matrix")
        if len(value) > limits.max_rows:
            raise ValueError("table exceeds row operation bound")
        columns, seen = [], set()
        for item in value:
            row = (
                item
                if isinstance(item, dict)
                else {str(i): v for i, v in enumerate(item)}
                if isinstance(item, list)
                else {"value": item}
            )
            rows.append(row)
            for column in row:
                if column not in seen:
                    columns.append(column)
                    seen.add(column)
            if len(columns) > limits.max_columns:
                raise ValueError("table exceeds column operation bound")
            if len(rows) * len(columns) > limits.max_cells:
                raise ValueError("table exceeds cell operation bound")
    else:
        raise ValueError("table format must be csv or json")
    if len(columns) > limits.max_columns:
        raise ValueError("table exceeds column operation bound")
    return columns, rows


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("not a numeric scalar")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite numeric value")
    return result


def _tolerance(spec: dict) -> tuple[float, float]:
    absolute, relative = (
        _number(spec.get("abs_tolerance", 0)),
        _number(spec.get("rel_tolerance", 0)),
    )
    if absolute < 0 or relative < 0:
        raise ValueError("numeric tolerances cannot be negative")
    return absolute, relative


def _index(
    rows: list[dict], keys: list[str], key_mode: str = "typed"
) -> tuple[dict, int]:
    if key_mode not in {"typed", "string"}:
        raise ValueError("key_mode must be typed or string")
    result, duplicates = {}, 0
    for row in rows:
        values = [row[key] for key in keys]
        key = canonical([str(v) for v in values] if key_mode == "string" else values)
        duplicates += key in result
        result[key] = row
    return result, duplicates


def _builtin(
    check: dict,
    data: bytes | None,
    binding: dict,
    inputs: dict,
    limits: ValidationLimits,
) -> tuple[bool, dict, dict]:
    kind = check["kind"]
    evidence, metrics = {}, {}
    if "size" in binding:
        metrics["size_bytes"] = binding["size"]
    if kind in {"exists", "regular_file"}:
        return binding["state"] == "regular", metrics, {"file_state": binding["state"]}
    if data is None:
        return False, metrics, {"file_state": binding["state"]}
    if kind in {"nonempty", "file_size"}:
        minimum, maximum = (
            check.get("min_bytes", 1 if kind == "nonempty" else 0),
            check.get("max_bytes", limits.max_file_bytes),
        )
        if (
            not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or minimum < 0
            or maximum < minimum
        ):
            raise ValueError("invalid file size bounds")
        return (
            minimum <= len(data) <= maximum,
            metrics,
            {"min_bytes": minimum, "max_bytes": maximum},
        )
    if kind == "sha256":
        expected = check.get("expected")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("expected sha256 must be 64 lowercase hex characters")
        return binding["sha256"] == expected, metrics, {}
    if kind == "text":
        text = data.decode(check.get("encoding", "utf-8"))
        return True, {**metrics, "character_count": len(text)}, {}
    if kind == "source_syntax":
        if check.get("language") == "python":
            ast.parse(
                data.decode(check.get("encoding", "utf-8")), filename=check["path"]
            )
        elif check.get("language") == "json":
            _json(data)
        else:
            raise ValueError("supported source syntax languages are python and json")
        return True, metrics, {"executed_source": False}
    if kind == "json":
        value = _json(data)
        types = {
            "object": dict,
            "array": list,
            "string": str,
            "number": (int, float),
            "boolean": bool,
            "null": type(None),
        }
        root_type = check.get("root_type")
        if root_type is not None and root_type not in types:
            raise ValueError("unknown JSON root_type")
        matches = (
            root_type is None
            or isinstance(value, types[root_type])
            and not (root_type == "number" and isinstance(value, bool))
        )
        required = _columns(check.get("required_keys", []), allow_empty=True)
        matches &= (
            not required
            or isinstance(value, dict)
            and all(k in value for k in required)
        )
        return bool(matches), metrics, {"actual_type": type(value).__name__}
    columns, rows = _rows(
        data,
        {**check, **({"format": "csv"} if kind == "csv" else {})},
        check["path"],
        limits,
    )
    metrics.update(row_count=len(rows), column_count=len(columns))
    if kind in {"csv", "table"}:
        required = _columns(check.get("required_columns", []), allow_empty=True)
        valid = all(column in columns for column in required) and all(
            all(column in row for column in required) for row in rows
        )
        if "exact_columns" in check:
            valid &= columns == _columns(check["exact_columns"], allow_empty=True)
        minimum, maximum = (
            check.get("min_rows", 0),
            check.get("max_rows", limits.max_rows),
        )
        if (
            not isinstance(minimum, int)
            or not isinstance(maximum, int)
            or minimum < 0
            or maximum < minimum
        ):
            raise ValueError("invalid row count bounds")
        valid &= minimum <= len(rows) <= maximum
        if "rows_equal" in check:
            if type(check["rows_equal"]) is not int or check["rows_equal"] < 0:
                raise ValueError("rows_equal must be a nonnegative integer")
            valid &= len(rows) == check["rows_equal"]
        if "same_rows_as" in check:
            _, reference = _rows(
                inputs[check["same_rows_as"]],
                {
                    **check,
                    "format": check.get("input_format", check.get("format", "csv")),
                },
                check["same_rows_as"],
                limits,
            )
            valid &= len(rows) == len(reference)
        if "primary_key" in check:
            _, duplicates = _index(
                rows, _columns(check["primary_key"]), check.get("key_mode", "typed")
            )
            metrics["duplicate_key_count"] = duplicates
            valid &= duplicates == 0
        return bool(valid), metrics, {"columns": columns}
    fields = _columns(check.get("columns", columns))
    if kind == "numeric":
        values, invalid, sums = [], 0, []
        for row in rows:
            current = []
            for field in fields:
                try:
                    current.append(_number(row[field]))
                except (ValueError, KeyError, OverflowError):
                    invalid += 1
            values.extend(current)
            if len(current) == len(fields):
                sums.append(math.fsum(current))
        metrics.update(finite_count=len(values), invalid_value_count=invalid)
        valid = invalid == 0 and bool(values)
        if values:
            metrics.update(min_value=min(values), max_value=max(values))
            if "min_value" in check:
                valid &= min(values) >= _number(check["min_value"])
            if "max_value" in check:
                valid &= max(values) <= _number(check["max_value"])
        if "sum_equals" in check:
            target = _number(check["sum_equals"])
            absolute, relative = _tolerance(check)
            errors = [abs(value - target) for value in sums]
            metrics["max_sum_error"] = max(errors, default=0)
            valid &= bool(sums) and all(
                error <= absolute + relative * abs(target) for error in errors
            )
        return bool(valid), metrics, {}
    if kind not in {"compare", "preserve"}:
        raise ValueError("unsupported built-in check")
    if "columns" not in check:
        raise ValueError("reference comparisons require explicit columns")
    name = check.get("input")
    if name not in inputs:
        raise ValueError("reference input is missing or unreadable")
    _, reference = _rows(
        inputs[name],
        {
            **check,
            "format": check.get("input_format", check.get("format")),
            "records_pointer": check.get("input_records_pointer", ""),
        },
        name,
        limits,
    )
    keys = check.get("primary_key")
    if keys is not None:
        keys = _columns(keys)
        actual, duplicates = _index(rows, keys, check.get("key_mode", "typed"))
        expected, reference_duplicates = _index(
            reference, keys, check.get("key_mode", "typed")
        )
    else:
        actual, expected = dict(enumerate(rows)), dict(enumerate(reference))
        duplicates = reference_duplicates = 0
    shared = actual.keys() & expected.keys()
    missing, extra = (
        len(expected.keys() - actual.keys()),
        len(actual.keys() - expected.keys()),
    )
    metrics.update(
        matched_rows=len(shared),
        missing_keys=missing,
        extra_keys=extra,
        duplicate_key_count=duplicates,
        reference_duplicate_key_count=reference_duplicates,
    )
    valid = not (missing or duplicates or reference_duplicates) and (
        not extra or check.get("allow_extra_rows") is True
    )
    absolute, relative = _tolerance(check)
    changes, comparisons, max_absolute, max_relative = 0, 0, 0.0, 0.0
    for key in shared:
        for field in fields:
            comparisons += 1
            if field not in actual[key] or field not in expected[key]:
                changes += 1
                continue
            left, right = actual[key][field], expected[key][field]
            if kind == "preserve":
                changes += canonical(left) != canonical(right)
            else:
                try:
                    left, right = _number(left), _number(right)
                    error = abs(left - right)
                    max_absolute = max(max_absolute, error)
                    if right:
                        max_relative = max(max_relative, error / abs(right))
                    changes += error > absolute + relative * abs(right)
                except (ValueError, OverflowError):
                    changes += 1
    metrics.update(
        compared_count=comparisons, changed_values=changes, mismatch_count=changes
    )
    if kind == "compare":
        metrics.update(max_abs_error=max_absolute, max_rel_error=max_relative)
    valid &= changes == 0 and bool(comparisons)
    return bool(valid), metrics, evidence


def _report(process: dict) -> tuple[bool | None, dict]:
    if process["timed_out"] or process["stdout"]["truncated"]:
        return None, {}
    try:
        report = json.loads(process["stdout"]["text"])
        checks = report["checks"]
        if (
            report.get("schema_version") != "aworld.check-report/v1"
            or not isinstance(checks, list)
            or not checks
            or any(
                not isinstance(c, dict)
                or not isinstance(c.get("id"), str)
                or not c["id"]
                or type(c.get("passed")) is not bool
                for c in checks
            )
        ):
            return None, {}
        if len({c["id"] for c in checks}) != len(checks):
            return None, {}
        metrics = report.get("metrics", {})
        if not isinstance(metrics, dict) or any(
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,80}", name)
            or type(value) not in {int, float}
            or not math.isfinite(value)
            for name, value in metrics.items()
        ):
            return None, {}
        return all(c["passed"] for c in checks), metrics
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, {}


async def _command(
    check: dict,
    files: Mapping[str, Path],
    input_files: Mapping[str, Path],
    *,
    working_dir: Path,
    limits: ValidationLimits,
) -> tuple[str, dict, dict]:
    argv = check.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(a, str) for a in argv)
    ):
        raise ValueError("command checks require argv, never shell text")

    def resolve(mapping):
        def replace(match):
            collection = mapping if match[1] == "artifact" else input_files
            if match[2] not in collection:
                raise ValueError("unknown command file placeholder")
            return str(Path(collection[match[2]]).absolute())

        return [re.sub(r"\{(artifact|input):([^{}]+)\}", replace, arg) for arg in argv]

    command = resolve(files)
    executable = shutil.which(command[0], path=isolated_env(check.get("env"))["PATH"])
    checker_files = {
        str(path): working_dir / path for path in check.get("checker_files", [])
    }
    if executable:
        checker_files["__executable__"] = Path(executable).resolve()
    for index, arg in enumerate(command[1:], 1):
        path = working_dir / arg
        if path.is_file() and str(path) not in {
            str(p) for p in (*files.values(), *input_files.values())
        }:
            checker_files[f"__argv_file_{index}__"] = path
    checker_bindings = snapshot_bindings(checker_files, limits)
    if any(b["state"] != "regular" for b in checker_bindings.values()):
        raise ValueError("checker code or executable cannot be fingerprinted")
    process_limits = limits.process
    if "timeout_seconds" in check:
        from dataclasses import replace

        timeout = _number(check["timeout_seconds"])
        if timeout <= 0 or timeout > limits.process.timeout_seconds:
            raise ValueError("check timeout exceeds caller's operation bound")
        process_limits = replace(process_limits, timeout_seconds=timeout)
    process = await run_bounded(
        command, cwd=working_dir, env=check.get("env"), limits=process_limits
    )
    passed, reported_metrics = _report(process)
    evidence = {
        "process": process,
        "checker_files": checker_bindings,
        "checker_paths": {
            name: str(Path(path).absolute()) for name, path in checker_files.items()
        },
        "report_passed": passed,
        "calibration": "unverified",
        "negative_controls": [],
    }
    controls = check.get("negative_controls", [])
    if not isinstance(controls, list) or len(controls) > 16:
        raise ValueError("negative_controls must be a bounded list")
    calibrated = bool(controls)
    for control in controls:
        replacements = (
            control.get("replacements") if isinstance(control, dict) else None
        )
        if not isinstance(replacements, dict) or not replacements:
            raise ValueError(
                "negative control replacements map artifact keys to input fixture keys"
            )
        altered = dict(files)
        for artifact, fixture in replacements.items():
            if artifact not in files or fixture not in input_files:
                raise ValueError(
                    "negative control references an unknown artifact/fixture"
                )
            altered[artifact] = input_files[fixture]
        negative = await run_bounded(
            resolve(altered),
            cwd=working_dir,
            env=check.get("env"),
            limits=process_limits,
        )
        rejected, _ = _report(negative)
        expected_codes = control.get("expected_return_codes", [0, 1])
        if (
            not isinstance(expected_codes, list)
            or not expected_codes
            or any(
                type(code) is not int or not 0 <= code <= 255 for code in expected_codes
            )
        ):
            raise ValueError(
                "negative control expected_return_codes must be ordinary exit codes"
            )
        detected = rejected is False and negative["return_code"] in expected_codes
        calibrated &= detected
        evidence["negative_controls"].append(
            {"replacements": replacements, "detected": detected, "process": negative}
        )
    unchanged = snapshot_bindings(checker_files, limits) == checker_bindings
    evidence["checker_unchanged"] = unchanged
    if passed is None:
        return "unknown", {}, evidence
    if not passed or process["return_code"] != 0 or not unchanged:
        return "failed", {}, evidence
    if not calibrated:
        return "unknown", {}, evidence
    evidence["calibration"] = "negative_controls_rejected"
    return "passed", reported_metrics, evidence


async def validate_candidate(
    candidate_files: Mapping[str, Path],
    inputs: Mapping[str, Path],
    checks: Sequence[dict],
    *,
    scope: str = "",
    working_dir: Path | None = None,
    limits: ValidationLimits | None = None,
) -> dict:
    """Execute checks against current bytes. No caller-provided pass/metrics are consumed."""
    limits = limits or ValidationLimits()
    if (
        not isinstance(checks, (list, tuple))
        or not checks
        or len(checks) > limits.max_checks
    ):
        raise ValueError("validation needs a nonempty bounded check list")
    ids = [c.get("id") for c in checks if isinstance(c, dict)]
    if (
        len(ids) != len(checks)
        or any(
            not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", i)
            for i in ids
        )
        or len(ids) != len(set(ids))
    ):
        raise ValueError("every check requires a unique stable id")
    if len(canonical(checks)) > 512 * 1024:
        raise ValueError("check definitions exceed operation bound")
    bindings, contents, read_limits, total = {}, {}, {}, 0
    for label, mapping in (("artifacts", candidate_files), ("inputs", inputs)):
        bindings[label], contents[label] = {}, {}
        read_limits[label] = {}
        for name, path in mapping.items():
            if not isinstance(name, str) or not name:
                raise ValueError("file mappings require nonempty logical names")
            read_limits[label][name] = min(
                limits.max_file_bytes, max(0, limits.max_total_bytes - total)
            )
            binding, data = _read(Path(path), read_limits[label][name])
            bindings[label][name] = binding
            if data is not None:
                contents[label][name] = data
                total += len(data)
    bindings["check_definitions_sha256"] = definition_hash(checks)
    results, metrics = [], {}
    with tempfile.TemporaryDirectory(prefix="aworld-validation-") as directory:
        cwd = Path(working_dir) if working_dir is not None else Path(directory)
        for check in checks:
            await asyncio.sleep(0)
            result = {
                "id": check["id"],
                "kind": check.get("kind"),
                "path": check.get("path"),
                "status": "error",
                "success": False,
                "metrics": {},
                "definition_sha256": definition_hash([check]),
            }
            try:
                if check.get("kind") not in KINDS or any(
                    key in check for key in ("success", "passed", "metrics")
                ):
                    raise ValueError(
                        "unsupported check definition or caller-supplied result"
                    )
                if check["kind"] == "command":
                    status, measured, evidence = await _command(
                        check, candidate_files, inputs, working_dir=cwd, limits=limits
                    )
                else:
                    path = check.get("path")
                    if path not in candidate_files:
                        raise ValueError(
                            "check path is not a registered candidate artifact"
                        )
                    passed, measured, evidence = _builtin(
                        check,
                        contents["artifacts"].get(path),
                        bindings["artifacts"][path],
                        contents["inputs"],
                        limits,
                    )
                    status = "passed" if passed else "failed"
                if any(
                    type(value) not in {int, float} or not math.isfinite(value)
                    for value in measured.values()
                ):
                    raise ValueError("check produced a non-finite metric")
                result.update(
                    status=status,
                    success=status == "passed",
                    metrics=measured,
                    evidence=evidence,
                )
                metrics.update(
                    {
                        check["id"] + "." + name: value
                        for name, value in measured.items()
                    }
                )
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                SyntaxError,
                OverflowError,
                csv.Error,
            ) as error:
                result["error_type"] = type(error).__name__
                result["error"] = str(error)[:2048]
            results.append(result)
    after = {
        label: {
            name: _read(Path(path), read_limits[label][name])[0]
            for name, path in mapping.items()
        }
        for label, mapping in (("artifacts", candidate_files), ("inputs", inputs))
    }
    unchanged = all(after[key] == bindings[key] for key in after)
    all_readable = all(
        value["state"] == "regular"
        for key in ("artifacts", "inputs")
        for value in bindings[key].values()
    )
    success = (
        unchanged and all_readable and all(result["success"] for result in results)
    )
    if not unchanged:
        metrics = {}
    return {
        "schema_version": SCHEMA,
        "scope": scope,
        "success": success,
        "checks": results,
        "metrics": metrics,
        "bindings": bindings,
        "unchanged": unchanged,
        "after_bindings": after if not unchanged else None,
        "provenance": "executed_artifact_checks",
        "task_reward": "not_assessed",
    }


__all__ = [
    "SCHEMA",
    "KINDS",
    "CHECK_SCHEMA",
    "ValidationLimits",
    "ProcessLimits",
    "validate_candidate",
    "snapshot_bindings",
    "definition_hash",
    "describe_validation",
]
