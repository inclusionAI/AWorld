from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any


async def ensure_remote_skill_assets_ready(
    sandbox: Any,
    skill_name: str,
    skill_config: dict[str, Any],
) -> str:
    execution_assets = dict(skill_config.get("execution_assets", {}) or {})
    if getattr(sandbox, "mode", "local") != "remote":
        return str(skill_config.get("asset_root", "") or "")

    if not execution_assets.get("enabled"):
        raise RuntimeError(f"Skill '{skill_name}' has no remote execution assets to sync")

    digest = str(execution_assets.get("digest", "") or "").strip()
    relative_paths = [
        str(path).strip()
        for path in execution_assets.get("relative_paths", []) or []
        if str(path).strip()
    ]
    if not digest or not relative_paths:
        raise RuntimeError(
            f"Skill '{skill_name}' execution asset metadata is incomplete for remote sync"
        )

    cache = _get_or_create_root_cache(sandbox)
    cache_key = (skill_name, digest)
    if cache_key in cache:
        return cache[cache_key]

    asset_root = Path(str(skill_config.get("asset_root", "") or "")).resolve()
    if not asset_root.exists():
        raise RuntimeError(f"Skill '{skill_name}' asset root does not exist: {asset_root}")

    remote_base_dir = await _resolve_remote_base_dir(sandbox)
    remote_root = str(Path(remote_base_dir) / ".aworld" / "skills" / skill_name / digest)

    await _require_success(
        await sandbox.file.create_directory(remote_root),
        f"create remote skill root for '{skill_name}'",
    )

    for relative_path in relative_paths:
        source_path = (asset_root / relative_path).resolve()
        try:
            source_path.relative_to(asset_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Skill '{skill_name}' execution asset escapes asset_root: {relative_path}"
            ) from exc
        if not source_path.is_file():
            raise RuntimeError(
                f"Skill '{skill_name}' execution asset file not found: {relative_path}"
            )
        target_path = str(Path(remote_root) / relative_path)
        try:
            content = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if not hasattr(sandbox.file, "upload_file"):
                raise RuntimeError(
                    f"Skill '{skill_name}' execution asset is not UTF-8 text and remote upload is unavailable: "
                    f"{relative_path}"
                )
            await _require_success(
                await sandbox.file.upload_file(str(source_path), target_path),
                f"upload remote execution asset '{relative_path}' for '{skill_name}'",
            )
        else:
            await _require_success(
                await sandbox.file.write_file(target_path, content),
                f"write remote execution asset '{relative_path}' for '{skill_name}'",
            )
        await _preserve_remote_permissions(
            sandbox,
            source_path=source_path,
            target_path=target_path,
            skill_name=skill_name,
            relative_path=relative_path,
        )

    cache[cache_key] = remote_root
    return remote_root


def _get_or_create_root_cache(sandbox: Any) -> dict[tuple[str, str], str]:
    cache = getattr(sandbox, "_remote_skill_execution_roots", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(sandbox, "_remote_skill_execution_roots", cache)
    return cache


async def _resolve_remote_base_dir(sandbox: Any) -> str:
    cached = getattr(sandbox, "_remote_skill_execution_base_dir", None)
    if cached:
        return str(cached)

    result = await sandbox.file.list_allowed_directories()
    await _require_success(result, "list remote allowed directories")
    directories = _parse_allowed_directories(result.get("data"))
    if not directories:
        raise RuntimeError("Remote filesystem did not expose any allowed directories")
    base_dir = directories[0]
    setattr(sandbox, "_remote_skill_execution_base_dir", base_dir)
    return base_dir


def _parse_allowed_directories(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if lines and lines[0].lower().startswith("allowed directories"):
            lines = lines[1:]
        return lines
    return []


async def _require_success(result: dict[str, Any], phase: str) -> None:
    if result and result.get("success", True):
        return
    error = None
    if isinstance(result, dict):
        error = result.get("error") or result.get("data")
    raise RuntimeError(f"Failed to {phase}: {error or 'unknown error'}")


async def _preserve_remote_permissions(
    sandbox: Any,
    *,
    source_path: Path,
    target_path: str,
    skill_name: str,
    relative_path: str,
) -> None:
    permission_bits = source_path.stat().st_mode & 0o777
    if not permission_bits:
        return

    terminal = getattr(sandbox, "terminal", None)
    if terminal is None or not hasattr(terminal, "run_code"):
        raise RuntimeError(
            f"Failed to preserve remote execution asset permissions for '{skill_name}': "
            "sandbox terminal namespace is unavailable"
        )

    quoted_target_path = shlex.quote(target_path)
    result = await terminal.run_code(f"chmod {permission_bits:o} {quoted_target_path}")
    await _require_success(
        result,
        f"preserve permissions for remote execution asset '{relative_path}' in '{skill_name}'",
    )
