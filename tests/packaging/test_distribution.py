"""Build and install real distributions outside the source checkout."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    work = tmp_path_factory.mktemp("distribution")
    output = work / "artifacts"
    run(sys.executable, str(ROOT / "scripts/build_packages.py"), "--no-isolation", "--outdir", str(output))
    assert json.loads((output / "packages.json").read_text())["version"] == "1.0.0a5"
    return work, output


def test_wheel_contains_one_kernel_and_explicit_dependencies(artifacts):
    _, output = artifacts
    with zipfile.ZipFile(output / "aworld-1.0.0a5-py3-none-any.whl") as wheel:
        files = set(wheel.namelist())
        assert {"aworld/cli/main.py", "aworld/core/agent/loop.py", "aworld/core/session/models.py",
                "aworld/core/agent/usage.py", "aworld/core/context/budget.py", "aworld/models/token_accounting.py"} <= files
        assert not any(name.startswith("aworldv1/") for name in files)
        assert not any("llm_agent" in name or "/runners/" in name or "/events/" in name for name in files)
        assert {"aworld/models/openai_provider.py", "aworld/core/llm_provider.py",
                "aworld/core/context/base.py", "aworld/config/cl100k_base.tiktoken"} <= files
        metadata = Parser().parsestr(wheel.read("aworld-1.0.0a5.dist-info/METADATA").decode())
        assert metadata["Version"] == "1.0.0a5"
        assert all("extra ==" in value for value in metadata.get_all("Requires-Dist", []))
        scripts = wheel.read("aworld-1.0.0a5.dist-info/entry_points.txt").decode()
        assert "aworld = aworld.cli.main:main" in scripts
        assert "legacy" not in scripts and "aworldv1" not in scripts
    with zipfile.ZipFile(output / "aworld_cli-1.0.0a5-py3-none-any.whl") as wheel:
        files = [name for name in wheel.namelist() if name.endswith(".py")]
        assert set(files) == {"aworld_cli/__init__.py", "aworld_cli/__main__.py", "aworld_cli/entrypoint.py"}
        metadata = Parser().parsestr(wheel.read("aworld_cli-1.0.0a5.dist-info/METADATA").decode())
        assert "aworld==1.0.0a5" in metadata.get_all("Requires-Dist")


def test_normal_install_and_both_commands_work_without_source_or_extras(artifacts):
    work, output = artifacts
    environment = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(environment))
    python = environment / "bin/python"
    run("uv", "pip", "install", "--offline", "--python", str(python),
        str(output / "aworld-1.0.0a5-py3-none-any.whl"), str(output / "aworld_cli-1.0.0a5-py3-none-any.whl"))
    for command in ("aworld", "aworld-cli"):
        results = [json.loads(line) for line in run(str(environment / "bin" / command), "run", "--demo", "--no-skills",
            "--task", "hello", "--follow-up", "again", "--json", cwd=work).splitlines()]
        assert [result["status"] for result in results] == ["completed", "completed"]
        assert results[0]["session_id"] == results[1]["session_id"]
    run(str(python), "-I", "-c", "import aworld, importlib.util; assert 'site-packages' in aworld.__file__; assert importlib.util.find_spec('httpx') is None; assert importlib.util.find_spec('aworldv1') is None", cwd=work)


def test_sdist_rebuild_is_self_contained_and_reproducible(artifacts):
    work, output = artifacts
    for name, wheel in (("aworld", "aworld"), ("aworld_cli", "aworld_cli")):
        unpacked = work / (name + "-source")
        with tarfile.open(output / f"{name}-1.0.0a5.tar.gz") as archive:
            archive.extractall(unpacked, filter="data")
        source = next(unpacked.iterdir())
        rebuilt = work / (name + "-rebuilt")
        run(sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(rebuilt), str(source))
        filename = f"{wheel}-1.0.0a5-py3-none-any.whl"
        assert (rebuilt / filename).read_bytes() == (output / filename).read_bytes()


def test_pep660_editable_install_uses_canonical_namespace(artifacts):
    work, _ = artifacts
    environment = work / "editable"
    run("uv", "venv", "--python", sys.executable, str(environment))
    python = environment / "bin/python"
    run("uv", "pip", "install", "--offline", "--python", str(python), "-e", str(ROOT), "-e", str(ROOT / "aworld-cli"))
    run(
        str(python),
        "-I",
        "-c",
        (
            "import aworld; import importlib.metadata; "
            "from aworld.cli.main import main; "
            "assert aworld.__version__ == importlib.metadata.version('aworld') == '1.0.0a5'"
        ),
        cwd=work,
    )
    assert json.loads(run(str(environment / "bin/aworld-cli"), "run", "--demo", "--no-skills", "--task", "editable", "--json", cwd=work))["status"] == "completed"


def test_installed_wheel_reuses_the_real_provider_outside_checkout(artifacts):
    work, output = artifacts
    environment = work / "live-provider"
    run("uv", "venv", "--python", sys.executable, str(environment))
    python = environment / "bin/python"
    run("uv", "pip", "install", "--offline", "--python", str(python),
        str(output / "aworld-1.0.0a5-py3-none-any.whl") + "[llm]")
    # Construct the actual backend and exercise complete()/SDK parsing without
    # any source checkout on sys.path. A missing transitive import fails here.
    code = """
import asyncio, httpx, subprocess
from unittest.mock import patch
async def check():
 with patch.object(subprocess, 'Popen', side_effect=AssertionError('unexpected installation')):
  from aworld.models.chat_completions import ChatCompletionsModel
  from aworld.models.openai_provider import OpenAIProvider
  from aworld.core.agent.messages import ModelRequest, UserMessage
  model = ChatCompletionsModel(model='fixture', base_url='http://fixture/v1')
  assert isinstance(model._provider, OpenAIProvider)
  client = model._provider.async_provider
  await client.close()
  def handle(request):
   return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'installed'}}]})
  model._provider.async_provider = client.with_options(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
  try:
   assert (await model.complete(ModelRequest('', (UserMessage('test'),), ()))).content == 'installed'
  finally:
   await model.aclose()
asyncio.run(check())
"""
    run(str(python), "-I", "-c", code, cwd=work)
