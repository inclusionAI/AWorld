import asyncio
import hashlib
import os
import sys
import pytest
from aworld.core.task_workspace.probes import (
    ProcessLimits,
    probe_python,
    probe_argv,
    describe_probes,
)
from aworld.core.task_workspace._process import run_bounded


def test_actual_interpreter_signature_doc_call_and_parent_isolation(tmp_path):
    module = tmp_path / "fixture_api.py"
    module.write_text('''"""Public fixture API."""
import os
__version__='1.2.3'
os.environ['PROBE_PARENT_MARKER']='child'
def scale(value: int, *, factor: int = 2):
    """Scale a value using a keyword factor."""
    return {'answer':value*factor}
''')
    before = os.environ.get("PROBE_PARENT_MARKER")
    result = asyncio.run(
        probe_python(
            sys.executable,
            "fixture_api",
            "scale",
            working_dir=tmp_path,
            call={"args": [3], "kwargs": {"factor": 4}, "result": "json"},
        )
    )
    assert result["success"], result
    report = result["report"]
    assert report["module_file"] == str(module) and report["module_version"] == "1.2.3"
    assert "factor" in report["signature"] and "Scale a value" in report["doc"]
    assert report["json_result"] == {"answer": 12}
    assert (
        os.environ.get("PROBE_PARENT_MARKER") == before
        and "fixture_api" not in sys.modules
    )
    assert result["task_correctness"] == "not_assessed"


def test_python_failure_preserves_stack_and_actual_return_code(tmp_path):
    (tmp_path / "fixture_failure.py").write_text(
        "def fail():\n    raise ValueError('fixture failure')\n"
    )
    result = asyncio.run(
        probe_python(
            sys.executable,
            "fixture_failure",
            "fail",
            working_dir=tmp_path,
            call={"args": [], "kwargs": {}},
        )
    )
    assert not result["success"] and result["process"]["return_code"] == 1
    assert (
        result["report"]["error_type"] == "ValueError"
        and "fixture_failure.py" in result["report"]["traceback"]
    )


@pytest.mark.parametrize("failing", [False, True])
def test_async_api_call_executes_body_and_reports_failure(tmp_path, failing):
    (tmp_path / "async_fixture.py").write_text(
        "import asyncio\nasync def execute():\n    await asyncio.sleep(0)\n"
        + (
            "    raise ValueError('async failure')\n"
            if failing
            else "    return {'executed': True}\n"
        )
    )
    result = asyncio.run(
        probe_python(
            sys.executable,
            "async_fixture",
            "execute",
            working_dir=tmp_path,
            call={"args": [], "kwargs": {}, "result": "json"},
        )
    )
    assert result["report"]["call_awaited"]
    assert result["success"] is not failing
    if failing:
        assert result["report"]["error_type"] == "ValueError"
        assert "async_fixture.py" in result["report"]["traceback"]
    else:
        assert result["report"]["json_result"] == {"executed": True}


def test_process_environment_only_contains_explicit_task_values(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_RUNTIME_TEST_TOKEN", "do-not-forward")
    result = asyncio.run(
        probe_argv(
            [
                sys.executable,
                "-c",
                "import os;print(os.getenv('PRIVATE_RUNTIME_TEST_TOKEN'));print(os.getenv('TASK_PROBE_VALUE'))",
            ],
            working_dir=tmp_path,
            env={"TASK_PROBE_VALUE": "provided"},
        )
    )
    assert (
        result["success"] and result["process"]["stdout"]["text"] == "None\nprovided\n"
    )
    assert "PRIVATE_RUNTIME_TEST_TOKEN" not in result["process"]["environment_keys"]
    assert describe_probes()["python"]["required"] == ["interpreter", "module"]


def test_stdout_bounded_but_full_stream_digest_is_preserved(tmp_path):
    data = b"x" * 300000
    result = asyncio.run(
        run_bounded(
            [sys.executable, "-c", f"import sys;sys.stdout.write('x'*{len(data)})"],
            cwd=tmp_path,
            limits=ProcessLimits(output_bytes=128),
        )
    )
    assert result["return_code"] == 0 and result["stdout"]["bytes"] == len(data)
    assert result["stdout"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["stdout"]["truncated"] and len(result["stdout"]["text"]) < 200


def test_operation_timeout_is_not_success(tmp_path):
    result = asyncio.run(
        probe_argv(
            [sys.executable, "-c", "import time;time.sleep(30)"],
            working_dir=tmp_path,
            limits=ProcessLimits(timeout_seconds=0.15),
        )
    )
    assert result["process"]["timed_out"] and not result["success"]


def test_cancellation_reaps_probe_process(tmp_path):
    pidfile = tmp_path / "pid"

    async def scenario():
        task = asyncio.create_task(
            run_bounded(
                [
                    sys.executable,
                    "-c",
                    f"import os,pathlib,time;pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(30)",
                ],
                cwd=tmp_path,
            )
        )
        for _ in range(100):
            if pidfile.exists():
                break
            await asyncio.sleep(0.01)
        assert pidfile.exists()
        pid = int(pidfile.read_text())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancellation must propagate")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("probe survived cancellation")

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX file resource limit")
def test_file_resource_limit_is_actually_enforced(tmp_path):
    target = tmp_path / "bounded.bin"
    result = asyncio.run(
        probe_argv(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path;Path({str(target)!r}).write_bytes(b'x'*100000)",
            ],
            working_dir=tmp_path,
            limits=ProcessLimits(file_bytes=1024),
        )
    )
    assert not result["success"]
    assert target.stat().st_size <= 1024
    assert result["process"]["resource_capabilities"]["file_size"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group cleanup")
def test_cancellation_stops_import_spawned_descendant(tmp_path):
    ready, escaped_write = tmp_path / "ready", tmp_path / "descendant-write"
    descendant = f"import time,pathlib;time.sleep(.5);pathlib.Path({str(escaped_write)!r}).write_text('survived');time.sleep(30)"
    parent = f"import subprocess,sys,time,pathlib;subprocess.Popen([sys.executable,'-c',{descendant!r}]);pathlib.Path({str(ready)!r}).write_text('ready');time.sleep(30)"

    async def scenario():
        task = asyncio.create_task(
            run_bounded([sys.executable, "-c", parent], cwd=tmp_path)
        )
        for _ in range(100):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        assert ready.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.7)
        assert not escaped_write.exists()

    asyncio.run(scenario())
