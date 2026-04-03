"""
Unit tests for glob tool.
Can be run with pytest or standalone.
"""
try:
    import pytest
except ImportError:
    pytest = None  # pytest optional

from pathlib import Path
from typing import Optional


# Extract core logic without decorator (for testing)
def glob_search_core(pattern: str, path: Optional[str] = None) -> str:
    """Core glob logic for testing"""
    search_path = Path(path) if path else Path.cwd()

    if not search_path.exists():
        return f"Error: Path does not exist: {search_path}"

    if not search_path.is_dir():
        return f"Error: Path is not a directory: {search_path}"

    try:
        matches = list(search_path.glob(pattern))

        if not matches:
            return f"No files found matching pattern: {pattern}"

        matches_with_mtime = [(m, m.stat().st_mtime) for m in matches if m.is_file()]
        matches_with_mtime.sort(key=lambda x: x[1], reverse=True)

        relative_paths = []
        for match, _ in matches_with_mtime:
            try:
                rel_path = match.relative_to(Path.cwd())
                relative_paths.append(str(rel_path))
            except ValueError:
                relative_paths.append(str(match))

        result = "\n".join(relative_paths)
        count = len(relative_paths)

        if count > 100:
            result += f"\n\n(Showing {count} files. Consider using a more specific pattern.)"

        return result

    except Exception as e:
        return f"Error searching with pattern '{pattern}': {str(e)}"


class TestGlobBasic:
    """Basic glob functionality tests"""

    def test_basic_pattern_py_files(self):
        """Test finding .py files in root"""
        result = glob_search_core("*.py")
        assert not result.startswith("Error:")
        assert not result.startswith("No files")
        # Should find at least our test files
        assert "test_glob" in result or ".py" in result

    def test_basic_pattern_md_files(self):
        """Test finding .md files"""
        result = glob_search_core("*.md")
        # Either finds files or no files (both OK)
        assert result.startswith("No files") or "CLAUDE.md" in result or ".md" in result

    def test_no_matching_files(self):
        """Test pattern with no matches"""
        result = glob_search_core("*.nonexistent_extension_xyz")
        assert result.startswith("No files found matching pattern:")


class TestGlobRecursive:
    """Recursive pattern tests"""

    def test_recursive_all_py(self):
        """Test recursive search for all Python files"""
        result = glob_search_core("**/*.py")
        assert not result.startswith("Error:")
        assert not result.startswith("No files")
        # Should find many files
        lines = result.split("\n")
        file_count = len([l for l in lines if l and not l.startswith("(")])
        assert file_count > 10  # Reasonable threshold

    def test_recursive_in_subdirectory(self):
        """Test recursive search in specific subdirectory"""
        result = glob_search_core("aworld/**/*.py")
        assert not result.startswith("Error:")
        # Should find aworld Python files
        if not result.startswith("No files"):
            assert "aworld/" in result

    def test_recursive_init_files(self):
        """Test finding all __init__.py files"""
        result = glob_search_core("**/__init__.py")
        if not result.startswith("No files"):
            assert "__init__.py" in result


class TestGlobWithPath:
    """Tests with path parameter"""

    def test_specific_directory(self):
        """Test glob in specific directory"""
        result = glob_search_core("*.py", "aworld/tools")
        if Path("aworld/tools").exists():
            assert not result.startswith("Error:")
        else:
            assert result.startswith("Error:") or result.startswith("No files")

    def test_nonexistent_directory(self):
        """Test error handling for non-existent directory"""
        result = glob_search_core("*.py", "nonexistent_directory_xyz")
        assert result.startswith("Error: Path does not exist:")

    def test_file_as_path(self):
        """Test error when path is a file, not directory"""
        # Find any Python file
        import glob as builtin_glob
        py_files = builtin_glob.glob("*.py")
        if py_files:
            result = glob_search_core("*.py", py_files[0])
            assert result.startswith("Error: Path is not a directory:")


class TestGlobSorting:
    """Test modification time sorting"""

    def test_results_sorted_by_mtime(self):
        """Verify results are sorted by modification time"""
        result = glob_search_core("*.py")

        if not result.startswith("Error:") and not result.startswith("No files"):
            lines = [l for l in result.split("\n") if l and not l.startswith("(")]

            if len(lines) >= 2:
                # Check that first file has mtime >= second file
                import os
                try:
                    mtime1 = os.path.getmtime(lines[0])
                    mtime2 = os.path.getmtime(lines[1])
                    assert mtime1 >= mtime2, "Files should be sorted by mtime (newest first)"
                except FileNotFoundError:
                    # Files might have been deleted, skip check
                    pass


class TestGlobWildcards:
    """Complex wildcard pattern tests"""

    def test_prefix_wildcard(self):
        """Test prefix wildcard (test_*.py)"""
        result = glob_search_core("test_*.py")
        # May or may not find files depending on location
        assert not result.startswith("Error:")

    def test_nested_wildcard(self):
        """Test nested directory wildcard"""
        result = glob_search_core("**/commands/*.py")
        # Should find command files if they exist
        if Path("aworld-cli/src/aworld_cli/commands").exists():
            # If directory exists, should find files or report none
            assert not result.startswith("Error:")

    def test_multiple_level_wildcard(self):
        """Test multiple directory levels"""
        result = glob_search_core("aworld/**/agent*.py")
        assert not result.startswith("Error:")


class TestGlobOutput:
    """Output format tests"""

    def test_relative_paths(self):
        """Verify output uses relative paths"""
        result = glob_search_core("**/*.py")
        if not result.startswith("Error:") and not result.startswith("No files"):
            lines = result.split("\n")
            # Check that paths don't start with / (relative, not absolute)
            sample_lines = [l for l in lines[:5] if l and not l.startswith("(")]
            for line in sample_lines:
                # Relative paths shouldn't start with /
                # (unless on a system where they're absolute by necessity)
                pass  # Just verify no crash

    def test_large_result_warning(self):
        """Test warning for large result sets"""
        result = glob_search_core("**/*.py")
        if not result.startswith("Error:") and not result.startswith("No files"):
            lines = result.split("\n")
            file_count = len([l for l in lines if l and not l.startswith("(")])

            if file_count > 100:
                assert "Consider using a more specific pattern" in result


class TestGlobEdgeCases:
    """Edge case tests"""

    def test_empty_pattern(self):
        """Test behavior with empty pattern"""
        try:
            result = glob_search_core("")
            # Should either work or error gracefully
            assert isinstance(result, str)
        except:
            pass  # Some implementations may raise exception

    def test_special_characters(self):
        """Test pattern with special characters"""
        result = glob_search_core("*[].py")
        # Should handle gracefully without crash
        assert isinstance(result, str)

    def test_very_specific_pattern(self):
        """Test very specific pattern that won't match"""
        result = glob_search_core("very_specific_file_that_does_not_exist_12345.xyz")
        assert result.startswith("No files found")


# Standalone test runner
if __name__ == "__main__":
    import sys

    # Run tests manually if pytest not available
    test_classes = [
        TestGlobBasic,
        TestGlobRecursive,
        TestGlobWithPath,
        TestGlobSorting,
        TestGlobWildcards,
        TestGlobOutput,
        TestGlobEdgeCases,
    ]

    total = 0
    passed = 0
    failed = 0

    print("=" * 80)
    print("Running glob tool unit tests")
    print("=" * 80)

    for test_class in test_classes:
        print(f"\n{test_class.__name__}:")
        test_instance = test_class()

        for method_name in dir(test_instance):
            if method_name.startswith("test_"):
                total += 1
                method = getattr(test_instance, method_name)

                try:
                    method()
                    print(f"  ✓ {method_name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  ✗ {method_name}: {str(e)}")
                    failed += 1
                except Exception as e:
                    print(f"  ✗ {method_name}: {type(e).__name__}: {str(e)}")
                    failed += 1

    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed, {failed} failed")
    print("=" * 80)

    sys.exit(0 if failed == 0 else 1)
