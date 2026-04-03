"""
Glob tool for fast file pattern matching.
Provides a simpler alternative to search_content when only filenames matter.
"""
from pathlib import Path
from typing import Optional
from pydantic import Field

from aworld.core.tool.func_to_tool import be_tool


@be_tool(
    tool_name='glob',
    tool_desc='Find files matching a glob pattern (e.g., *.py, src/**/*.ts). '
              'Faster than search_content when you only need filenames, not content.'
)
def glob_search(
    pattern: str = Field(description="Glob pattern to match files against (e.g., '*.py', 'src/**/*.ts', 'test_*.py')"),
    path: Optional[str] = Field(None, description="Directory to search in. Defaults to current working directory if not specified.")
) -> str:
    """
    Find files matching a glob pattern.

    Returns a newline-separated list of relative file paths that match the pattern.
    Results are sorted by modification time (most recent first).

    Examples:
    - glob("*.py") → All Python files in current directory
    - glob("src/**/*.ts") → All TypeScript files in src/ recursively
    - glob("test_*.py", "tests/") → Test files in tests/ directory
    """
    import os
    from pathlib import Path

    # Determine search path
    search_path = Path(path) if path else Path.cwd()

    if not search_path.exists():
        return f"Error: Path does not exist: {search_path}"

    if not search_path.is_dir():
        return f"Error: Path is not a directory: {search_path}"

    try:
        # Use glob to find matches
        matches = list(search_path.glob(pattern))

        if not matches:
            return f"No files found matching pattern: {pattern}"

        # Sort by modification time (most recent first)
        matches_with_mtime = [(m, m.stat().st_mtime) for m in matches if m.is_file()]
        matches_with_mtime.sort(key=lambda x: x[1], reverse=True)

        # Convert to relative paths
        relative_paths = []
        for match, _ in matches_with_mtime:
            try:
                rel_path = match.relative_to(Path.cwd())
                relative_paths.append(str(rel_path))
            except ValueError:
                # If not relative to cwd, use absolute path
                relative_paths.append(str(match))

        # Format output
        result = "\n".join(relative_paths)
        count = len(relative_paths)

        if count > 100:
            result += f"\n\n(Showing {count} files. Consider using a more specific pattern.)"

        return result

    except Exception as e:
        return f"Error searching with pattern '{pattern}': {str(e)}"
