"""
Plugin manager for AWorld CLI.
Handles plugin installation, removal, and listing.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from aworld.logs.util import logger

# Default plugin installation directory
DEFAULT_PLUGIN_DIR = Path.home() / ".aworld" / "plugins"
PLUGIN_MANIFEST_FILE = DEFAULT_PLUGIN_DIR / ".manifest.json"


class PluginManager:
    """
    Manager for AWorld CLI plugins.
    
    Plugins are installed to ~/.aworld/plugins/ and contain:
    - agents/ directory: Agent definitions
    - skills/ directory: Skill definitions (optional)
    
    Example:
        >>> manager = PluginManager()
        >>> manager.install("my-plugin", url="https://github.com/user/plugin-repo")
        >>> plugins = manager.list()
        >>> manager.remove("my-plugin")
    """
    
    def __init__(self, plugin_dir: Optional[Path] = None):
        """
        Initialize plugin manager.
        
        Args:
            plugin_dir: Plugin installation directory, defaults to ~/.aworld/plugins
        """
        self.plugin_dir = plugin_dir or DEFAULT_PLUGIN_DIR
        self.manifest_file = self.plugin_dir / ".manifest.json"
        
        # Ensure plugin directory exists
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        
        # Load manifest
        self._manifest = self._load_manifest()
    
    def _load_manifest(self) -> Dict[str, Dict]:
        """
        Load plugin manifest file.
        
        Returns:
            Dictionary mapping plugin names to their metadata
        """
        if not self.manifest_file.exists():
            return {}
        
        try:
            with open(self.manifest_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"⚠️ Failed to load plugin manifest: {e}, creating new one")
            return {}
    
    def _save_manifest(self) -> None:
        """Save plugin manifest to file."""
        try:
            with open(self.manifest_file, 'w', encoding='utf-8') as f:
                json.dump(self._manifest, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"❌ Failed to save plugin manifest: {e}")
            raise RuntimeError(f"Failed to save plugin manifest: {e}")
    
    def _parse_github_url(self, url: str) -> Optional[Dict[str, str]]:
        """
        Parse GitHub URL to extract owner, repo, branch, and subdirectory.
        
        Supports multiple formats:
        - https://github.com/owner/repo
        - https://github.com/owner/repo/tree/branch
        - https://github.com/owner/repo/tree/branch/subdirectory
        - git@github.com:owner/repo.git
        
        Args:
            url: GitHub repository URL
            
        Returns:
            Dict with 'owner', 'repo', 'branch' (default: 'main'), and 'subdirectory' (optional) keys,
            or None if URL is invalid
            
        Example:
            >>> manager = PluginManager()
            >>> manager._parse_github_url("https://github.com/user/repo")
            {'owner': 'user', 'repo': 'repo', 'branch': 'main', 'subdirectory': None}
        """
        # Handle SSH format: git@github.com:owner/repo.git
        ssh_pattern = r'git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:\.git)?$'
        ssh_match = re.match(ssh_pattern, url)
        if ssh_match:
            return {
                'owner': ssh_match.group('owner'),
                'repo': ssh_match.group('repo'),
                'branch': 'main',
                'subdirectory': None
            }
        
        # Handle HTTPS format
        parsed = urlparse(url)
        if parsed.netloc not in ['github.com', 'www.github.com']:
            return None
        
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) < 2:
            return None
        
        owner = path_parts[0]
        repo = path_parts[1]
        branch = 'main'
        subdirectory = None
        
        # Handle tree/branch[/subdirectory] format
        if len(path_parts) > 2 and path_parts[2] == 'tree':
            if len(path_parts) > 3:
                branch = path_parts[3]
            if len(path_parts) > 4:
                subdirectory = '/'.join(path_parts[4:])
        
        return {
            'owner': owner,
            'repo': repo,
            'branch': branch,
            'subdirectory': subdirectory
        }
    
    def _clone_or_update_repo(
        self,
        repo_info: Dict[str, str],
        target_dir: Path,
        force_update: bool = False
    ) -> Path:
        """
        Clone or update a GitHub repository.
        
        Args:
            repo_info: Dictionary with 'owner', 'repo', 'branch' keys
            target_dir: Target directory to clone to
            force_update: If True, remove existing directory and re-clone
            
        Returns:
            Path to the cloned repository directory
            
        Raises:
            RuntimeError: If git is not available or clone/update fails
        """
        repo_url = f"https://github.com/{repo_info['owner']}/{repo_info['repo']}.git"
        branch = repo_info['branch']
        
        # Check if git is available
        try:
            subprocess.run(['git', '--version'], check=True, capture_output=True, timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            raise RuntimeError("❌ Git is not available. Please install git to use GitHub plugins.")
        
        # If force_update, remove existing directory
        if force_update and target_dir.exists():
            logger.info(f"🔄 Force updating repository: {target_dir}")
            shutil.rmtree(target_dir)
        
        # Clone or update repository
        if target_dir.exists():
            # Update existing repository
            try:
                logger.info(f"🔄 Updating cached repository: {repo_url} (branch: {branch})")
                subprocess.run(
                    ['git', 'fetch', 'origin'],
                    cwd=target_dir,
                    check=True,
                    capture_output=True,
                    timeout=60
                )
                subprocess.run(
                    ['git', 'checkout', branch],
                    cwd=target_dir,
                    check=True,
                    capture_output=True,
                    timeout=10
                )
                subprocess.run(
                    ['git', 'pull', 'origin', branch],
                    cwd=target_dir,
                    check=True,
                    capture_output=True,
                    timeout=60
                )
                logger.info(f"✅ Repository updated successfully: {target_dir}")
            except subprocess.CalledProcessError as e:
                logger.warning(f"⚠️ Failed to update repository, will re-clone: {e}")
                shutil.rmtree(target_dir)
            except subprocess.TimeoutExpired:
                logger.warning(f"⚠️ Git operation timed out, will re-clone")
                shutil.rmtree(target_dir)
        
        if not target_dir.exists():
            # Clone repository
            try:
                logger.info(f"📥 Cloning repository: {repo_url} (branch: {branch}) to {target_dir}")
                subprocess.run(
                    ['git', 'clone', '--depth', '1', '--branch', branch, repo_url, str(target_dir)],
                    check=True,
                    capture_output=True,
                    timeout=120
                )
                logger.info(f"✅ Repository cloned successfully: {target_dir}")
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
                logger.error(f"❌ Failed to clone repository {repo_url}: {error_msg}")
                raise RuntimeError(f"Failed to clone GitHub repository {repo_url}: {error_msg}")
            except subprocess.TimeoutExpired:
                logger.error(f"❌ Git clone timed out for {repo_url}")
                raise RuntimeError(f"Git clone timed out for GitHub repository {repo_url}")
        
        # If there's a subdirectory, return the subdirectory path
        if repo_info.get('subdirectory'):
            subdir_path = target_dir / repo_info['subdirectory']
            if not subdir_path.exists():
                logger.warning(f"⚠️ Subdirectory not found in repository: {subdir_path}")
            return subdir_path
        
        return target_dir
    
    def install(
        self,
        plugin_name: str,
        url: Optional[str] = None,
        local_path: Optional[str] = None,
        force: bool = False
    ) -> bool:
        """
        Install a plugin from URL or local path.
        
        Args:
            plugin_name: Name of the plugin (used as installation directory name)
            url: GitHub URL or other git URL to clone from
            local_path: Local path to plugin directory (for local installation)
            force: If True, remove existing plugin and reinstall
        
        Returns:
            True if installation succeeded, False otherwise
            
        Raises:
            ValueError: If neither url nor local_path is provided, or if both are provided
            RuntimeError: If installation fails
            
        Example:
            >>> manager = PluginManager()
            >>> manager.install("my-plugin", url="https://github.com/user/repo")
            True
            >>> manager.install("local-plugin", local_path="./local/plugin")
            True
        """
        if not url and not local_path:
            raise ValueError("❌ Either --url or --local-path must be provided")
        
        if url and local_path:
            raise ValueError("❌ Cannot specify both --url and --local-path")
        
        plugin_path = self.plugin_dir / plugin_name
        
        # Check if plugin already exists
        if plugin_path.exists() and not force:
            logger.error(f"❌ Plugin '{plugin_name}' already exists. Use --force to reinstall.")
            return False
        
        # Remove existing plugin if force is True
        if plugin_path.exists() and force:
            logger.info(f"🔄 Removing existing plugin: {plugin_name}")
            shutil.rmtree(plugin_path)
            if plugin_name in self._manifest:
                del self._manifest[plugin_name]
        
        try:
            if url:
                # Install from URL (GitHub or other git repository)
                repo_info = self._parse_github_url(url)
                if not repo_info:
                    # Try treating as generic git URL
                    logger.info(f"📥 Cloning repository from URL: {url}")
                    try:
                        subprocess.run(['git', '--version'], check=True, capture_output=True, timeout=5)
                    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                        raise RuntimeError("❌ Git is not available. Please install git to use git URLs.")
                    
                    # Clone directly to plugin_path
                    subprocess.run(
                        ['git', 'clone', '--depth', '1', url, str(plugin_path)],
                        check=True,
                        capture_output=True,
                        timeout=120
                    )
                    logger.info(f"✅ Plugin '{plugin_name}' installed from URL: {url}")
                else:
                    # GitHub repository
                    cloned_path = self._clone_or_update_repo(repo_info, plugin_path, force_update=force)
                    if cloned_path != plugin_path:
                        # If subdirectory was specified, move contents to plugin_path
                        if plugin_path.exists():
                            shutil.rmtree(plugin_path)
                        shutil.copytree(cloned_path, plugin_path)
                        shutil.rmtree(cloned_path.parent.parent)  # Clean up cloned repo
                    logger.info(f"✅ Plugin '{plugin_name}' installed from GitHub: {url}")
            else:
                # Install from local path
                source_path = Path(local_path).resolve()
                if not source_path.exists():
                    raise ValueError(f"❌ Local path does not exist: {local_path}")
                
                if source_path.is_file():
                    raise ValueError(f"❌ Local path must be a directory, not a file: {local_path}")
                
                logger.info(f"📥 Installing plugin from local path: {local_path}")
                shutil.copytree(source_path, plugin_path)
                logger.info(f"✅ Plugin '{plugin_name}' installed from local path: {local_path}")
            
            # Verify plugin structure (at least agents directory should exist)
            agents_dir = plugin_path / "agents"
            skills_dir = plugin_path / "skills"
            
            if not agents_dir.exists():
                logger.warning(f"⚠️ Plugin '{plugin_name}' does not have an 'agents' directory. "
                             f"This may be intentional, but agents won't be loaded from this plugin.")
            
            # Register plugin in manifest
            from datetime import datetime
            plugin_url = url or local_path or "unknown"
            self._manifest[plugin_name] = {
                "name": plugin_name,
                "path": str(plugin_path),
                "source": plugin_url,
                "installed_at": datetime.now().isoformat(),
            }
            self._save_manifest()
            
            logger.info(f"🎉 Plugin '{plugin_name}' installed successfully to {plugin_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to install plugin '{plugin_name}': {e}")
            # Clean up on failure
            if plugin_path.exists():
                shutil.rmtree(plugin_path)
            raise
    
    def remove(self, plugin_name: str) -> bool:
        """
        Remove an installed plugin.
        
        Args:
            plugin_name: Name of the plugin to remove
        
        Returns:
            True if removal succeeded, False otherwise
            
        Example:
            >>> manager = PluginManager()
            >>> manager.remove("my-plugin")
            True
        """
        if plugin_name not in self._manifest:
            logger.error(f"❌ Plugin '{plugin_name}' is not installed")
            return False
        
        plugin_path = self.plugin_dir / plugin_name
        
        if not plugin_path.exists():
            logger.warning(f"⚠️ Plugin directory does not exist: {plugin_path}")
            # Remove from manifest anyway
            del self._manifest[plugin_name]
            self._save_manifest()
            return True
        
        try:
            shutil.rmtree(plugin_path)
            del self._manifest[plugin_name]
            self._save_manifest()
            logger.info(f"✅ Plugin '{plugin_name}' removed successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to remove plugin '{plugin_name}': {e}")
            return False
    
    def list_plugins(self) -> List[Dict[str, str]]:
        """
        List all installed plugins.
        
        Returns:
            List of dictionaries containing plugin information
            
        Example:
            >>> manager = PluginManager()
            >>> plugins = manager.list_plugins()
            >>> for plugin in plugins:
            ...     print(plugin['name'], plugin['path'])
        """
        plugins = []
        
        # Get plugins from manifest
        for plugin_name, plugin_info in self._manifest.items():
            plugin_path = Path(plugin_info.get('path', self.plugin_dir / plugin_name))
            
            # Check if plugin still exists
            if not plugin_path.exists():
                logger.warning(f"⚠️ Plugin '{plugin_name}' in manifest but directory not found: {plugin_path}")
                continue
            
            # Get plugin info
            agents_dir = plugin_path / "agents"
            skills_dir = plugin_path / "skills"
            
            plugin_data = {
                "name": plugin_name,
                "path": str(plugin_path),
                "source": plugin_info.get("source", "unknown"),
                "has_agents": agents_dir.exists() and any(agents_dir.iterdir()),
                "has_skills": skills_dir.exists() and any(skills_dir.iterdir()),
            }
            plugins.append(plugin_data)
        
        # Also check for plugins in directory that aren't in manifest (legacy plugins)
        if self.plugin_dir.exists():
            for item in self.plugin_dir.iterdir():
                if item.is_dir() and not item.name.startswith('.') and item.name not in self._manifest:
                    agents_dir = item / "agents"
                    skills_dir = item / "skills"
                    
                    plugin_data = {
                        "name": item.name,
                        "path": str(item),
                        "source": "unknown (legacy plugin)",
                        "has_agents": agents_dir.exists() and any(agents_dir.iterdir()),
                        "has_skills": skills_dir.exists() and any(skills_dir.iterdir()),
                    }
                    plugins.append(plugin_data)
        
        return plugins
    
    def get_plugin_dirs(self) -> List[Path]:
        """
        Get list of plugin agent directories for runtime loading.
        
        Returns:
            List of paths to agents directories from all installed plugins
            
        Example:
            >>> manager = PluginManager()
            >>> agent_dirs = manager.get_plugin_dirs()
            >>> for dir in agent_dirs:
            ...     print(dir)
        """
        agent_dirs = []
        
        plugins = self.list_plugins()
        for plugin in plugins:
            plugin_path = Path(plugin['path'])
            agents_dir = plugin_path / "agents"
            
            if agents_dir.exists() and agents_dir.is_dir():
                agent_dirs.append(agents_dir)
        
        return agent_dirs

    async def _load_skills(self, plugin_dirs: List[Path], console=None) -> Dict[str, int]:
        """
        Load skills from all plugin directories.
        
        Searches for skills in plugin_dir/skills directory for each plugin.
        Only directories containing SKILL.md file are considered as skills.
        Skills are registered into the global skill registry.
        
        Args:
            plugin_dirs: List of plugin directory paths
            console: Optional Rich console for output
            
        Returns:
            Dictionary mapping plugin names to number of skills loaded
        """
        from ..core.skill_registry import get_skill_registry
        
        registry = get_skill_registry()
        loaded_skills: Dict[str, int] = {}
        
        for plugin_dir in plugin_dirs:
            skills_dir = plugin_dir / "skills"
            
            if not skills_dir.exists() or not skills_dir.is_dir():
                continue
            
            try:
                # Check for subdirectories containing SKILL.md files
                skill_count = 0
                for subdir in skills_dir.iterdir():
                    if not subdir.is_dir():
                        continue
                    
                    # Only consider directories that contain SKILL.md file
                    skill_md_file = subdir / "SKILL.md"
                    if skill_md_file.exists() and skill_md_file.is_file():
                        skill_count += 1
                
                # Only register if there are valid skill directories (with SKILL.md)
                if skill_count > 0:
                    count = registry.register_source(str(skills_dir), source_name=str(skills_dir))
                    plugin_name = plugin_dir.name
                    loaded_skills[plugin_name] = count
                    
                    if console and count > 0:
                        console.print(f"[dim]📚 Loaded {count} skill(s) from plugin: {plugin_name}[/dim]")
                else:
                    # No valid skill directories found (no SKILL.md files)
                    plugin_name = plugin_dir.name
                    loaded_skills[plugin_name] = 0
            except Exception as e:
                plugin_name = plugin_dir.name
                if console:
                    console.print(f"[yellow]⚠️ Failed to load skills from plugin {plugin_name}: {e}[/yellow]")
                loaded_skills[plugin_name] = 0
        
        return loaded_skills

    async def _load_agents(
        self,
        plugin_dirs: List[Path],
        local_dirs: Optional[List[str]] = None,
        remote_backends: Optional[List[str]] = None,
        console=None
    ) -> Tuple[List, Dict[str, Dict]]:
        """
        Load agents following unified lifecycle (Load phase):
        1. Load plugins (skills + agents)
        2. Load local agents
        3. Load remote agents
        
        Uses abstract loaders to eliminate code duplication.
        Loaders are responsible ONLY for loading, not for creating executors.
        
        Args:
            plugin_dirs: List of plugin directory paths
            local_dirs: Optional list of local agent directories
            remote_backends: Optional list of remote backend URLs
            console: Optional Rich console for output
            
        Returns:
            Tuple of (List of all loaded AgentInfo objects, agent_sources_map dictionary)
            Agents are deduplicated, prioritizing local over remote
        """
        from ..models import AgentInfo
        from ..runtime.loaders import PluginLoader, LocalAgentLoader, RemoteAgentLoader
        
        all_agents: List[AgentInfo] = []
        agent_sources_map: Dict[str, Dict] = {}  # Track sources for executor creation
        
        # ========== Lifecycle Step 1: Load Plugins ==========
        # For each plugin: load skills, then load agents
        for plugin_dir in plugin_dirs:
            try:
                loader = PluginLoader(plugin_dir, console=console)
                
                # Load agents from plugin (this also loads skills internally)
                plugin_agents = await loader.load_agents()
                
                # Track source information
                for agent in plugin_agents:
                    if agent.name not in agent_sources_map:
                        agent_sources_map[agent.name] = {
                            "type": "plugin",
                            "location": str(plugin_dir),
                            "agents_dir": str(plugin_dir / "agents")  # Store agents dir for executor creation
                        }
                        all_agents.append(agent)
                    else:
                        if console:
                            console.print(f"[dim]⚠️ Duplicate agent '{agent.name}' from plugin, keeping first[/dim]")
                        
            except Exception as e:
                if console:
                    console.print(f"[yellow]⚠️ Failed to load plugin {plugin_dir}: {e}[/yellow]")
        
        # ========== Lifecycle Step 2: Load Local Agents ==========
        if local_dirs:
            if console:
                console.print(f"[dim]📂 Loading local agents from {len(local_dirs)} directory(ies)...[/dim]")
        
        local_agents_count = 0
        for local_dir in local_dirs or []:
            try:
                if console:
                    console.print(f"[dim]  📁 Scanning local directory: {local_dir}[/dim]")
                loader = LocalAgentLoader(local_dir, console=console)
                
                # Load agents from local directory
                local_agents = await loader.load_agents()
                
                if local_agents:
                    if console:
                        console.print(f"[dim]  ✅ Found {len(local_agents)} agent(s) in {local_dir}[/dim]")
                    local_agents_count += len(local_agents)
                else:
                    if console:
                        console.print(f"[dim]  ℹ️  No agents found in {local_dir}[/dim]")
                
                # Track source information (prioritize local over remote)
                for agent in local_agents:
                    if agent.name not in agent_sources_map:
                        agent_sources_map[agent.name] = {
                            "type": "local",
                            "location": local_dir
                        }
                        all_agents.append(agent)
                        if console:
                            console.print(f"[dim]    ✓ Loaded agent: {agent.name} (local)[/dim]")
                    else:
                        existing_source = agent_sources_map[agent.name]
                        if existing_source["type"] == "local":
                            if console:
                                console.print(f"[dim]    ⚠️ Duplicate agent '{agent.name}' found, keeping first occurrence[/dim]")
                        else:
                            # Replace remote/plugin with local (prioritize LOCAL)
                            agent_sources_map[agent.name] = {
                                "type": "local",
                                "location": local_dir
                            }
                            # Replace in all_agents list
                            for i, a in enumerate(all_agents):
                                if a.name == agent.name:
                                    all_agents[i] = agent
                                    break
                            if console:
                                console.print(f"[dim]    ⚠️ Duplicate agent '{agent.name}' found, replacing {existing_source['type']} version with local[/dim]")
                        
            except Exception as e:
                if console:
                    console.print(f"[yellow]⚠️ Failed to load from {local_dir}: {e}[/yellow]")
        
        if local_dirs and local_agents_count > 0:
            if console:
                console.print(f"[dim]📊 Total local agents loaded: {local_agents_count}[/dim]")
        
        # ========== Lifecycle Step 3: Load Remote Agents ==========
        if remote_backends:
            if console:
                console.print(f"[dim]🌐 Loading remote agents from {len(remote_backends)} backend(s)...[/dim]")
        
        remote_agents_count = 0
        for backend_url in remote_backends or []:
            try:
                if console:
                    console.print(f"[dim]  🔗 Connecting to remote backend: {backend_url}[/dim]")
                loader = RemoteAgentLoader(backend_url, console=console)
                
                # Load agents from remote backend
                remote_agents = await loader.load_agents()
                
                if remote_agents:
                    if console:
                        console.print(f"[dim]  ✅ Found {len(remote_agents)} agent(s) from {backend_url}[/dim]")
                    remote_agents_count += len(remote_agents)
                else:
                    if console:
                        console.print(f"[dim]  ℹ️  No agents found from {backend_url}[/dim]")
                
                # Track source information (only if local doesn't exist)
                for agent in remote_agents:
                    if agent.name not in agent_sources_map:
                        agent_sources_map[agent.name] = {
                            "type": "remote",
                            "location": backend_url
                        }
                        all_agents.append(agent)
                        if console:
                            console.print(f"[dim]    ✓ Loaded agent: {agent.name} (remote)[/dim]")
                    else:
                        # Local/plugin source exists, skip remote duplicate
                        existing_source = agent_sources_map[agent.name]
                        if console:
                            console.print(f"[dim]    ⚠️ Duplicate agent '{agent.name}' found (remote), keeping {existing_source['type']} version[/dim]")
                        
            except Exception as e:
                if console:
                    console.print(f"[yellow]⚠️ Failed to load from {backend_url}: {e}[/yellow]")
        
        if remote_backends and remote_agents_count > 0:
            if console:
                console.print(f"[dim]📊 Total remote agents loaded: {remote_agents_count}[/dim]")
        
        # Summary log
        plugin_count = len([a for a in all_agents if agent_sources_map.get(a.name, {}).get("type") == "plugin"])
        local_count = len([a for a in all_agents if agent_sources_map.get(a.name, {}).get("type") == "local"])
        remote_count = len([a for a in all_agents if agent_sources_map.get(a.name, {}).get("type") == "remote"])
        
        if all_agents:
            if console:
                console.print(f"[green]✅ Agent loading complete: {len(all_agents)} total agent(s) (plugin: {plugin_count}, local: {local_count}, remote: {remote_count})[/green]")
        
        if not all_agents:
            if console:
                console.print("[red]❌ No agents found from any source.[/red]")
        
        return all_agents, agent_sources_map
