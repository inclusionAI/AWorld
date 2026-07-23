from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, Protocol, Sequence

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.trajectory_context import task_input_requires_prior_context

if TYPE_CHECKING:
    from aworld.self_evolve.replay_capability import FrozenReplayCapability


REPLAY_ADAPTATION_SCHEMA_VERSION = "aworld.self_evolve.replay_adaptation.v1"
REPLAY_PREFLIGHT_SCHEMA_VERSION = "aworld.self_evolve.replay_preflight.v1"
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
    r"(?<![.:/\w${}])/(?!/)[^\s\"'<>|;,)}\]]+"
)
_NON_FILE_PATH_CONTEXT = re.compile(
    r"(?i)(?:"
    r"\b(?:get|post|put|patch|delete|head|options)"
    r"|\b(?:api\s+)?(?:route|endpoint|url|uri)"
    r"|\b(?:regex|pattern)"
    r")\s*(?::|=)?\s*$"
)
_STATEFUL_BROWSER_TOOL_TOKENS = frozenset(
    {
        "browser",
        "chrome",
        "chromium",
        "firefox",
        "safari",
        "playwright",
        "selenium",
        "puppeteer",
        "cdp",
    }
)
_STATEFUL_WEB_ACTION_TOKENS = frozenset(
    {"run", "search", "fetch", "open", "navigate", "click"}
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
class ReplayCapabilityRequirement:
    requirement_id: str
    kind: str
    identifier: str
    case_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: str
    detail: str | None = None


@dataclass(frozen=True)
class ReplayPreflightReport:
    schema_version: str
    requirements: tuple[ReplayCapabilityRequirement, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayAdapterBinding:
    adapter_id: str
    dependency_id: str
    deterministic: bool
    environment: Mapping[str, str] = field(default_factory=dict)
    fixture_paths: tuple[str, ...] = ()
    concurrency_mode: Literal[
        "isolated", "shared_read_only", "exclusive"
    ] = "exclusive"
    resource_key: str | None = None
    binding_fingerprint: str | None = None


REPLAY_BINDING_CONCURRENCY_MODES = (
    "exclusive",
    "isolated",
    "shared_read_only",
)


def validate_replay_binding_concurrency(
    binding: ReplayAdapterBinding,
) -> ReplayAdapterBinding:
    """Validate generic skill-owned scheduling metadata and fill safe defaults."""

    if binding.concurrency_mode not in REPLAY_BINDING_CONCURRENCY_MODES:
        raise ValueError(
            f"unsupported replay binding concurrency mode: {binding.concurrency_mode}"
        )
    resource_key = binding.resource_key
    if resource_key is not None:
        resource_key = resource_key.strip()
        if not resource_key:
            raise ValueError("replay binding resource_key must not be empty")
    if binding.concurrency_mode == "isolated":
        if resource_key is not None:
            raise ValueError(
                "isolated replay binding cannot declare a shared resource_key"
            )
        if not binding.deterministic:
            raise ValueError("isolated replay binding must be deterministic")
    elif resource_key is None:
        resource_key = f"replay-adapter:{binding.adapter_id}"
    binding_fingerprint = binding.binding_fingerprint
    if binding_fingerprint is not None:
        binding_fingerprint = binding_fingerprint.strip()
        if not binding_fingerprint:
            raise ValueError("replay binding fingerprint must not be empty")
    if binding_fingerprint is None:
        binding_fingerprint = _json_fingerprint(
            {
                "adapter_id": binding.adapter_id,
                "dependency_id": binding.dependency_id,
                "deterministic": binding.deterministic,
                "fixture_paths": list(binding.fixture_paths),
            }
        )
    return replace(
        binding,
        resource_key=resource_key,
        binding_fingerprint=binding_fingerprint,
    )


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
    environment_snapshot_path: str
    environment_fingerprint: str
    cases: tuple[ReplayCaseAdaptation, ...]
    adaptation_fingerprint: str
    ready: bool
    replay_capability: FrozenReplayCapability | None = None

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

    def preflight(
        self,
        *,
        dataset: SelfEvolveDataset,
        workspace_root: str | Path,
    ) -> ReplayPreflightReport:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise ReplayAdaptationError(f"replay workspace does not exist: {workspace}")
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for case in dataset.cases:
            task_input = _normalize_value(
                case.input,
                lambda text: _normalize_workspace_paths(
                    text,
                    workspace_root=workspace,
                ),
            )
            for dependency, _raw_path in self._analyze_case_dependencies(
                case,
                task_input=task_input,
                workspace_root=workspace,
            ):
                if dependency.deterministic:
                    continue
                key = (
                    dependency.kind,
                    dependency.identifier,
                    dependency.status,
                )
                item = grouped.setdefault(
                    key,
                    {"case_ids": [], "evidence_refs": [], "detail": dependency.detail},
                )
                if case.case_id not in item["case_ids"]:
                    item["case_ids"].append(case.case_id)
                evidence_ref = _context_evidence_ref(case)
                if evidence_ref not in item["evidence_refs"]:
                    item["evidence_refs"].append(evidence_ref)
        requirements = tuple(
            ReplayCapabilityRequirement(
                requirement_id=_requirement_id(kind, identifier),
                kind=kind,
                identifier=identifier,
                case_ids=tuple(value["case_ids"]),
                evidence_refs=tuple(value["evidence_refs"]),
                status=status,
                detail=value["detail"],
            )
            for (kind, identifier, status), value in sorted(grouped.items())
        )
        payload = {
            "schema_version": REPLAY_PREFLIGHT_SCHEMA_VERSION,
            "requirements": [asdict(item) for item in requirements],
        }
        return ReplayPreflightReport(
            schema_version=REPLAY_PREFLIGHT_SCHEMA_VERSION,
            requirements=requirements,
            fingerprint=_json_fingerprint(payload),
        )

    def compile(
        self,
        *,
        dataset: SelfEvolveDataset,
        workspace_root: str | Path,
        artifact_root: str | Path,
        additional_adapters: Sequence[ReplayDependencyAdapter] = (),
        replay_capability: FrozenReplayCapability | None = None,
    ) -> ReplayAdaptationBundle:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise ReplayAdaptationError(f"replay workspace does not exist: {workspace}")
        artifact = Path(artifact_root).expanduser().resolve()
        artifact.mkdir(parents=True, exist_ok=True)
        seed = artifact / "workspace_seed"
        if seed.is_symlink():
            seed.unlink()
        elif seed.exists():
            shutil.rmtree(seed)
        try:
            self._copy_workspace_seed(workspace, seed, artifact_root=artifact)
            cases = tuple(
                self._compile_case(
                    case,
                    workspace_root=workspace,
                    workspace_seed=seed,
                    artifact_root=artifact,
                    adapters=(*self.adapters, *additional_adapters),
                )
                for case in dataset.cases
            )
            self._assert_workspace_seed_limits(seed)
        except Exception:
            if seed.is_symlink():
                seed.unlink()
            elif seed.exists():
                shutil.rmtree(seed)
            raise
        manifest_path = artifact / "workspace_manifest.json"
        manifest = _workspace_manifest(seed)
        _write_json_atomic(manifest_path, manifest)
        seed_fingerprint = _json_fingerprint(manifest)
        environment_snapshot_path = artifact / "environment_snapshot.json"
        environment_snapshot = _environment_snapshot(cases)
        _write_json_atomic(environment_snapshot_path, environment_snapshot)
        environment_fingerprint = _json_fingerprint(environment_snapshot)
        adaptation_payload = {
            "schema_version": REPLAY_ADAPTATION_SCHEMA_VERSION,
            "source_workspace_root": str(workspace),
            "workspace_seed_fingerprint": seed_fingerprint,
            "environment_fingerprint": environment_fingerprint,
            "cases": [asdict(case) for case in cases],
            "replay_capability_fingerprint": (
                replay_capability.fingerprint
                if replay_capability is not None
                else None
            ),
        }
        bundle = ReplayAdaptationBundle(
            schema_version=REPLAY_ADAPTATION_SCHEMA_VERSION,
            source_workspace_root=str(workspace),
            workspace_seed=str(seed),
            workspace_seed_fingerprint=seed_fingerprint,
            manifest_path=str(manifest_path),
            environment_snapshot_path=str(environment_snapshot_path),
            environment_fingerprint=environment_fingerprint,
            cases=cases,
            adaptation_fingerprint=_json_fingerprint(adaptation_payload),
            ready=bool(cases) and all(case.readiness == "ready" for case in cases),
            replay_capability=replay_capability,
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
        adapters: Sequence[ReplayDependencyAdapter],
    ) -> ReplayCaseAdaptation:
        task_input = _normalize_value(
            case.input,
            lambda text: _normalize_workspace_paths(text, workspace_root=workspace_root),
        )
        analyzed_dependencies = self._analyze_case_dependencies(
            case,
            task_input=task_input,
            workspace_root=workspace_root,
        )
        dependencies: list[ReplayDependency] = []
        diagnostics: list[str] = []

        for dependency, raw_path in analyzed_dependencies:
            if raw_path is not None:
                task_input, dependency = self._adapt_external_path(
                    task_input,
                    raw_path=raw_path,
                    workspace_seed=workspace_seed,
                    dependency=dependency,
                )
            dependencies.append(dependency)

        tool_names = _case_tool_names(case)

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
            binding = self._bind_dependency(
                dependency,
                context=context,
                adapters=adapters,
            )
            if binding is None:
                adapted_dependencies.append(dependency)
                continue
            safe_binding = validate_replay_binding_concurrency(
                self._snapshot_adapter_fixtures(
                    replace(
                        binding,
                        environment=_safe_adapter_environment(binding.environment),
                    ),
                    workspace_seed=workspace_seed,
                )
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

    def _analyze_case_dependencies(
        self,
        case: EvalCase,
        *,
        task_input: Any,
        workspace_root: Path,
    ) -> tuple[tuple[ReplayDependency, str | None], ...]:
        dependency_input = _case_dependency_input(
            case,
            normalized_task_input=task_input,
            workspace_root=workspace_root,
        )
        analyzed: list[tuple[ReplayDependency, str | None]] = [
            (dependency, None)
            for dependency in _detected_runtime_dependencies(case, dependency_input)
        ]
        for raw_path in _absolute_local_path_references(
            _text_fragments(dependency_input)
        ):
            analyzed.append((self._external_path_dependency(raw_path), raw_path))
        return tuple(analyzed)

    def _external_path_dependency(self, raw_path: str) -> ReplayDependency:
        source = Path(raw_path).expanduser()
        identifier = "local-file:" + hashlib.sha256(
            raw_path.encode("utf-8")
        ).hexdigest()[:16]
        try:
            can_snapshot = (
                source.is_file()
                and not source.is_symlink()
                and not _is_sensitive_path(source)
                and source.stat().st_size <= self.max_external_file_bytes
            )
        except OSError:
            can_snapshot = False
        if not can_snapshot:
            return ReplayDependency(
                kind="local_file",
                identifier=identifier,
                status="unresolved",
                deterministic=False,
                detail="external path cannot be included in the replay seed",
            )
        return ReplayDependency(
            kind="local_file",
            identifier=identifier,
            status="snapshotted",
            deterministic=True,
            detail="bounded local file copied into the replay seed",
        )

    def _adapt_external_path(
        self,
        task_input: Any,
        *,
        raw_path: str,
        workspace_seed: Path,
        dependency: ReplayDependency | None = None,
    ) -> tuple[Any, ReplayDependency]:
        source = Path(raw_path).expanduser()
        dependency = dependency or self._external_path_dependency(raw_path)
        if not dependency.deterministic:
            replacement = "${AWORLD_REPLAY_UNRESOLVED_PATH}"
            return (
                _replace_in_value(task_input, raw_path, replacement),
                dependency,
            )
        fixture_name = (
            hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            + "-"
            + source.name
        )
        relative = Path(".aworld_replay_fixtures") / fixture_name
        destination = workspace_seed / relative
        if not destination.exists():
            self._ensure_workspace_seed_capacity(
                workspace_seed,
                additional_files=1,
                additional_bytes=source.stat().st_size,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        replacement = f"{REPLAY_WORKSPACE_PLACEHOLDER}/{relative.as_posix()}"
        return (
            _replace_in_value(task_input, raw_path, replacement),
            dependency,
        )

    def _bind_dependency(
        self,
        dependency: ReplayDependency,
        *,
        context: ReplayAdapterContext,
        adapters: Sequence[ReplayDependencyAdapter],
    ) -> ReplayAdapterBinding | None:
        for adapter in adapters:
            binding = adapter.bind(dependency, context=context)
            if binding is not None:
                return binding
        return None

    def _snapshot_adapter_fixtures(
        self,
        binding: ReplayAdapterBinding,
        *,
        workspace_seed: Path,
    ) -> ReplayAdapterBinding:
        environment = dict(binding.environment)
        snapshotted_paths: list[str] = []
        adapter_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", binding.adapter_id).strip(
            ".-"
        ) or "adapter"
        for raw_path in binding.fixture_paths:
            source = Path(raw_path).expanduser()
            if (
                not source.is_file()
                or source.is_symlink()
                or _is_sensitive_path(source)
                or source.stat().st_size > self.max_external_file_bytes
            ):
                raise ReplayAdaptationError(
                    "replay adapter fixture must be a bounded non-secret regular file: "
                    f"{binding.adapter_id}"
                )
            content = source.read_bytes()
            fixture_name = hashlib.sha256(content).hexdigest()[:12] + "-" + source.name
            relative = (
                Path(".aworld_replay_adapter_fixtures")
                / adapter_name
                / fixture_name
            )
            destination = workspace_seed / relative
            if not destination.exists():
                self._ensure_workspace_seed_capacity(
                    workspace_seed,
                    additional_files=1,
                    additional_bytes=len(content),
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(source, destination)
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
            fixture_ref = f"{REPLAY_WORKSPACE_PLACEHOLDER}/{relative.as_posix()}"
            snapshotted_paths.append(fixture_ref)
            environment = {
                key: value.replace(str(source), fixture_ref)
                for key, value in environment.items()
            }
        return replace(
            binding,
            environment=environment,
            fixture_paths=tuple(snapshotted_paths),
        )

    def _copy_workspace_seed(
        self,
        source: Path,
        destination: Path,
        *,
        artifact_root: Path,
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        tracked_paths = _git_tracked_workspace_paths(source)
        if tracked_paths is not None:
            self._copy_tracked_workspace_seed(
                source,
                destination,
                artifact_root=artifact_root,
                tracked_paths=tracked_paths,
            )
            self._assert_workspace_seed_limits(destination)
            return

        file_count = 0
        byte_count = 0
        for current_root, directory_names, file_names in os.walk(source):
            current = Path(current_root)
            relative_root = current.relative_to(source)
            target_root = destination / relative_root
            target_root.mkdir(parents=True, exist_ok=True)
            retained_directories: list[str] = []
            for name in directory_names:
                source_path = current / name
                if (
                    name in _IGNORED_DIRECTORY_NAMES
                    or _is_within(source_path, artifact_root)
                    or _is_sensitive_path(source_path)
                ):
                    continue
                try:
                    metadata = source_path.lstat()
                except OSError as exc:
                    raise ReplayAdaptationError(
                        f"cannot inspect replay seed input: {source_path.name}: {exc}"
                    ) from exc
                if stat.S_ISLNK(metadata.st_mode):
                    self._copy_internal_symlink(
                        source_path,
                        source_root=source,
                        destination_root=destination,
                        destination_path=target_root / name,
                        target_is_directory=True,
                    )
                    continue
                retained_directories.append(name)
            directory_names[:] = retained_directories
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
                    self._copy_internal_symlink(
                        source_path,
                        source_root=source,
                        destination_root=destination,
                        destination_path=target_root / file_name,
                        target_is_directory=False,
                    )
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

        self._assert_workspace_seed_limits(destination)

    def _copy_tracked_workspace_seed(
        self,
        source: Path,
        destination: Path,
        *,
        artifact_root: Path,
        tracked_paths: Sequence[Path],
    ) -> None:
        file_count = 0
        byte_count = 0
        for relative in tracked_paths:
            if any(part in _IGNORED_DIRECTORY_NAMES for part in relative.parts):
                continue
            source_path = source / relative
            if (
                _is_within(source_path, artifact_root)
                or _is_sensitive_path(source_path)
                or source_path.name.endswith(_IGNORED_FILE_SUFFIXES)
            ):
                continue
            try:
                metadata = source_path.lstat()
            except FileNotFoundError:
                # A tracked deletion in the current working tree is part of the
                # snapshot state and therefore remains absent from the seed.
                continue
            except OSError as exc:
                raise ReplayAdaptationError(
                    f"cannot inspect replay seed input: {source_path.name}: {exc}"
                ) from exc
            destination_path = destination / relative
            if stat.S_ISLNK(metadata.st_mode):
                self._copy_internal_symlink(
                    source_path,
                    source_root=source,
                    destination_root=destination,
                    destination_path=destination_path,
                    target_is_directory=False,
                )
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            file_count += 1
            byte_count += metadata.st_size
            if file_count > self.max_workspace_files:
                raise ReplayAdaptationError("workspace snapshot file limit exceeded")
            if byte_count > self.max_workspace_bytes:
                raise ReplayAdaptationError("workspace snapshot byte limit exceeded")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)

    def _copy_internal_symlink(
        self,
        source_path: Path,
        *,
        source_root: Path,
        destination_root: Path,
        destination_path: Path,
        target_is_directory: bool,
    ) -> None:
        try:
            resolved = source_path.resolve(strict=True)
        except OSError:
            return
        if not _is_within(resolved, source_root) or _is_sensitive_path(resolved):
            return
        relative_target = resolved.relative_to(source_root.resolve())
        seeded_target = destination_root / relative_target
        rebased_target = os.path.relpath(seeded_target, start=destination_path.parent)
        destination_path.symlink_to(
            rebased_target,
            target_is_directory=target_is_directory,
        )

    def _assert_workspace_seed_limits(self, seed: Path) -> None:
        file_count, byte_count = self._workspace_seed_usage(seed)
        if file_count > self.max_workspace_files:
            raise ReplayAdaptationError("workspace snapshot file limit exceeded")
        if byte_count > self.max_workspace_bytes:
            raise ReplayAdaptationError("workspace snapshot byte limit exceeded")

    def _ensure_workspace_seed_capacity(
        self,
        seed: Path,
        *,
        additional_files: int,
        additional_bytes: int,
    ) -> None:
        file_count, byte_count = self._workspace_seed_usage(seed)
        if file_count + additional_files > self.max_workspace_files:
            raise ReplayAdaptationError("workspace snapshot file limit exceeded")
        if byte_count + additional_bytes > self.max_workspace_bytes:
            raise ReplayAdaptationError("workspace snapshot byte limit exceeded")

    @staticmethod
    def _workspace_seed_usage(seed: Path) -> tuple[int, int]:
        file_count = 0
        byte_count = 0
        for path in seed.rglob("*"):
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise ReplayAdaptationError(
                    f"cannot inspect replay seed output: {path.name}: {exc}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                file_count += 1
                byte_count += metadata.st_size
            elif stat.S_ISREG(metadata.st_mode):
                file_count += 1
                byte_count += metadata.st_size
            else:
                continue
        return file_count, byte_count


def materialize_replay_workspace(
    bundle: ReplayAdaptationBundle,
    destination: str | Path,
) -> Path:
    """Create a clean rollout workspace from a verified adaptation seed."""

    seed = Path(bundle.workspace_seed).expanduser().resolve()
    target = Path(os.path.abspath(str(Path(destination).expanduser())))
    if not seed.is_dir():
        raise ReplayAdaptationError(f"replay workspace seed does not exist: {seed}")
    if any(parent.is_symlink() for parent in target.parents):
        raise ReplayAdaptationError(
            "rollout workspace cannot have a symlinked parent"
        )
    if (
        target == seed
        or _is_within(target, seed)
        or _is_within(seed, target)
    ):
        raise ReplayAdaptationError(
            "rollout workspace and replay seed cannot overlap"
        )
    current_fingerprint = _json_fingerprint(_workspace_manifest(seed))
    if current_fingerprint != bundle.workspace_seed_fingerprint:
        raise ReplayAdaptationError(
            "replay workspace seed changed after adaptation compilation"
        )
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    _clone_or_copy_workspace(seed, target)
    return target


def _clone_or_copy_workspace(seed: Path, target: Path) -> None:
    """Materialize a writable rollout using filesystem copy-on-write when available."""

    clone_command: list[str] | None = None
    if sys.platform == "darwin":
        clone_command = ["cp", "-cR", f"{seed}/.", str(target)]
    elif sys.platform.startswith("linux"):
        clone_command = [
            "cp",
            "--reflink=always",
            "-a",
            f"{seed}/.",
            str(target),
        ]
    if clone_command is not None:
        target.mkdir(parents=True, exist_ok=False)
        try:
            completed = subprocess.run(
                clone_command,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0:
            return
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(seed, target, symlinks=True)


def _normalize_workspace_paths(text: str, *, workspace_root: Path) -> str:
    normalized = text.replace(str(workspace_root), REPLAY_WORKSPACE_PLACEHOLDER)
    repository_name = re.escape(workspace_root.name)
    stale_pattern = (
        rf"/(?:Users|home)/[^/\s]+/Documents/workspace/{repository_name}"
    )
    return re.sub(stale_pattern, REPLAY_WORKSPACE_PLACEHOLDER, normalized)


def _git_tracked_workspace_paths(source: Path) -> tuple[Path, ...] | None:
    """Return current tracked paths for a Git-backed replay seed.

    The current working-tree bytes are copied, so local edits to tracked source are
    preserved. Untracked files are excluded because they cannot be attributed to
    the recorded initial state; explicit external inputs are snapshotted later by
    dependency adaptation.
    """

    try:
        root_result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if root_result.returncode != 0:
        return None
    try:
        git_root = Path(os.fsdecode(root_result.stdout).strip()).resolve()
        source_prefix = source.relative_to(git_root)
    except (OSError, ValueError):
        return None
    try:
        files_result = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "--cached", "--full-name", "-z"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if files_result.returncode != 0:
        return None
    tracked: list[Path] = []
    for raw_path in files_result.stdout.split(b"\0"):
        if not raw_path:
            continue
        root_relative = Path(os.fsdecode(raw_path))
        try:
            source_relative = root_relative.relative_to(source_prefix)
        except ValueError:
            continue
        if source_relative.parts:
            tracked.append(source_relative)
    return tuple(sorted(set(tracked), key=lambda item: item.as_posix()))


def _absolute_local_path_references(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _ABSOLUTE_LOCAL_PATH.finditer(text):
        raw_path = match.group(0)
        prefix = text[max(0, match.start() - 64) : match.start()]
        if "{" in raw_path or "}" in raw_path:
            continue
        if _NON_FILE_PATH_CONTEXT.search(prefix):
            continue
        if raw_path not in paths:
            paths.append(raw_path)
    return tuple(paths)


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


def _is_stateful_tool_name(tool_name: str) -> bool:
    tokens = tuple(
        token
        for token in re.split(r"[._:/-]+", tool_name.lower())
        if token
    )
    if any(token in _STATEFUL_BROWSER_TOOL_TOKENS for token in tokens):
        return True
    return any(
        (token == "computer" and next_token == "use")
        or (token == "web" and next_token in _STATEFUL_WEB_ACTION_TOKENS)
        for token, next_token in zip(tokens, tokens[1:])
    ) or tokens == ("web",)


def _detected_runtime_dependencies(
    case: EvalCase,
    task_input: Any,
) -> tuple[ReplayDependency, ...]:
    task_text = _text_fragments(task_input)
    dependencies: list[ReplayDependency] = []
    if (
        task_input_requires_prior_context(task_input)
        and not _case_has_reconstructed_context(case)
    ):
        dependencies.append(
            ReplayDependency(
                kind="conversation_context",
                identifier="prior-task-context",
                status="context_incomplete",
                deterministic=False,
                detail="required prior task context is absent",
            )
        )
    local_endpoints = tuple(
        dict.fromkeys(
            _normalize_detected_url(value)
            for value in _LOCAL_ENDPOINT.findall(task_text)
        )
    )
    for endpoint in local_endpoints:
        dependencies.append(
            ReplayDependency(
                kind="local_endpoint",
                identifier=endpoint,
                status="runtime_required",
                deterministic=False,
                detail="stateful local endpoint requires a replay capability",
            )
        )
    for url in dict.fromkeys(
        _normalize_detected_url(value)
        for value in _HTTP_RESOURCE.findall(task_text)
    ):
        if url in local_endpoints:
            continue
        dependencies.append(
            ReplayDependency(
                kind="http_resource",
                identifier=url,
                status="runtime_required",
                deterministic=False,
                detail="live HTTP content requires a deterministic replay capability",
            )
        )
    for tool_name in _case_tool_names(case):
        if not _is_stateful_tool_name(tool_name):
            continue
        dependencies.append(
            ReplayDependency(
                kind="stateful_tool",
                identifier=tool_name,
                status="runtime_required",
                deterministic=False,
                detail="stateful trace tool requires a replay capability",
            )
        )
    return tuple(dependencies)


def _case_dependency_input(
    case: EvalCase,
    *,
    normalized_task_input: Any,
    workspace_root: Path,
) -> Any:
    """Use the current task for dependencies; prior turns remain replay evidence."""

    snapshot = case.context_snapshot
    if snapshot is None or not snapshot.prior_turns:
        return normalized_task_input
    transcript = "\n".join(
        f"{turn.role.title()}: {turn.content}"
        for turn in snapshot.prior_turns
    )
    reconstructed_prefix = (
        "Recorded prior task context "
        f"[{snapshot.link_strategy or 'recorded'}]:\n{transcript}\n\n"
        "Current task:\n"
    )
    if isinstance(normalized_task_input, str) and normalized_task_input.startswith(
        reconstructed_prefix
    ):
        return normalized_task_input[len(reconstructed_prefix) :]
    if isinstance(normalized_task_input, Mapping):
        content = normalized_task_input.get("content")
        if isinstance(content, str) and content.startswith(reconstructed_prefix):
            return {
                **dict(normalized_task_input),
                "content": content[len(reconstructed_prefix) :],
            }
    return _normalize_value(
        snapshot.task_input,
        lambda text: _normalize_workspace_paths(
            text,
            workspace_root=workspace_root,
        ),
    )


def _normalize_detected_url(value: str) -> str:
    # URL regexes that stop only at ASCII whitespace can absorb adjacent
    # natural-language prose in scripts that use full-width punctuation. Raw
    # non-ASCII punctuation is not a legal URI delimiter unless percent-
    # encoded, so treat the first such punctuation mark as the evidence URL
    # boundary while preserving Unicode letters in valid IRIs.
    for index, character in enumerate(value):
        if (
            ord(character) > 127
            and unicodedata.category(character).startswith("P")
        ):
            value = value[:index]
            break
    normalized = value.rstrip(".,")
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        while normalized.endswith(closing) and normalized.count(closing) > normalized.count(
            opening
        ):
            normalized = normalized[:-1]
    return normalized


def _case_has_reconstructed_context(case: EvalCase) -> bool:
    snapshot = case.context_snapshot
    return bool(snapshot is not None and snapshot.prior_turns)


def _context_evidence_ref(case: EvalCase) -> str:
    snapshot = case.context_snapshot
    if snapshot is not None:
        return f"context:{case.case_id}:{snapshot.fingerprint}"
    return f"case:{case.case_id}:input"


def _requirement_id(kind: str, identifier: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\0{identifier}".encode("utf-8")
    ).hexdigest()[:20]
    return f"requirement-{digest}"


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


def _environment_snapshot(
    cases: Sequence[ReplayCaseAdaptation],
) -> dict[str, Any]:
    environment_keys = ("LANG", "LC_ALL", "LC_CTYPE", "TZ")
    return {
        "schema_version": "aworld.self_evolve.environment_snapshot.v1",
        "runtime": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "platform": sys.platform,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "environment": {
            key: os.environ[key]
            for key in environment_keys
            if key in os.environ
        },
        "tool_names": sorted(
            {
                tool_name
                for case in cases
                for tool_name in case.tool_names
            }
        ),
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
