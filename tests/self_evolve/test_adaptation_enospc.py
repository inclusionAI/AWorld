import errno
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from aworld.self_evolve.controllers import run_capability_validation as capability
from aworld.self_evolve.controllers import run_repair_conformance as conformance
from aworld.self_evolve.controllers import run_replay_adaptation as adaptation
from aworld.self_evolve.controllers.run_generation_helpers import _typed_repair_frontiers
from aworld.self_evolve.controllers.run_iteration_helpers import _iteration_validation_feedback
from aworld.self_evolve.controllers.run_replay_adaptation import ReplayAdaptationResult
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.repair_conformance import RepairConformanceContract
from aworld.self_evolve.repair_conformance_diagnostics import _conformance_gate_blocks_population
from aworld.self_evolve.replay_adaptation_diagnostics import _replay_adaptation_exception_details
from aworld.self_evolve import replay_adaptation as seed_module
from aworld.self_evolve.replay_adaptation import ReplayAdaptationCompiler, ReplayPreflightReport
from aworld.self_evolve.replay_capability import ReplayCapabilityError
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant, DatasetRecipe, GateResult, SelfEvolveTargetRef


class _ChildReportedErrno(RuntimeError):
    errno = errno.ENOSPC


def _wrapped_child_error():
    error = ReplayCapabilityError(
        "compiler subprocess failed",
        details={"stderr": "OSError: [Errno 28] No space left on device", "errno": errno.ENOSPC},
    )
    error.__cause__ = OSError(errno.ENOSPC, "child-reported failure")
    return error


@pytest.mark.parametrize("candidate_capability", [False, True])
def test_host_enospc_is_native_shared_infrastructure(candidate_capability):
    # Deliberately avoid the usual error text: classification must use errno.
    error = OSError(errno.ENOSPC, "arbitrary localized message")
    details = _replay_adaptation_exception_details(error, candidate_capability=candidate_capability)

    assert details["code"] == "replay_adaptation_storage_exhausted"
    assert details["failure_class"] == details["failure_owner"] == "infrastructure"
    assert details["failure_scope"] == "shared_run"
    assert details["failure_source"] == "native"
    assert details["repairable"] is False
    assert details["error_errno"] == errno.ENOSPC
    assert "diagnostics" not in details  # No candidate compiler repair recipe.
    event = details["failure_event"]
    assert event["code"] == details["code"]
    assert event["owner"] == "infrastructure" and event["scope"] == "shared_run"
    assert event["repairable"] is False and event["stage"] == "adaptation"
    assert details["causal_failure_events"] == [event]


@pytest.mark.parametrize("error", [
    RuntimeError("OSError: [Errno 28] No space left on device"),
    OSError("[Errno 28] No space left on device"),
    OSError(errno.EACCES, "[Errno 28] No space left on device"),
    shutil.Error([("source", "destination", "[Errno 28] No space left on device")]),
    _ChildReportedErrno("No space left on device"),
    _wrapped_child_error(),
])
def test_messages_and_child_errors_do_not_establish_host_enospc(error):
    details = _replay_adaptation_exception_details(error, candidate_capability=True)

    assert details["failure_class"] == details["failure_owner"] == "candidate"
    assert details["failure_scope"] == "candidate"
    assert details["repairable"] is True
    assert details["failure_event"]["owner"] == "candidate"
    assert details["failure_event"]["code"] == "invalid_replay_capability_compile"
    assert "error_errno" not in details


def test_host_workspace_seed_copy_enospc_reaches_native_adaptation_gate(tmp_path, monkeypatch):
    source = tmp_path / "seed.txt"
    source.write_text("small seed\n")
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case", input="task"),),
        recipe=DatasetRecipe(source={}, split_seed="seed", splits={}),
    )
    compiler = ReplayAdaptationCompiler()
    monkeypatch.setattr(adaptation, "discover_replay_capability", lambda _: None)
    monkeypatch.setattr(compiler, "preflight", lambda **_: ReplayPreflightReport(
        schema_version=seed_module.REPLAY_PREFLIGHT_SCHEMA_VERSION, requirements=(), fingerprint="sha256:preflight",
    ))
    monkeypatch.setattr(seed_module, "_git_tracked_workspace_paths", lambda _: (Path("seed.txt"),))
    copy_attempts = []

    def full_host_filesystem(src, dst):
        error = OSError(errno.ENOSPC, "host copy failed", str(src), None, str(dst))
        copy_attempts.append((src, dst, error))
        raise error

    monkeypatch.setattr(seed_module.shutil, "copy2", full_host_filesystem)
    result = adaptation.prepare_replay_adaptation(
        adaptation.ReplayAdaptationRequest(
            run_id="copy", dataset=dataset, capability_skill_root=tmp_path,
            candidate_package_fingerprint="sha256:candidate", emit_progress=False,
        ),
        adaptation.ReplayAdaptationRuntime(store=FilesystemSelfEvolveStore(tmp_path), compiler=compiler),
        adaptation.ReplayAdaptationState(),
    )

    # Exercise prepare -> compiler.compile -> _copy_workspace_seed -> tracked
    # shutil.copy2, while simulating the syscall failure without filling disk.
    assert len(copy_attempts) == 1
    src, dst, error = copy_attempts[0]
    assert src == source and dst.name == "seed.txt" and dst.parent.name == "workspace_seed"
    assert type(error) is OSError and error.errno == errno.ENOSPC
    assert result.bundle is None and not result.gate.passed
    details = result.gate.details
    assert details["type"] == "OSError" and details["error_errno"] == errno.ENOSPC
    assert details["failure_owner"] == "infrastructure"
    assert details["failure_scope"] == "shared_run" and details["failure_source"] == "native"
    assert details["repairable"] is False
    assert details["failure_event"]["code"] == "replay_adaptation_storage_exhausted"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", ["conformance", "capability"])
@pytest.mark.parametrize("host_enospc", [False, True])
async def test_adaptation_errno_attribution_survives_both_compile_wrappers(
    tmp_path, monkeypatch, wrapper, host_enospc,
):
    skill = tmp_path / "skills/demo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Demo\n")
    target = SimpleNamespace(identity=SelfEvolveTargetRef("skill", "demo", str(skill)), baseline_skill_roots=())
    candidate = CandidateVariant(
        candidate_id="candidate", target=target.identity, content="# Demo\n", rationale="repair",
        files=(CandidateFileDelta("replay/compiler.py", content="print('compiler')\n"),),
    )
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case", input="task"),),
        recipe=DatasetRecipe(source={}, split_seed="seed", splits={}),
    )
    error = OSError(errno.ENOSPC, "host copy failed") if host_enospc else _wrapped_child_error()
    details = _replay_adaptation_exception_details(error, candidate_capability=True)
    adaptation_gate = GateResult(
        "replay_adaptation", False, "replay adaptation compilation failed",
        {**details, "type": type(error).__name__, "reason": str(error)},
    )
    store = FilesystemSelfEvolveStore(tmp_path)

    def overlay(**_):
        return SimpleNamespace(candidate_skill_path=skill)

    if wrapper == "conformance":
        monkeypatch.setattr(conformance, "execute_replay_adaptation", lambda *_: ReplayAdaptationResult(None, adaptation_gate))
        result = await conformance.preflight_candidate_repair_conformance(
            conformance.RepairConformancePreflightRequest(
                run_id="run", target=target, dataset=dataset, candidate=candidate,
                contract=RepairConformanceContract(
                    focus_candidate_id="parent", failure_codes=("compile_failed",), interaction_progress=0,
                    base_file_fingerprints={}, base_branch_fingerprints={}, required_branch_paths=("replay/compiler.py",),
                ),
            ),
            conformance.RepairConformancePreflightRuntime(
                store=store, replay_adaptation=None, create_candidate_skill_overlay=overlay,
            ),
        )
        gate = result.gate
    else:
        calls = []

        def adapt(request, _runtime):
            calls.append(request)
            if request.capability_skill_root is None:
                return ReplayAdaptationResult(None, GateResult("replay_capability", False, "candidate support required"))
            return ReplayAdaptationResult(None, adaptation_gate)

        monkeypatch.setattr(capability, "execute_replay_adaptation", adapt)
        monkeypatch.setattr(capability, "_replay_manifest_compatibility_gate", lambda **_: None)
        result = await capability.validate_candidate_capabilities(
            capability.CapabilityValidationRequest("run", target, dataset, candidate, (SimpleNamespace(kind="http_resource"),)),
            capability.CapabilityValidationPolicy(replay_enabled=True),
            capability.CapabilityValidationRuntime(
                store=store, replay_adaptation=None, create_candidate_skill_overlay=overlay,
                validate_applicable_capabilities=lambda **_: (SimpleNamespace(capability_type="replay", passed=True, diagnostics=()),),
            ),
        )
        assert len(calls) == 2
        gate = result.gates[0]

    assert not gate.passed
    event = gate.details["failure_event"]
    expected_owner = "infrastructure" if host_enospc else "candidate"
    assert gate.details["failure_class"] == gate.details["failure_owner"] == expected_owner
    assert gate.details["failure_source"] == "native"
    assert gate.details["failure_scope"] == ("shared_run" if host_enospc else "candidate")
    assert gate.details["repairable"] is (not host_enospc)
    assert event["owner"] == expected_owner and event["repairable"] is (not host_enospc)
    assert _conformance_gate_blocks_population(gate) is host_enospc
    if host_enospc:
        assert event["code"] == "replay_adaptation_storage_exhausted"
        if wrapper == "conformance":
            assert event == adaptation_gate.details["failure_event"]
            assert gate.gate_name == adaptation_gate.gate_name

    feedback = _iteration_validation_feedback(
        candidate=candidate, baseline_summary=None, candidate_summary=None, held_out_summary=None, failed_gates=[gate],
    )
    if host_enospc:
        assert all("repair_candidate_package" not in summary.metrics for summary in feedback)
        assert not any(frontier.repairable for frontier in _typed_repair_frontiers(feedback))
    else:
        assert any("repair_candidate_package" in summary.metrics for summary in feedback)
