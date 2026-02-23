"""路径工具函数"""

from pathlib import Path
import os


def normalize_path(p: str) -> str:
    """规范化路径"""
    p = p.strip().strip('"\'')
    
    # 展开 ~
    if p.startswith("~/") or p == "~":
        p = str(Path.home() / p[1:])
    
    path_obj = Path(p)
    try:
        normalized = path_obj.resolve()
    except (OSError, RuntimeError):
        normalized = path_obj.absolute()
    
    return str(normalized)


def is_path_allowed(path: str, allowed_dirs: list[str]) -> bool:
    """检查路径是否在允许的目录内"""
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
    """验证并解析路径"""
    normalized = normalize_path(path)
    
    # 检查是否在允许目录内
    if not is_path_allowed(normalized, allowed_dirs):
        raise ValueError(f"Access denied: {path} not in allowed directories")
    
    # 解析符号链接
    try:
        real_path = Path(normalized).resolve()
        if not is_path_allowed(str(real_path), allowed_dirs):
            raise ValueError(f"Access denied: symlink target outside allowed directories")
        return str(real_path)
    except OSError:
        # 文件不存在，检查父目录
        parent = Path(normalized).parent
        try:
            real_parent = parent.resolve()
            if not is_path_allowed(str(real_parent), allowed_dirs):
                raise ValueError(f"Access denied: parent directory outside allowed directories")
            return normalized
        except OSError:
            raise ValueError(f"Parent directory does not exist: {parent}")


def resolve_and_require_file(path: str) -> str:
    """解析路径并校验存在且为文件（不校验允许目录，用于 upload 的 source_path）"""
    normalized = normalize_path(path)
    try:
        real = Path(normalized).resolve()
    except (OSError, RuntimeError):
        raise ValueError(f"Path does not exist or cannot be resolved: {path}")
    if not real.exists():
        raise ValueError(f"Path does not exist: {path}")
    if not real.is_file():
        raise ValueError(f"Path is not a file: {path}")
    return str(real)

