# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Path utility functions."""

from pathlib import Path


def normalize_path(p: str) -> str:
    """Normalize path."""
    p = p.strip().strip('"\'')
    
    if p.startswith("~/") or p == "~":
        p = str(Path.home() / p[1:])
    
    path_obj = Path(p)
    try:
        normalized = path_obj.resolve()
    except (OSError, RuntimeError):
        normalized = path_obj.absolute()
    
    return str(normalized)


def is_path_allowed(path: str, allowed_dirs: list[str]) -> bool:
    """Check if path is in allowed directories."""
    if not path or not allowed_dirs:
        return False
    
    try:
        normalized_path = Path(path).resolve()
        if not normalized_path.is_absolute():
            return False
        
        for allowed_dir in allowed_dirs:
            if not allowed_dir:
                continue
            try:
                normalized_dir = Path(allowed_dir).resolve()
                if normalized_path == normalized_dir:
                    return True
                try:
                    normalized_path.relative_to(normalized_dir)
                    return True
                except ValueError:
                    continue
            except (OSError, RuntimeError):
                continue
    except (OSError, RuntimeError):
        return False
    
    return False


async def validate_path(path: str, allowed_dirs: list[str]) -> str:
    """Validate and resolve path."""
    normalized = normalize_path(path)
    
    if not is_path_allowed(normalized, allowed_dirs):
        raise ValueError(f"Access denied: {path} not in allowed directories")
    
    try:
        real_path = Path(normalized).resolve()
        if not is_path_allowed(str(real_path), allowed_dirs):
            raise ValueError(f"Access denied: symlink target outside allowed directories")
        return str(real_path)
    except OSError:
        parent = Path(normalized).parent
        try:
            real_parent = parent.resolve()
            if not is_path_allowed(str(real_parent), allowed_dirs):
                raise ValueError(f"Access denied: parent directory outside allowed directories")
            return normalized
        except OSError:
            raise ValueError(f"Parent directory does not exist: {parent}")

