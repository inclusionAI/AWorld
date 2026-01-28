"""
Agent loader for scanning and loading agents from local directories.
"""
import os
import sys
import importlib.util
import traceback
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
    """Initialize and register all agents decorated with @agent and from markdown files.
    
    This function automatically discovers and imports:
    1. Python files with @agent decorator
    2. Markdown files with YAML front matter containing agent definitions
    
    Args:
        agents_dir: Path to the agents directory (can be str or Path object)
    
    Example:
        >>> from aworld_cli.core.loader import init_agents
        >>> init_agents("./agents")
    """
    if not agents_dir:
        # Default to current working directory if not specified
        agents_dir = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or os.getcwd()

    from .agent_registry import LocalAgentRegistry
    from .markdown_agent_loader import load_markdown_agents
    from .._globals import console

    # Convert to Path object if it's a string
    agents_dir = Path(agents_dir) if isinstance(agents_dir, str) else agents_dir
    
    if not agents_dir.exists():
        console.print(f"[yellow]⚠️ Agents directory not found: {agents_dir}[/yellow]")
        return
    
    # Load markdown agents first
    from rich.status import Status
    with Status(f"[dim]📂 Loading agents from: {agents_dir}[/dim]", console=console):
        markdown_agents = load_markdown_agents(agents_dir)
    markdown_loaded_count = 0
    markdown_failed_count = 0
    
    for agent in markdown_agents:
        try:
            LocalAgentRegistry.register(agent)
            markdown_loaded_count += 1
            # Get file path from metadata if available
            file_path = agent.metadata.get("file_path", "unknown") if agent.metadata else "unknown"
            console.print(f"[dim]✅ Loaded markdown agent: {agent.name} from {file_path}[/dim]")
        except Exception as e:
            markdown_failed_count += 1
            console.print(f"[dim]❌ Failed to register markdown agent {agent.name}: {e}[/dim]")
    
    # Find all Python files recursively, excluding __init__.py, private modules, and plugin_manager
    all_python_files = [
        f for f in agents_dir.rglob("*.py")
        if f.name != "__init__.py" 
        and not f.name.startswith("_")
        and "plugin_manager" not in str(f.relative_to(agents_dir))
        and f.name != "plugin_manager.py"
    ]
    
    # Filter files that contain @agent decorator
    python_files = [f for f in all_python_files if _has_agent_decorator(f)] if all_python_files else []
    
    if all_python_files:
        console.print(f"[dim]🔍 Found {len(all_python_files)} Python file(s), {len(python_files)} with @agent decorator[/dim]")
        if python_files:
            console.print(f"[dim]  Files with @agent decorator:[/dim]")
            for py_file in python_files:
                console.print(f"[dim]    • {py_file.relative_to(agents_dir) if agents_dir.exists() else py_file}[/dim]")
    elif markdown_agents:
        console.print(f"[dim]🔍 Found {len(markdown_agents)} markdown agent file(s)[/dim]")
    
    if not python_files and not markdown_agents:
        console.print("[yellow]ℹ️ No agents found (no Python files with @agent decorator or markdown files)[/yellow]")
        return []
    
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
    
    # Import modules with status indicator
    with Status(f"[dim]📦 Loading {len(python_files)} agent module(s)...[/dim]", console=console):
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
                    console.print(f"[dim]⚠️ Skipping invalid module name: {module_name}[/dim]")
                    continue

                # Use importlib to load the module
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    file_path = str(py_file.resolve())
                    console.print(f"[dim]⚠️ Could not create spec for {file_path}[/dim]")
                    failed_count += 1
                    failed_files.append((str(py_file), "Could not create module spec"))
                    continue

                module = importlib.util.module_from_spec(spec)

                # Execute the module to trigger decorator registration
                # Note: We don't use Status here because the module execution might create its own Status
                # which would conflict with "Only one live display may be active at once"
                try:
                    # Get agent count before loading
                    agents_before = len(LocalAgentRegistry.list_agents())
                    spec.loader.exec_module(module)
                    loaded_count += 1
                    # Get agent count after loading
                    agents_after = len(LocalAgentRegistry.list_agents())
                    agents_registered = agents_after - agents_before
                    file_path = str(py_file.resolve())
                    if agents_registered > 0:
                        # Get the names of newly registered agents
                        all_agents = LocalAgentRegistry.list_agents()
                        new_agents = all_agents[-agents_registered:] if agents_registered > 0 else []
                        agent_names = [a.name for a in new_agents]
                        console.print(f"[dim]✅ Loaded {agents_registered} agent(s) from: {file_path}[/dim]")
                        for agent_name in agent_names:
                            console.print(f"[dim]    • Registered agent: {agent_name}[/dim]")
                    else:
                        console.print(f"[dim]✅ Loaded module (no new agents registered): {file_path}[/dim]")
                except Exception as import_error:
                    failed_count += 1
                    error_msg = str(import_error)
                    failed_files.append((str(py_file), error_msg))
                    file_path = str(py_file.resolve())
                    console.print(f"[dim]❌ Failed to load {file_path}: {error_msg}[/dim]")
                    continue

            except Exception as e:
                failed_count += 1
                error_msg = str(e)
                failed_files.append((str(py_file), error_msg))
                file_path = str(py_file.resolve())
                console.print(f"[dim]❌ Error processing {file_path}: {error_msg}[/dim]")
                continue
    
    # Summary
    total_registered = len(LocalAgentRegistry.list_agents())
    total_loaded = loaded_count + markdown_loaded_count
    total_failed = failed_count + markdown_failed_count
    console.print(f"[dim]📊 Summary: Loaded {total_loaded} file(s) ({loaded_count} Python, {markdown_loaded_count} markdown), {total_failed} failed, {total_registered} agent(s) registered[/dim]")

    # Return loaded Python files for debugging
    return python_files
    
    if failed_files:
        console.print("\n[yellow]⚠️ Failed files:[/yellow]")
        for file_path, error in failed_files:
            console.print(f"[dim]  - {file_path}: {error}[/dim]")


def init_agent_file(agent_file: Union[str, Path]) -> Optional[str]:
    """
    Initialize and register a single agent file (Python or Markdown).
    
    Args:
        agent_file: Path to the agent file (Python .py or Markdown .md)
    
    Returns:
        Agent name if successfully loaded, None otherwise
    
    Example:
        >>> from aworld_cli.core.loader import init_agent_file
        >>> agent_name = init_agent_file("./agents/my_agent.py")
        >>> if agent_name:
        ...     print(f"Loaded agent: {agent_name}")
    """
    from .agent_registry import LocalAgentRegistry
    from .markdown_agent_loader import parse_markdown_agent
    
    # Convert to Path object if it's a string
    agent_file = Path(agent_file) if isinstance(agent_file, str) else agent_file
    
    from .._globals import console

    if not agent_file.exists():
        console.print(f"[yellow]⚠️ Agent file not found: {agent_file}[/yellow]")
        return None
    
    console.print("[dim]📂 Loading agent file...[/dim]")

    if agent_file.suffix == '.md':
        # Load markdown agent
        try:
            agent = parse_markdown_agent(agent_file)
            if agent:
                LocalAgentRegistry.register(agent)
                file_path = str(agent_file.resolve())
                console.print(f"[dim]✅ Loaded markdown agent: {agent.name} from {file_path}[/dim]")
                return agent.name
            else:
                console.print(f"[yellow]⚠️ Failed to parse markdown agent from: {agent_file}[/yellow]")
                return None
        except Exception as e:
            console.print(f"[red]❌ Failed to load markdown agent from {agent_file}: {e}[/red]")
            return None
    elif agent_file.suffix == '.py':
        # Load Python agent
        if not _has_agent_decorator(agent_file):
            console.print(f"[yellow]⚠️ Python file {agent_file} does not contain @agent decorator, skipping[/yellow]")
            return
        
        try:
            # Find project root (parent directory containing the file)
            project_root = agent_file.parent
            project_root_str = str(project_root.absolute())
            
            # Add project root to sys.path if not already there
            if project_root_str not in sys.path:
                sys.path.insert(0, project_root_str)
            
            # Calculate module name
            module_name = agent_file.stem
            
            # Use importlib to load the module
            spec = importlib.util.spec_from_file_location(module_name, agent_file)
            if spec is None or spec.loader is None:
                console.print(f"[yellow]⚠️ Could not create spec for {agent_file}[/yellow]")
                return
            
            module = importlib.util.module_from_spec(spec)
            
            # Execute the module to trigger decorator registration
            # Note: We don't use Status here because the module execution might create its own Status
            spec.loader.exec_module(module)
            file_path = str(agent_file.resolve())
            console.print(f"[dim]✅ Loaded agent from: {file_path}[/dim]")
            
            # Try to get the agent name from registry (get the most recently registered agent)
            # This works because the decorator registers the agent when the module is executed
            agents = LocalAgentRegistry.list_agents()
            if agents:
                # Return the last registered agent name (most likely from this file)
                return agents[-1].name
            return None
        except Exception as e:
            file_path = str(agent_file.resolve())
            console.print(f"[red]❌ Failed to load Python agent from {file_path}: {e}[/red]")
            return None
    else:
        console.print(f"[yellow]⚠️ Unsupported file type: {agent_file.suffix}. Only .py and .md files are supported.[/yellow]")
        return None


__all__ = ["init_agents", "init_agent_file", "_has_agent_decorator"]

