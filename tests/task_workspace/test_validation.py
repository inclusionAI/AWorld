import asyncio
import hashlib
import os
import sys
import pytest
from aworld.core.task_workspace.validation import (
    ValidationLimits,
    validate_candidate,
    definition_hash,
    describe_validation,
)


def write(root, name, content):
    path = root / name
    path.write_text(content)
    return path


def validate(files, checks, inputs=None, **kwargs):
    return asyncio.run(
        validate_candidate(files, inputs or {}, checks, scope="test-scope", **kwargs)
    )


def test_csv_semantics_and_independent_reference(tmp_path):
    actual = write(tmp_path, "out.csv", "id,label,p,q\n2,b,0.3,0.7\n1,a,0.2,0.8\n")
    source = write(tmp_path, "input.csv", "id,label,p,q\n1,a,0.2,0.8\n2,b,0.3,0.7\n")
    checks = [
        dict(
            id="table",
            kind="csv",
            path="out",
            required_columns=["id", "label", "p", "q"],
            rows_equal=2,
            primary_key=["id"],
        ),
        dict(
            id="normalized",
            kind="numeric",
            path="out",
            format="csv",
            columns=["p", "q"],
            min_value=0,
            max_value=1,
            sum_equals=1,
            abs_tolerance=1e-12,
        ),
        dict(
            id="identity",
            kind="preserve",
            path="out",
            format="csv",
            input="source",
            primary_key=["id"],
            columns=["label"],
        ),
        dict(
            id="values",
            kind="compare",
            path="out",
            format="csv",
            input="source",
            primary_key=["id"],
            columns=["p", "q"],
            abs_tolerance=1e-6,
        ),
    ]
    result = validate({"out": actual}, checks, {"source": source})
    assert result["success"] and result["unchanged"], result
    assert result["metrics"]["values.max_abs_error"] == 0
    assert result["metrics"]["table.row_count"] == 2
    assert result["bindings"]["artifacts"]["out"] == dict(
        state="regular",
        size=actual.stat().st_size,
        sha256=hashlib.sha256(actual.read_bytes()).hexdigest(),
    )
    assert result["bindings"]["check_definitions_sha256"] == definition_hash(checks)
    # Wrong values still normalize; an independent reference must detect them.
    actual.write_text("id,label,p,q\n2,b,0.5,0.5\n1,a,0.5,0.5\n")
    wrong = validate({"out": actual}, checks, {"source": source})
    assert (
        not wrong["success"]
        and wrong["checks"][1]["success"]
        and not wrong["checks"][3]["success"]
    )
    assert wrong["metrics"]["values.max_abs_error"] > 0.29


@pytest.mark.parametrize(
    "content", ["id,v\n1,2\n1,3\n", "id,v\n2,3\n", "id,v\n1,changed\n"]
)
def test_preservation_detects_bad_keys_rows_and_values(tmp_path, content):
    source = write(tmp_path, "input.csv", "id,v\n1,2\n2,3\n")
    actual = write(tmp_path, "out.csv", content)
    assert not validate(
        {"out": actual},
        [
            dict(
                id="p",
                kind="preserve",
                path="out",
                format="csv",
                input="source",
                columns=["v"],
                primary_key=["id"],
            )
        ],
        {"source": source},
    )["success"]


@pytest.mark.parametrize(
    "content", ["a,a\n1,2\n", "a,b\n1\n", 'a,b\n"unterminated,2\n']
)
def test_bad_csv_is_rejected(tmp_path, content):
    result = validate(
        {"x": write(tmp_path, "x.csv", content)}, [dict(id="csv", kind="csv", path="x")]
    )
    assert not result["success"] and result["checks"][0]["status"] == "error"


@pytest.mark.parametrize(
    "content", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', "[1,"]
)
def test_json_rejects_duplicate_keys_nonfinite_and_invalid_syntax(tmp_path, content):
    assert not validate(
        {"x": write(tmp_path, "x.json", content)},
        [dict(id="json", kind="json", path="x")],
    )["success"]


def test_finite_values_and_independent_tolerance(tmp_path):
    actual = write(tmp_path, "x.json", '{"rows":[{"v":1.0001},{"v":2}]}')
    source = write(tmp_path, "r.json", '[{"v":1},{"v":2}]')
    check = dict(
        id="error",
        kind="compare",
        path="x",
        format="json",
        records_pointer="/rows",
        input="ref",
        columns=["v"],
        abs_tolerance=0.001,
    )
    assert validate({"x": actual}, [check], {"ref": source})["success"]
    check["abs_tolerance"] = 0.00001
    assert not validate({"x": actual}, [check], {"ref": source})["success"]
    actual.write_text('["nan","inf","1"]')
    result = validate(
        {"x": actual},
        [dict(id="finite", kind="numeric", path="x", format="json", columns=["value"])],
    )
    assert (
        not result["success"] and result["metrics"]["finite.invalid_value_count"] == 2
    )


def test_source_syntax_is_not_executed_and_hashes_are_real(tmp_path):
    source = write(
        tmp_path,
        "source.py",
        f"open({str(tmp_path / 'sentinel')!r},'w').write('side effect')\n",
    )
    checks = [
        dict(id="syntax", kind="source_syntax", path="code", language="python"),
        dict(
            id="hash",
            kind="sha256",
            path="code",
            expected=hashlib.sha256(source.read_bytes()).hexdigest(),
        ),
        dict(id="size", kind="file_size", path="code", min_bytes=1, max_bytes=1000),
    ]
    assert (
        validate({"code": source}, checks)["success"]
        and not (tmp_path / "sentinel").exists()
    )
    source.write_text("def broken(:\n")
    assert not validate({"code": source}, checks[:1])["success"]


def test_missing_symlink_fifo_directory_and_size_bounds(tmp_path):
    target = write(tmp_path, "target", "data")
    link = tmp_path / "link"
    link.symlink_to(target)
    for path in (tmp_path / "missing", link, tmp_path):
        assert not validate(
            {"x": path}, [dict(id="file", kind="regular_file", path="x")]
        )["success"]
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        assert not validate(
            {"x": fifo}, [dict(id="file", kind="regular_file", path="x")]
        )["success"]
    assert not validate(
        {"x": target},
        [dict(id="file", kind="nonempty", path="x")],
        limits=ValidationLimits(max_file_bytes=2),
    )["success"]


def checker(root, always_pass=False, mutate=False):
    script = root / "checker.py"
    script.write_text(
        "import json,pathlib,sys\np=pathlib.Path(sys.argv[1]);v=p.read_text();ok="
        + ("True" if always_pass else "v=='good'")
        + "\n"
        + ("p.write_text('mutated')\n" if mutate else "")
        + "print(json.dumps({'schema_version':'aworld.check-report/v1','checks':[{'id':'value','passed':ok}],'metrics':{'length':len(v)}}))\n"
    )
    return script


def command_check(script):
    return dict(
        id="quality",
        kind="command",
        argv=[sys.executable, str(script), "{artifact:out}"],
        negative_controls=[{"replacements": {"out": "wrong"}}],
    )


def test_command_must_detect_negative_fixture_and_fingerprints_checker(tmp_path):
    actual = write(tmp_path, "out", "good")
    wrong = write(tmp_path, "wrong", "bad")
    script = checker(tmp_path)
    check = command_check(script)
    result = validate({"out": actual}, [check], {"wrong": wrong}, working_dir=tmp_path)
    assert result["success"] and result["metrics"]["quality.length"] == 4, result
    evidence = result["checks"][0]["evidence"]
    assert set(evidence["checker_paths"]) == set(evidence["checker_files"])
    assert str(script) in evidence["checker_paths"].values()
    assert (
        evidence["process"]["return_code"] == 0
        and evidence["negative_controls"][0]["detected"]
    )
    assert any(
        b.get("sha256") == hashlib.sha256(script.read_bytes()).hexdigest()
        for b in evidence["checker_files"].values()
    )
    check.pop("negative_controls")
    unknown = validate({"out": actual}, [check], {"wrong": wrong}, working_dir=tmp_path)
    assert (
        not unknown["success"]
        and unknown["checks"][0]["status"] == "unknown"
        and not unknown["metrics"]
    )


def test_exit_zero_and_always_pass_are_not_reliable(tmp_path):
    actual = write(tmp_path, "out", "good")
    wrong = write(tmp_path, "wrong", "bad")
    result = validate(
        {"out": actual},
        [command_check(checker(tmp_path, always_pass=True))],
        {"wrong": wrong},
        working_dir=tmp_path,
    )
    assert not result["success"] and result["checks"][0]["status"] == "unknown"
    result = validate(
        {"out": actual},
        [dict(id="exit", kind="command", argv=[sys.executable, "-c", "print('ok')"])],
        working_dir=tmp_path,
    )
    assert result["checks"][0]["evidence"]["process"]["return_code"] == 0
    assert not result["success"] and result["checks"][0]["status"] == "unknown"


def test_mutating_checker_invalidates_candidate_and_input_bindings(tmp_path):
    actual = write(tmp_path, "out", "good")
    wrong = write(tmp_path, "wrong", "bad")
    result = validate(
        {"out": actual},
        [command_check(checker(tmp_path, mutate=True))],
        {"wrong": wrong},
        working_dir=tmp_path,
    )
    assert not result["success"] and not result["unchanged"] and not result["metrics"]


def test_model_results_and_duplicate_check_ids_are_rejected(tmp_path):
    output = write(tmp_path, "out", "good")
    result = validate(
        {"out": output},
        [
            dict(
                id="fake",
                kind="nonempty",
                path="out",
                metrics={"quality": 1},
                success=True,
            )
        ],
    )
    assert not result["success"] and not result["metrics"]
    with pytest.raises(ValueError, match="unique"):
        validate({"out": output}, [dict(id="x", kind="exists", path="out")] * 2)
    assert "numeric" in describe_validation()["kinds"]
