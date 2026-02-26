"""
Agent loader for scanning and loading agents from local directories.
"""
import importlib.util
import os
import sys
from pathlib import Path
from typing import Union, Optional
from aworld.logs.util import logger


def _ensure_parent_packages_in_sys_modules(module_name: str, project_root: Union[str, Path]) -> None:
    """
    Register parent packages in sys.modules so relative imports (e.g. from ..mcp_tools) resolve correctly.

    When loading skill_agent.agents.swarm via importlib without going through the normal importer,
    parent packages (skill_agent, skill_agent.agents) may not be in sys.modules, so "from ..mcp_tools"
    can be resolved wrongly as skill_agent.agents.mcp_tools. This adds placeholder package modules.

    Args:
        module_name: Full module name, e.g. "skill_agent.agents.swarm".
        project_root: Directory that contains the top-level package (e.g. .../examples).

    Example:
        >>> _ensure_parent_packages_in_sys_modules("skill_agent.agents.swarm", Path("/path/to/examples"))
    """
    root = Path(project_root).resolve()
    parts = module_name.split(".")
    # Only register the top-level package (e.g. skill_agent) so "from ..mcp_tools" resolves to skill_agent.mcp_tools
    if len(parts) < 2:
        return
    top_level = parts[0]
    pkg_path = root / top_level
    if top_level in sys.modules:
        logger.info(f"📦 _ensure_parent_packages: skip (already in sys.modules): {top_level}")
        return
    if not pkg_path.is_dir():
        logger.info(f"📦 _ensure_parent_packages: skip (not a dir): {top_level} path={pkg_path}")
        return
    pkg = type(sys)(top_level)
    pkg.__path__ = [str(pkg_path)]
    pkg.__package__ = top_level
    sys.modules[top_level] = pkg
    logger.info(f"📦 _ensure_parent_packages: registered top-level {top_level} -> {pkg_path}")


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


def init_agents(agents_dir: Union[str, Path] = None, load_markdown_agents: bool = False) -> None:
    """Initialize and register all agents decorated with @agent and optionally from markdown files.
    
    This function automatically discovers and imports:
    1. Python files with @agent decorator
    2. Markdown files with YAML front matter containing agent definitions (optional, disabled by default)
    
    Args:
        agents_dir: Path to the agents directory (can be str or Path object)
        load_markdown_agents: If True, load markdown agents. Defaults to False.
    
    Example:
        >>> from aworld_cli.core.loader import init_agents
        >>> init_agents("./agents")
        >>> # To enable markdown agent loading:
        >>> init_agents("./agents", load_markdown_agents=True)
    """
    from .agent_registry import LocalAgentRegistry
    from .._globals import console

    if not agents_dir:
        # Do not default to current directory; require explicit path or env
        agents_dir = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR")
        if not agents_dir:
            logger.warn("[yellow]⚠️ Agents directory not set. Set LOCAL_AGENTS_DIR or AGENTS_DIR, or pass agents_dir explicitly. Current directory is not scanned.[/yellow]")
            return []

    # Convert to Path object if it's a string
    agents_dir = Path(agents_dir) if isinstance(agents_dir, str) else agents_dir
    agents_dir_resolved = agents_dir.resolve()
    cwd_resolved = Path.cwd().resolve()

    if not agents_dir.exists():
        logger.warning(f"Agents directory not found: {agents_dir}")
        return []

    # Do not scan the current working directory (avoid loading from cwd by mistake)
    if agents_dir_resolved == cwd_resolved:
        console.print("[yellow]⚠️ Refusing to scan current directory. Use an explicit agents path or set LOCAL_AGENTS_DIR/AGENTS_DIR.[/yellow]")
        return []
    
    # Load markdown agents only if explicitly enabled
    markdown_loaded_count = 0
    markdown_failed_count = 0
    markdown_agents = []
    
    if load_markdown_agents:
        from .markdown_agent_loader import load_markdown_agents as load_md_agents
        from rich.status import Status
        with Status(f"[dim]📂 Loading agents from: {agents_dir}[/dim]", console=console):
            markdown_agents = load_md_agents(agents_dir)
        
        for agent in markdown_agents:
            try:
                LocalAgentRegistry.register(agent)
                markdown_loaded_count += 1
                # Get file path from metadata if available
                file_path = agent.metadata.get("file_path", "unknown") if agent.metadata else "unknown"
                logger.info(f"[dim]✅ Loaded markdown agent: {agent.name} from {file_path}[/dim]")
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
        logger.info(f"[dim]🔍 Found {len(all_python_files)} Python file(s), {len(python_files)} with @agent decorator[/dim]")
        if python_files:
            logger.info(f"[dim]  Files with @agent decorator:[/dim]")
            for py_file in python_files:
                logger.info(f"[dim]    • {py_file.relative_to(agents_dir) if agents_dir.exists() else py_file}[/dim]")
    elif markdown_agents:
        console.print(f"[dim]🔍 Found {len(markdown_agents)} markdown agent file(s)[/dim]")
    
    if not python_files and not markdown_agents:
        console.print("[yellow]ℹ️ No agents found (no Python files with @agent decorator or markdown files)[/yellow]")
        return []
    
    # Import each Python module to trigger decorator registration
    loaded_count = 0
    failed_count = 0
    failed_files = []  # Track failed files with error messages
    
    # Resolve project root so that relative imports like "from ..mcp_tools.mcp_config" work.
    # Default: use "local project folder" - the parent of agents_dir is the project (e.g. skill_agent),
    # so sys.path needs the parent of that (e.g. examples/) and module name = skill_agent.agents.swarm.
    agents_dir_abs = agents_dir.resolve()
    project_folder = agents_dir_abs.parent  # e.g. .../skill_agent
    project_root = None
    use_local_package_layout = False

    # Prefer local package layout: agents_dir is inside a project (e.g. skill_agent/agents)
    # so "from ..mcp_tools.xxx" works: sys.path has parent of project folder, module = project.agents.stem
    parent_of_project = project_folder.parent
    try:
        if (
            parent_of_project
            and str(parent_of_project.resolve()) != str(project_folder.resolve())
            and str(parent_of_project.resolve()) != str(agents_dir_abs.resolve())
        ):
            project_root = parent_of_project
            use_local_package_layout = True
            logger.info(f"📂 init_agents: use_local_package_layout=True, project_root( parent_of_project)={parent_of_project}, project_folder={project_folder}")
    except Exception as e:
        logger.info(f"📂 init_agents: local layout check failed: {e}")

    # Fallback: find project root from sys.path or use agents_dir's parent
    if project_root is None:
        for path in sys.path:
            try:
                path_obj = Path(path).resolve()
                if agents_dir_abs.is_relative_to(path_obj):
                    project_root = path_obj
                    logger.info(f"📂 init_agents: project_root from sys.path: {project_root}, use_local_package_layout=False")
                    break
            except Exception:
                continue
    if project_root is None:
        project_root = agents_dir_abs.parent
        use_local_package_layout = False
        logger.info(f"📂 init_agents: project_root=agents_dir.parent: {project_root}, use_local_package_layout=False")

    if use_local_package_layout:
        logger.info(f"📂 init_agents: use_local_package_layout=True, agents_dir_abs={agents_dir_abs}, project_folder={project_folder}, project_root={project_root}")

    # Add project root to sys.path if not already there (default: local folder as base for imports)
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
    logger.info(f"📂 init_agents: sys.path[0]={sys.path[0]}")

    # Import modules with status indicator
    from rich.status import Status
    with Status(f"[dim]📦 Loading {len(python_files)} agent module(s)...[/dim]", console=console):
        for py_file in python_files:
            try:
                # Module name: project_folder-relative path so we get skill_agent.agents.swarm (not skill_agent.agents.agents.swarm)
                if use_local_package_layout:
                    try:
                        rel_to_project = py_file.relative_to(project_folder)
                    except ValueError:
                        rel_to_project = py_file
                    module_parts = list(rel_to_project.parts[:-1]) + [rel_to_project.stem]
                    module_name = project_folder.name + '.' + '.'.join(module_parts)
                else:
                    try:
                        rel_path = py_file.relative_to(project_root)
                    except ValueError:
                        rel_path = py_file
                    module_parts = list(rel_path.parts[:-1]) + [rel_path.stem]
                    module_name = '.'.join(module_parts)

                # Skip if module name starts with a number (invalid Python module name)
                if module_name and module_name[0].isdigit():
                    console.print(f"[dim]⚠️ Skipping invalid module name: {module_name}[/dim]")
                    continue

                logger.info(f"📦 Loading module: py_file={py_file.name}, module_name={module_name}, use_local_package_layout={use_local_package_layout}")

                # Use importlib to load the module
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    file_path = str(py_file.resolve())
                    logger.info(f"[dim]⚠️ Could not create spec for {file_path}[/dim]")
                    failed_count += 1
                    failed_files.append((str(py_file), "Could not create module spec"))
                    continue

                module = importlib.util.module_from_spec(spec)
                # So that "from ..mcp_tools" resolves to skill_agent.mcp_tools, register parent packages
                if use_local_package_layout and "." in module_name:
                    _ensure_parent_packages_in_sys_modules(module_name, project_root)
                    logger.info(f"📦 Before exec_module: sys.modules keys (skill_agent*): {[k for k in sys.modules if k.startswith('skill_agent')]}")
                else:
                    logger.info(f"📦 Skip _ensure_parent_packages: use_local_package_layout={use_local_package_layout}, dots_in_name={'.' in module_name}")

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
                        logger.info(f"[dim]✅ Loaded {agents_registered} agent(s) from: {file_path}[/dim]")
                        for agent_name in agent_names:
                            logger.info(f"[dim]    • Registered agent: {agent_name}[/dim]")
                    else:
                        logger.info(f"[dim]✅ Loaded module (no new agents registered): {file_path}[/dim]")
                except Exception as import_error:
                    failed_count += 1
                    error_msg = str(import_error)
                    failed_files.append((str(py_file), error_msg))
                    file_path = str(py_file.resolve())
                    logger.info(f"[dim]❌ Failed to load {file_path}: {error_msg}[/dim]")
                    logger.info(f"  (module_name={module_name}, use_local_package_layout={use_local_package_layout}, project_root={project_root_str}, sys.path[0]={sys.path[0] if sys.path else 'N/A'})")
                    continue

            except Exception as e:
                failed_count += 1
                error_msg = str(e)
                failed_files.append((str(py_file), error_msg))
                file_path = str(py_file.resolve())
                logger.info(f"[dim]❌ Error processing {file_path}: {error_msg}[/dim]")
                continue
    
    # Summary
    total_registered = len(LocalAgentRegistry.list_agents())
    total_loaded = loaded_count + markdown_loaded_count
    total_failed = failed_count + markdown_failed_count
    logger.info(f"[dim]📊 Summary: Loaded {total_loaded} file(s) ({loaded_count} Python, {markdown_loaded_count} markdown), {total_failed} failed, {total_registered} agent(s) registered[/dim]")

    if failed_files:
        logger.info("Failed files: %s", [(fp, err) for fp, err in failed_files])

    # Return loaded Python files for debugging
    return python_files


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
                logger.info(f"[dim]✅ Loaded markdown agent: {agent.name} from {file_path}[/dim]")
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
            return None

        agent_file_abs = agent_file.resolve()
        # Support "from ..mcp_tools.xxx": use project_folder.parent on sys.path and load as project.agents.stem
        project_folder = agent_file_abs.parent.parent  # e.g. .../skill_agent when file is .../skill_agent/agents/swarm.py
        parent_of_project = project_folder.parent if project_folder else None
        try:
            use_local_package_layout = (
                parent_of_project is not None
                and str(parent_of_project) != str(project_folder)
                and parent_of_project.resolve() != project_folder.resolve()
            )
        except Exception:
            use_local_package_layout = False
        try:
            if use_local_package_layout:
                project_root_str = str(parent_of_project)
                module_name = f"{project_folder.name}.{agent_file_abs.parent.name}.{agent_file.stem}"
            else:
                project_root_str = str(agent_file_abs.parent)
                module_name = agent_file.stem

            if project_root_str not in sys.path:
                sys.path.insert(0, project_root_str)

            # Use importlib to load the module
            spec = importlib.util.spec_from_file_location(module_name, agent_file)
            if spec is None or spec.loader is None:
                console.print(f"[yellow]⚠️ Could not create spec for {agent_file}[/yellow]")
                return None

            module = importlib.util.module_from_spec(spec)
            # So that "from ..mcp_tools" resolves to skill_agent.mcp_tools, register parent packages
            if use_local_package_layout and "." in module_name:
                _ensure_parent_packages_in_sys_modules(module_name, project_root_str)

            # Execute the module to trigger decorator registration
            # Note: We don't use Status here because the module execution might create its own Status
            spec.loader.exec_module(module)
            file_path = str(agent_file.resolve())
            logger.info(f"[dim]✅ Loaded agent from: {file_path}[/dim]")
            
            # Try to get the agent name from registry (get the most recently registered agent)
            # This works because the decorator registers the agent when the module is executed
            agents = LocalAgentRegistry.list_agents()
            if agents:
                # Return the last registered agent name (most likely from this file)
                return agents[-1].name
            return None
        except Exception as e:
            file_path = str(agent_file.resolve())
            logger.info(f"[red]❌ Failed to load Python agent from {file_path}: {e}[/red]")
            return None
    else:
        console.print(f"[yellow]⚠️ Unsupported file type: {agent_file.suffix}. Only .py and .md files are supported.[/yellow]")
        return None


__all__ = ["init_agents", "init_agent_file", "_has_agent_decorator"]

