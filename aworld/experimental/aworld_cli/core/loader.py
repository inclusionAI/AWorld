"""
Agent loader for scanning and loading agents from local directories.
"""
import os
import sys
import importlib.util
from pathlib import Path
from typing import Union, List, Optional


def _has_agent_decorator(file_path: Path) -> bool:
    """Check if a Python file contains @agent decorator.
    
    Args:
        file_path: Path to the Python file
        
    Returns:
        True if file contains @agent decorator, False otherwise
        
    Example:
        >>> if _has_agent_decorator(Path("my_agent.py")):
        ...     print("File contains @agent decorator")
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Check for @agent decorator (with or without parentheses)
            return '@agent' in content or '@agent(' in content
    except Exception as e:
        # If we can't read the file, assume it doesn't have the decorator
        return False


def init_agents(agents_dir: Union[str, Path] = None) -> None:
    """Initialize and register all agents decorated with @agent.
    
    This function automatically discovers and imports all Python files
    in the agents directory structure, which will trigger the @agent
    decorator to register agents.
    
    Args:
        agents_dir: Path to the agents directory (can be str or Path object)
    
    Example:
        >>> from aworld_cli.core.loader import init_agents
        >>> init_agents("./agents")
    """
    if not agents_dir:
        # Default to current working directory if not specified
        agents_dir = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or os.getcwd()

    from .registry import LocalAgentRegistry

    # Convert to Path object if it's a string
    agents_dir = Path(agents_dir) if isinstance(agents_dir, str) else agents_dir
    
    if not agents_dir.exists():
        print(f"⚠️ Agents directory not found: {agents_dir}")
        return
    
    # Find all Python files recursively, excluding __init__.py and private modules
    all_python_files = [
        f for f in agents_dir.rglob("*.py")
        if f.name != "__init__.py" and not f.name.startswith("_")
    ]
    
    if not all_python_files:
        print("ℹ️ No Python files found in agents directory")
        return
    
    # Filter files that contain @agent decorator
    python_files = [f for f in all_python_files if _has_agent_decorator(f)]
    
    print(f"🔍 Found {len(all_python_files)} Python file(s), {len(python_files)} with @agent decorator")
    
    if not python_files:
        print("ℹ️ No Python files with @agent decorator found")
        return
    
    # Import each Python module to trigger decorator registration
    loaded_count = 0
    failed_count = 0
    failed_files = []  # Track failed files with error messages
    
    # Try to find the project root by checking sys.path
    # Usually the project root is in sys.path
    project_root = None
    agents_dir_abs = agents_dir.resolve()
    
    # Try to find the project root
    for path in sys.path:
        try:
            path_obj = Path(path).resolve()
            if agents_dir_abs.is_relative_to(path_obj):
                project_root = path_obj
                break
        except Exception:
            continue
    
    # If project root not found, use agents_dir's parent as fallback
    if project_root is None:
        project_root = agents_dir_abs.parent
    
    # Add project root to sys.path if not already there
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
    
    for py_file in python_files:
        try:
            # Calculate relative path from project root for module name
            try:
                rel_path = py_file.relative_to(project_root)
            except ValueError:
                # If file is not relative to project root, use absolute path
                rel_path = py_file
            
            # Convert path to module name (e.g., agents/my_agent.py -> agents.my_agent)
            module_parts = list(rel_path.parts[:-1]) + [rel_path.stem]
            module_name = '.'.join(module_parts)
            
            # Skip if module name starts with a number (invalid Python module name)
            if module_name and module_name[0].isdigit():
                print(f"⚠️ Skipping invalid module name: {module_name}")
                continue
            
            # Use importlib to load the module
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                print(f"⚠️ Could not create spec for {py_file}")
                failed_count += 1
                failed_files.append((str(py_file), "Could not create module spec"))
                continue
            
            module = importlib.util.module_from_spec(spec)
            
            # Execute the module to trigger decorator registration
            try:
                spec.loader.exec_module(module)
                loaded_count += 1
                print(f"✅ Loaded agent from: {py_file.name}")
            except Exception as import_error:
                failed_count += 1
                error_msg = str(import_error)
                failed_files.append((str(py_file), error_msg))
                print(f"❌ Failed to load {py_file.name}: {error_msg}")
                continue
                
        except Exception as e:
            failed_count += 1
            error_msg = str(e)
            failed_files.append((str(py_file), error_msg))
            print(f"❌ Error processing {py_file}: {error_msg}")
            continue
    
    # Summary
    total_registered = len(LocalAgentRegistry.list_agents())
    print(f"\n📊 Summary: Loaded {loaded_count} file(s), {failed_count} failed, {total_registered} agent(s) registered")
    
    if failed_files:
        print("\n⚠️ Failed files:")
        for file_path, error in failed_files:
            print(f"  - {file_path}: {error}")


__all__ = ["init_agents", "_has_agent_decorator"]

