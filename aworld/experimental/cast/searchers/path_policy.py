"""Path authority helpers shared by CAST search implementations."""

from pathlib import Path
from typing import Union


def canonical_root(root_path: Union[str, Path]) -> Path:
    """Return an existing, canonical directory used as the search authority."""
    root = Path(root_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Search root is not a directory: {root}")
    return root


def resolve_within_root(
    root_path: Union[str, Path],
    requested_path: Union[str, Path],
    *,
    require_exists: bool = True,
) -> Path:
    """Resolve *requested_path* and reject lexical or symlink escapes."""
    root = canonical_root(root_path)
    requested = Path(requested_path).expanduser()
    candidate = requested if requested.is_absolute() else root / requested
    resolved = candidate.resolve(strict=require_exists)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            f"Path is outside the configured search root: {requested_path}"
        ) from exc
    return resolved
