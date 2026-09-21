from __future__ import annotations

import ast
import functools
import inspect
import types
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from aworld.self_evolve.controllers import (
    run_apply_phases,
    run_candidate_phases,
    run_lifecycle_bootstrap_execution,
    run_lifecycle_execution,
    run_lifecycle_iteration_execution,
    run_lifecycle_terminal_execution,
    run_measurement_phases,
    run_phase_assembly,
    run_phase_adapters,
    run_phase_context,
    run_screening_phases,
)
from aworld.self_evolve.runner import (
    _RUNNER_COMPAT_METHOD_NAMES,
    SelfEvolveRunner,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


_OWNER_MODULES = (
    run_lifecycle_execution,
    run_lifecycle_bootstrap_execution,
    run_lifecycle_iteration_execution,
    run_lifecycle_terminal_execution,
    run_phase_context,
    run_phase_adapters,
    run_phase_assembly,
    run_screening_phases,
    run_measurement_phases,
    run_candidate_phases,
    run_apply_phases,
)
_REQUIRED_COMPATIBILITY_SEAMS = (
    "_plan_candidate_measurement",
    "_execute_iteration_candidate",
    "_baseline_reuse_provenance",
    "_measurement_search_projection_execution",
    "_auto_apply_execution",
    "_verified_only_apply_execution",
    "_execute_screen_candidate_population",
)
_OPERATION_BY_SEAM = {
    "_plan_candidate_measurement": "plan_candidate_measurement",
    "_execute_iteration_candidate": "execute_iteration_candidate",
    "_baseline_reuse_provenance": "baseline_reuse_provenance",
    "_measurement_search_projection_execution": (
        "measurement_search_projection_execution"
    ),
    "_auto_apply_execution": "auto_apply_execution",
    "_verified_only_apply_execution": "verified_only_apply_execution",
    "_execute_screen_candidate_population": "execute_screen_candidate_population",
}


class _UnusedOptimizer:
    async def propose(self, _request: object) -> object:
        raise AssertionError("not used")


def _runner(tmp_path: Path, runner_type: type[SelfEvolveRunner] = SelfEvolveRunner):
    return runner_type(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=_UnusedOptimizer(),
    )


def _walk_runtime_graph(value: object, visited: set[int]) -> None:
    if id(value) in visited:
        return
    visited.add(id(value))
    if isinstance(value, (str, bytes, bytearray, int, float, bool, type(None))):
        return
    if isinstance(value, (type, types.ModuleType)):
        return
    if callable(value):
        assert not isinstance(getattr(value, "__self__", None), SelfEvolveRunner)
    if isinstance(value, functools.partial):
        _walk_runtime_graph(value.func, visited)
        _walk_runtime_graph(value.args, visited)
        _walk_runtime_graph(value.keywords or {}, visited)
    if isinstance(value, types.FunctionType):
        _walk_runtime_graph(value.__defaults__ or (), visited)
        _walk_runtime_graph(value.__kwdefaults__ or {}, visited)
        for cell in value.__closure__ or ():
            try:
                _walk_runtime_graph(cell.cell_contents, visited)
            except ValueError:
                pass
    if is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _walk_runtime_graph(getattr(value, item.name), visited)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _walk_runtime_graph(key, visited)
            _walk_runtime_graph(item, visited)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _walk_runtime_graph(item, visited)
    elif isinstance(value, (set, frozenset)):
        for item in value:
            _walk_runtime_graph(item, visited)
    else:
        namespace = getattr(value, "__dict__", None)
        if isinstance(namespace, Mapping):
            for item in namespace.values():
                _walk_runtime_graph(item, visited)
        slots = getattr(type(value), "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if hasattr(value, slot):
                _walk_runtime_graph(getattr(value, slot), visited)


def test_lifecycle_controllers_never_import_runner_or_cli_outward() -> None:
    for module in _OWNER_MODULES:
        tree = ast.parse(inspect.getsource(module))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert "aworld.self_evolve.runner" not in imports
        assert not any(name.startswith("aworld.self_evolve.cli") for name in imports)


def test_production_lifecycle_graph_contains_no_bound_runner(tmp_path: Path) -> None:
    _walk_runtime_graph(_runner(tmp_path)._lifecycle_execution(), set())


def test_graph_guard_finds_runner_callback_inside_ordinary_object(tmp_path: Path) -> None:
    class CallbackBox:
        pass

    runner = _runner(tmp_path)
    box = CallbackBox()
    box.nested = {"callbacks": [runner._plan_candidate_measurement]}
    with pytest.raises(AssertionError):
        _walk_runtime_graph(box, set())


@pytest.mark.parametrize("seam", _REQUIRED_COMPATIBILITY_SEAMS)
def test_instance_override_crosses_typed_compatibility_edge(
    tmp_path: Path,
    seam: str,
) -> None:
    runner = _runner(tmp_path)

    def override(*_args: object, **_kwargs: object) -> object:
        return object()

    setattr(runner, seam, override)
    lifecycle = runner._lifecycle_execution()
    assert lifecycle.context.overrides.get(seam) is override
    assert getattr(lifecycle.phases.operations, _OPERATION_BY_SEAM[seam]) is override


@pytest.mark.parametrize("seam", _REQUIRED_COMPATIBILITY_SEAMS)
def test_class_override_crosses_typed_compatibility_edge(
    tmp_path: Path,
    seam: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def override(self: SelfEvolveRunner, *_args: object, **_kwargs: object) -> object:
        return self

    monkeypatch.setattr(SelfEvolveRunner, seam, override)
    lifecycle = _runner(tmp_path)._lifecycle_execution()
    callback = lifecycle.context.overrides.get(seam)
    assert getattr(callback, "__func__", None) is override
    operation = getattr(lifecycle.phases.operations, _OPERATION_BY_SEAM[seam])
    assert getattr(operation, "__func__", None) is override


@pytest.mark.parametrize("seam", _REQUIRED_COMPATIBILITY_SEAMS)
def test_subclass_override_crosses_typed_compatibility_edge(
    tmp_path: Path,
    seam: str,
) -> None:
    def override(self: SelfEvolveRunner, *_args: object, **_kwargs: object) -> object:
        return self

    runner_type = type("CompatibilityRunner", (SelfEvolveRunner,), {seam: override})
    lifecycle = _runner(tmp_path, runner_type)._lifecycle_execution()
    callback = lifecycle.context.overrides.get(seam)
    assert getattr(callback, "__func__", None) is override
    operation = getattr(lifecycle.phases.operations, _OPERATION_BY_SEAM[seam])
    assert getattr(operation, "__func__", None) is override


def test_all_runner_adapters_have_typed_override_slots() -> None:
    typed_slots = {item.name for item in fields(run_phase_context.RunCompatibilityOverrides)}
    assert set(_RUNNER_COMPAT_METHOD_NAMES) == typed_slots


def test_phase_adapters_have_one_live_canonical_owner() -> None:
    adapter_tree = ast.parse(inspect.getsource(run_phase_adapters))
    adapter_names = {
        node.name
        for node in adapter_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name.endswith(("Adapter", "Override"))
    }
    assert adapter_names
    phase_modules = (run_screening_phases, run_candidate_phases)
    usage_names: set[str] = set()
    for module in phase_modules:
        tree = ast.parse(inspect.getsource(module))
        assert not {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name.endswith(("Adapter", "Override"))
        }
        usage_names.update(
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        )
    assert adapter_names <= usage_names


def test_phase_operation_annotations_are_unique() -> None:
    tree = ast.parse(inspect.getsource(run_phase_context))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RunPhaseOperations"
    )
    names = [
        node.target.id
        for node in owner.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    assert len(names) == len(set(names))


def test_runner_facade_and_phase_owners_have_responsibility_size_guards() -> None:
    runner_module = __import__("aworld.self_evolve.runner", fromlist=["*"])
    module_source = inspect.getsource(runner_module)
    tree = ast.parse(module_source)
    runner_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SelfEvolveRunner"
    )
    assert len(module_source.splitlines()) <= 1_600
    assert runner_class.end_lineno is not None
    assert runner_class.end_lineno - runner_class.lineno + 1 <= 1_000

    methods = {
        node.name: node
        for node in runner_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name, method in methods.items():
        if name in {"__init__", "_install_construction"}:
            continue
        assert method.end_lineno is not None
        assert method.end_lineno - method.lineno + 1 <= 150, name

    for module in _OWNER_MODULES:
        source = inspect.getsource(module)
        assert len(source.splitlines()) <= 800, module.__name__
        owner_tree = ast.parse(source)
        for node in ast.walk(owner_tree):
            if isinstance(node, ast.ClassDef):
                assert node.end_lineno is not None
                assert node.end_lineno - node.lineno + 1 <= 500, (
                    module.__name__,
                    node.name,
                )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno is not None
                assert node.end_lineno - node.lineno + 1 <= 250, (
                    module.__name__,
                    node.name,
                )
