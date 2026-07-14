from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset


REPLAY_ADAPTATION_SCHEMA_VERSION = "aworld.self_evolve.replay_adaptation.v1"
REPLAY_WORKSPACE_PLACEHOLDER = "${AWORLD_REPLAY_WORKSPACE}"
REPLAY_ARTIFACT_PLACEHOLDER = "${AWORLD_REPLAY_ARTIFACT_DIR}"

_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".aworld",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
    }
)
_IGNORED_FILE_SUFFIXES = (".pyc", ".pyo")
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_ed25519",
    }
)
_SENSITIVE_ENV_KEY = re.compile(
    r"(?i)(?:secret|token|password|credential|authorization|cookie|api[_-]?key)"
)
_LOCAL_ENDPOINT = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?[^\s\"'<>]*",
    re.IGNORECASE,
)
_HTTP_RESOURCE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_ABSOLUTE_LOCAL_PATH = re.compile(
    r"(?<![\w${])/(?:Users|home|private|var|tmp)/[^\s\"'<>|;,)}\]]+"
)
_CONTINUATION_MARKERS = (
    "continue the current task",
    "additional operator steering",
    "interrupt requested by operator",
)


class ReplayAdaptationError(RuntimeError):
    """Raised when a deterministic replay seed cannot be constructed."""


@dataclass(frozen=True)
class ReplayDependency:
    kind: str
    identifier: str
    status: str
    deterministic: bool
    adapter_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ReplayAdapterBinding:
    adapter_id: str
    dependency_id: str
    deterministic: bool
    environment: Mapping[str, str] = field(default_factory=dict)
    fixture_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayAdapterContext:
    case_id: str
    task_input: Any
    workspace_root: Path
    workspace_seed: Path
    artifact_root: Path


class ReplayDependencyAdapter(Protocol):
    adapter_id: str

    def bind(
        self,
        dependency: ReplayDependency,
        *,
        context: ReplayAdapterContext,
    ) -> ReplayAdapterBinding | None:
        """Return a deterministic fixture binding for a detected dependency."""


@dataclass(frozen=True)
class ReplayCaseAdaptation:
    case_id: str
    adapted_task_input: Any
    task_input_fingerprint: str
    dependencies: tuple[ReplayDependency, ...]
    bindings: tuple[ReplayAdapterBinding, ...]
    tool_names: tuple[str, ...]
    readiness: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayAdaptationBundle:
    schema_version: str
    source_workspace_root: str
    workspace_seed: str
    workspace_seed_fingerprint: str
    manifest_path: str
    cases: tuple[ReplayCaseAdaptation, ...]
    adaptation_fingerprint: str
    ready: bool

    def case(self, case_id: str) -> ReplayCaseAdaptation:
        for item in self.cases:
            if item.case_id == case_id:
                return item
        raise KeyError(f"replay adaptation case not found: {case_id}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReplayAdaptationCompiler:
    def __init__(
        self,
        *,
        adapters: Sequence[ReplayDependencyAdapter] = (),
        max_external_file_bytes: int = 10 * 1024 * 1024,
        max_workspace_files: int = 50_000,
        max_workspace_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        if max_external_file_bytes <= 0:
            raise ValueError("max_external_file_bytes must be positive")
        if max_workspace_files <= 0:
            raise ValueError("max_workspace_files must be positive")
        if max_workspace_bytes <= 0:
            raise ValueError("max_workspace_bytes must be positive")
        self.adapters = tuple(adapters)
        self.max_external_file_bytes = max_external_file_bytes
        self.max_workspace_files = max_workspace_files
        self.max_workspace_bytes = max_workspace_bytes

    def compile(
        self,
        *,
        dataset: SelfEvolveDataset,
        workspace_root: str | Path,
        artifact_root: str | Path,
    ) -> ReplayAdaptationBundle:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise ReplayAdaptationError(f"replay workspace does not exist: {workspace}")
        artifact = Path(artifact_root).expanduser().resolve()
        artifact.mkdir(parents=True, exist_ok=True)
        seed = artifact / "workspace_seed"
        if seed.exists():
            shutil.rmtree(seed)
        self._copy_workspace_seed(workspace, seed, artifact_root=artifact)

        cases = tuple(
            self._compile_case(
                case,
                workspace_root=workspace,
                workspace_seed=seed,
                artifact_root=artifact,
            )
            for case in dataset.cases
        )
        manifest_path = artifact / "workspace_manifest.json"
        manifest = _workspace_manifest(seed)
        _write_json_atomic(manifest_path, manifest)
        seed_fingerprint = _json_fingerprint(manifest)
        adaptation_payload = {
            "schema_version": REPLAY_ADAPTATION_SCHEMA_VERSION,
            "source_workspace_root": str(workspace),
            "workspace_seed_fingerprint": seed_fingerprint,
            "cases": [asdict(case) for case in cases],
        }
        bundle = ReplayAdaptationBundle(
            schema_version=REPLAY_ADAPTATION_SCHEMA_VERSION,
            source_workspace_root=str(workspace),
            workspace_seed=str(seed),
            workspace_seed_fingerprint=seed_fingerprint,
            manifest_path=str(manifest_path),
            cases=cases,
            adaptation_fingerprint=_json_fingerprint(adaptation_payload),
            ready=bool(cases) and all(case.readiness == "ready" for case in cases),
        )
        _write_json_atomic(artifact / "bundle.json", bundle.to_dict())
        return bundle

    def _compile_case(
        self,
        case: EvalCase,
        *,
        workspace_root: Path,
        workspace_seed: Path,
        artifact_root: Path,
    ) -> ReplayCaseAdaptation:
        task_input = _normalize_value(
            case.input,
            lambda text: _normalize_workspace_paths(text, workspace_root=workspace_root),
        )
        task_text = _text_fragments(task_input)
        dependencies: list[ReplayDependency] = []
        diagnostics: list[str] = []

        lowered = task_text.lower()
        if any(marker in lowered for marker in _CONTINUATION_MARKERS):
            dependencies.append(
                ReplayDependency(
                    kind="conversation_context",
                    identifier="prior-task-context",
                    status="context_incomplete",
                    deterministic=False,
                    detail="required prior task context is absent",
                )
            )

        local_endpoints = tuple(dict.fromkeys(_LOCAL_ENDPOINT.findall(task_text)))
        for endpoint in local_endpoints:
            dependencies.append(
                ReplayDependency(
                    kind="local_endpoint",
                    identifier=endpoint,
                    status="runtime_required",
                    deterministic=False,
                    detail="stateful local endpoint requires a registered replay adapter",
                )
            )

        external_urls = tuple(
            url
            for url in dict.fromkeys(_HTTP_RESOURCE.findall(task_text))
            if url not in local_endpoints
        )
        for url in external_urls:
            dependencies.append(
                ReplayDependency(
                    kind="http_resource",
                    identifier=url,
                    status="runtime_required",
                    deterministic=False,
                    detail="live HTTP content requires a deterministic replay adapter",
                )
            )

        for raw_path in tuple(dict.fromkeys(_ABSOLUTE_LOCAL_PATH.findall(task_text))):
            task_input, dependency = self._adapt_external_path(
                task_input,
                raw_path=raw_path,
                workspace_seed=workspace_seed,
            )
            dependencies.append(dependency)

        context = ReplayAdapterContext(
            case_id=case.case_id,
            task_input=task_input,
            workspace_root=workspace_root,
            workspace_seed=workspace_seed,
            artifact_root=artifact_root,
        )
        bindings: list[ReplayAdapterBinding] = []
        adapted_dependencies: list[ReplayDependency] = []
        for dependency in dependencies:
            binding = self._bind_dependency(dependency, context=context)
            if binding is None:
                adapted_dependencies.append(dependency)
                continue
            safe_binding = replace(
                binding,
                environment=_safe_adapter_environment(binding.environment),
            )
            bindings.append(safe_binding)
            adapted_dependencies.append(
                replace(
                    dependency,
                    status="adapter_bound",
                    deterministic=safe_binding.deterministic,
                    adapter_id=safe_binding.adapter_id,
                    detail="dependency is provided by a registered replay adapter",
                )
            )

        readiness = _case_readiness(adapted_dependencies)
        if readiness != "ready":
            diagnostics.append(f"replay adaptation is {readiness}")
        tool_names = _case_tool_names(case)
        return ReplayCaseAdaptation(
            case_id=case.case_id,
            adapted_task_input=task_input,
            task_input_fingerprint=_json_fingerprint(task_input),
            dependencies=tuple(adapted_dependencies),
            bindings=tuple(bindings),
            tool_names=tool_names,
            readiness=readiness,
            diagnostics=tuple(diagnostics),
        )

    def _adapt_external_path(
        self,
        task_input: Any,
        *,
        raw_path: str,
        workspace_seed: Path,
    ) -> tuple[Any, ReplayDependency]:
        source = Path(raw_path).expanduser()
        identifier = "local-file:" + hashlib.sha256(
            raw_path.encode("utf-8")
        ).hexdigest()[:16]
        if (
            not source.is_file()
            or _is_sensitive_path(source)
            or source.stat().st_size > self.max_external_file_bytes
        ):
            replacement = "${AWORLD_REPLAY_UNRESOLVED_PATH}"
            return (
                _replace_in_value(task_input, raw_path, replacement),
                ReplayDependency(
                    kind="local_file",
                    identifier=identifier,
                    status="unresolved",
                    deterministic=False,
                    detail="external path cannot be included in the replay seed",
                ),
            )
        fixture_name = (
            hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            + "-"
            + source.name
        )
        relative = Path(".aworld_replay_fixtures") / fixture_name
        destination = workspace_seed / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        replacement = f"{REPLAY_WORKSPACE_PLACEHOLDER}/{relative.as_posix()}"
        return (
            _replace_in_value(task_input, raw_path, replacement),
            ReplayDependency(
                kind="local_file",
                identifier=identifier,
                status="snapshotted",
                deterministic=True,
                detail="bounded local file copied into the replay seed",
            ),
        )

    def _bind_dependency(
        self,
        dependency: ReplayDependency,
        *,
        context: ReplayAdapterContext,
    ) -> ReplayAdapterBinding | None:
        for adapter in self.adapters:
            binding = adapter.bind(dependency, context=context)
            if binding is not None:
                return binding
        return None

    def _copy_workspace_seed(
        self,
        source: Path,
        destination: Path,
        *,
        artifact_root: Path,
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        file_count = 0
        byte_count = 0
        for current_root, directory_names, file_names in os.walk(source):
            current = Path(current_root)
            directory_names[:] = [
                name
                for name in directory_names
                if name not in _IGNORED_DIRECTORY_NAMES
                and not _is_within(current / name, artifact_root)
                and not _is_sensitive_path(current / name)
            ]
            relative_root = current.relative_to(source)
            target_root = destination / relative_root
            target_root.mkdir(parents=True, exist_ok=True)
            for file_name in file_names:
                source_path = current / file_name
                if (
                    file_name.endswith(_IGNORED_FILE_SUFFIXES)
                    or _is_sensitive_path(source_path)
                    or _is_within(source_path, artifact_root)
                ):
                    continue
                try:
                    metadata = source_path.lstat()
                except OSError as exc:
                    raise ReplayAdaptationError(
                        f"cannot inspect replay seed input: {source_path.name}: {exc}"
                    ) from exc
                if stat.S_ISLNK(metadata.st_mode):
                    resolved = source_path.resolve()
                    if not _is_within(resolved, source):
                        continue
                    target_path = target_root / file_name
                    target_path.symlink_to(os.readlink(source_path))
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                file_count += 1
                byte_count += metadata.st_size
                if file_count > self.max_workspace_files:
                    raise ReplayAdaptationError("workspace snapshot file limit exceeded")
                if byte_count > self.max_workspace_bytes:
                    raise ReplayAdaptationError("workspace snapshot byte limit exceeded")
                shutil.copy2(source_path, target_root / file_name)


def _normalize_workspace_paths(text: str, *, workspace_root: Path) -> str:
    normalized = text.replace(str(workspace_root), REPLAY_WORKSPACE_PLACEHOLDER)
    repository_name = re.escape(workspace_root.name)
    stale_pattern = (
        rf"/(?:Users|home)/[^/\s]+/Documents/workspace/{repository_name}"
    )
    return re.sub(stale_pattern, REPLAY_WORKSPACE_PLACEHOLDER, normalized)


def _normalize_value(value: Any, transform) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_value(item, transform) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(item, transform) for item in value)
    return value


def _replace_in_value(value: Any, source: str, destination: str) -> Any:
    return _normalize_value(value, lambda text: text.replace(source, destination))


def _text_fragments(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(_text_fragments(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_text_fragments(item) for item in value)
    return ""


def _case_tool_names(case: EvalCase) -> tuple[str, ...]:
    if case.trace_pack is None:
        return ()
    return tuple(
        dict.fromkeys(
            name
            for step in case.trace_pack.steps
            for name in step.tool_names
            if name
        )
    )


def _case_readiness(dependencies: Sequence[ReplayDependency]) -> str:
    statuses = {dependency.status for dependency in dependencies}
    if "context_incomplete" in statuses:
        return "context_incomplete"
    if "unresolved" in statuses:
        return "unresolved"
    if "runtime_required" in statuses or any(
        not dependency.deterministic for dependency in dependencies
    ):
        return "runtime_required"
    return "ready"


def _safe_adapter_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in environment.items()
        if not _SENSITIVE_ENV_KEY.search(str(key))
    }


def _is_sensitive_path(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in _SENSITIVE_NAMES
        or name.startswith(".env.")
        or "credential" in name
        or "secret" in name
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _workspace_manifest(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "type": "symlink",
                    "target": os.readlink(path),
                }
            )
            continue
        if not path.is_file():
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "type": "file",
                "size": len(data),
                "mode": stat.S_IMODE(path.stat().st_mode),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "schema_version": "aworld.self_evolve.workspace_manifest.v1",
        "entries": entries,
    }


def _json_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)
