#!/usr/bin/env python3
"""
Export structured metadata for all skill documentation files.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse
from aworld.logs.util import logger

# Default cache directory for GitHub repositories
DEFAULT_CACHE_DIR = Path.home() / ".aworld" / "skills"


def parse_github_url(github_url: str) -> Optional[Dict[str, str]]:
    """
    Parse GitHub URL to extract owner, repo, branch, and subdirectory.
    
    Supports multiple formats:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/tree/branch
    - https://github.com/owner/repo/tree/branch/subdirectory
    - git@github.com:owner/repo.git
    
    Args:
        github_url: GitHub repository URL
        
    Returns:
        Dict with 'owner', 'repo', 'branch' (default: 'main'), and 'subdirectory' (optional) keys,
        or None if URL is invalid
        
    Example:
        >>> parse_github_url("https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering")
        {'owner': 'muratcankoylan', 'repo': 'Agent-Skills-for-Context-Engineering', 'branch': 'main', 'subdirectory': None}
        >>> parse_github_url("https://github.com/user/repo/tree/main/skills")
        {'owner': 'user', 'repo': 'repo', 'branch': 'main', 'subdirectory': 'skills'}
    """
    # Handle SSH format: git@github.com:owner/repo.git
    ssh_pattern = r'git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:\.git)?$'
    ssh_match = re.match(ssh_pattern, github_url)
    if ssh_match:
        return {
            'owner': ssh_match.group('owner'),
            'repo': ssh_match.group('repo'),
            'branch': 'main',
            'subdirectory': None
        }
    
    # Handle HTTPS format
    parsed = urlparse(github_url)
    if parsed.netloc not in ['github.com', 'www.github.com']:
        return None
    
    path_parts = parsed.path.strip('/').split('/')
    if len(path_parts) < 2:
        return None
    
    owner = path_parts[0]
    repo = path_parts[1]
    branch = 'main'
    subdirectory = None
    
    # Check if there's a tree path (e.g., /tree/branch or /tree/branch/subdirectory)
    if len(path_parts) >= 4 and path_parts[2] == 'tree':
        branch = path_parts[3]
        if len(path_parts) > 4:
            subdirectory = '/'.join(path_parts[4:])
    
    return {
        'owner': owner,
        'repo': repo,
        'branch': branch,
        'subdirectory': subdirectory
    }


def get_github_cache_path(repo_info: Dict[str, str], cache_dir: Optional[Path] = None) -> Path:
    """
    Get the cache path for a GitHub repository.
    
    Args:
        repo_info: Dictionary with 'owner', 'repo', 'branch' keys from parse_github_url
        cache_dir: Optional cache directory, defaults to DEFAULT_CACHE_DIR
        
    Returns:
        Path to the cached repository directory
        
    Example:
        >>> repo_info = {'owner': 'user', 'repo': 'repo', 'branch': 'main'}
        >>> get_github_cache_path(repo_info)
        Path('~/.aworld/skills/user/repo/main')
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    
    # Create cache path: cache_dir/owner/repo/branch
    cache_path = cache_dir / repo_info['owner'] / repo_info['repo'] / repo_info['branch']
    return cache_path


def clone_or_update_github_repo(
    repo_info: Dict[str, str],
    cache_dir: Optional[Path] = None,
    force_update: bool = False
) -> Path:
    """
    Clone or update a GitHub repository to the cache directory.
    
    If the repository already exists in cache, it will be updated using git pull.
    If force_update is True, the cache will be removed and re-cloned.
    
    Args:
        repo_info: Dictionary with 'owner', 'repo', 'branch' keys from parse_github_url
        cache_dir: Optional cache directory, defaults to DEFAULT_CACHE_DIR
        force_update: If True, remove existing cache and re-clone
        
    Returns:
        Path to the cloned repository directory
        
    Raises:
        RuntimeError: If git is not available or clone/update fails
        
    Example:
        >>> repo_info = {'owner': 'muratcankoylan', 'repo': 'Agent-Skills-for-Context-Engineering', 'branch': 'main'}
        >>> clone_or_update_github_repo(repo_info)
        Path('~/.aworld/skills/muratcankoylan/Agent-Skills-for-Context-Engineering/main')
    """
    cache_path = get_github_cache_path(repo_info, cache_dir)
    repo_url = f"https://github.com/{repo_info['owner']}/{repo_info['repo']}.git"
    branch = repo_info['branch']
    
    # Check if git is available
    try:
        subprocess.run(['git', '--version'], check=True, capture_output=True, timeout=5)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        raise RuntimeError("❌ Git is not available. Please install git to use GitHub skill repositories.")
    
    # Create cache directory structure
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    
    # If force_update, remove existing cache
    if force_update and cache_path.exists():
        logger.info(f"🔄 Force updating repository cache: {cache_path}")
        shutil.rmtree(cache_path)
    
    # Clone or update repository
    if cache_path.exists():
        # Update existing repository
        try:
            logger.info(f"🔄 Updating cached repository: {repo_url} (branch: {branch})")
            # Fetch and checkout the specified branch
            subprocess.run(
                ['git', 'fetch', 'origin'],
                cwd=cache_path,
                check=True,
                capture_output=True,
                timeout=60
            )
            subprocess.run(
                ['git', 'checkout', branch],
                cwd=cache_path,
                check=True,
                capture_output=True,
                timeout=10
            )
            subprocess.run(
                ['git', 'pull', 'origin', branch],
                cwd=cache_path,
                check=True,
                capture_output=True,
                timeout=60
            )
            logger.info(f"✅ Repository updated successfully: {cache_path}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"⚠️ Failed to update repository, will re-clone: {e}")
            shutil.rmtree(cache_path)
            # Fall through to clone
        except subprocess.TimeoutExpired:
            logger.warning(f"⚠️ Git operation timed out, will re-clone")
            shutil.rmtree(cache_path)
            # Fall through to clone
    
    if not cache_path.exists():
        # Clone repository
        try:
            logger.info(f"📥 Cloning repository: {repo_url} (branch: {branch}) to {cache_path}")
            subprocess.run(
                ['git', 'clone', '--depth', '1', '--branch', branch, repo_url, str(cache_path)],
                check=True,
                capture_output=True,
                timeout=120
            )
            logger.info(f"✅ Repository cloned successfully: {cache_path}")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
            logger.error(f"❌ Failed to clone repository {repo_url}: {error_msg}")
            raise RuntimeError(f"Failed to clone GitHub repository {repo_url}: {error_msg}")
        except subprocess.TimeoutExpired:
            logger.error(f"❌ Git clone timed out for {repo_url}")
            raise RuntimeError(f"Git clone timed out for GitHub repository {repo_url}")
    
    return cache_path


def resolve_skill_path(skill_path: Union[str, Path], cache_dir: Optional[Path] = None) -> Path:
    """
    Resolve skill path, handling both local paths and GitHub URLs.
    
    If the path is a GitHub URL, it will be cloned/cached locally.
    If it's a local path, it will be resolved as-is.
    
    Args:
        skill_path: Local path or GitHub URL
        cache_dir: Optional cache directory for GitHub repos, defaults to DEFAULT_CACHE_DIR
        
    Returns:
        Resolved Path object pointing to the skill directory
        
    Example:
        >>> resolve_skill_path("https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering")
        Path('~/.aworld/skills/muratcankoylan/Agent-Skills-for-Context-Engineering/main')
        >>> resolve_skill_path("./local/skills")
        Path('/absolute/path/to/local/skills')
    """
    skill_path_str = str(skill_path)
    
    # Check if it's a GitHub URL
    if 'github.com' in skill_path_str or skill_path_str.startswith('git@github.com'):
        repo_info = parse_github_url(skill_path_str)
        if not repo_info:
            raise ValueError(f"❌ Invalid GitHub URL format: {skill_path_str}")
        
        # Clone or update repository
        cache_path = clone_or_update_github_repo(repo_info, cache_dir)
        
        # If there's a subdirectory, append it to the cache path
        if repo_info.get('subdirectory'):
            cache_path = cache_path / repo_info['subdirectory']
            if not cache_path.exists():
                logger.warning(f"⚠️ Subdirectory not found in repository: {cache_path}")
        
        return cache_path
    else:
        # Local path
        return Path(skill_path).resolve()


def extract_front_matter(content_lines: List[str]) -> Tuple[Dict[str, Any], int]:
    """
    Extract YAML-like front matter from the provided content lines.

    Args:
        content_lines (List[str]): The content of the markdown file split into lines.

    Returns:
        Tuple[Dict[str, Any], int]: A dictionary containing the parsed front matter key-value pairs
        and the index where the front matter ends. Values can be strings or parsed JSON objects.

    Example:
        >>> extract_front_matter(["---", "name: sample", "---", "body"])
        ({'name': 'sample'}, 3)
        >>> extract_front_matter(["---", "name: sample", 'tool_list: {"ms-playwright": []}', "---", "body"])
        ({'name': 'sample', 'tool_list': {'ms-playwright': []}}, 4)
    """
    front_matter: Dict[str, Any] = {}
    if not content_lines or content_lines[0].strip() != "---":
        return front_matter, 0

    end_index = 1
    while end_index < len(content_lines) and content_lines[end_index].strip() != "---":
        line = content_lines[end_index].strip()
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            
            # Try to parse JSON values (for tool_list and other structured data)
            if key == "tool_list" and value:
                try:
                    front_matter[key] = json.loads(value)
                    logger.debug(f"✅ Successfully parsed tool_list as JSON: {front_matter[key]}")
                except json.JSONDecodeError as e:
                    logger.warning(f"⚠️ Failed to parse tool_list as JSON: {e}, keeping as string")
                    front_matter[key] = value
            else:
                front_matter[key] = value
        end_index += 1

    if end_index >= len(content_lines):
        return front_matter, len(content_lines)

    return front_matter, end_index + 1


def collect_skill_docs(
    root_path: Union[str, Path],
    cache_dir: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Collect skill documentation metadata from all subdirectories containing skill.md or SKILL.md files.
    
    Supports both local paths and GitHub URLs. If a GitHub URL is provided, it will be cloned/cached
    locally before collecting skills.

    Args:
        root_path (Union[str, Path]): Root directory or GitHub URL to search for skill documentation files.
            Examples:
            - Local path: "./skills" or "/path/to/skills"
            - GitHub URL: "https://github.com/owner/repo" or "https://github.com/owner/repo/tree/branch/skills"
        cache_dir (Optional[Path]): Cache directory for GitHub repositories, defaults to DEFAULT_CACHE_DIR

    Returns:
        Dict[str, Dict[str, Any]]: Mapping from skill names to metadata containing
        name, description, tool_list (as dict), usage content, and skill_path.

    Example:
        >>> collect_skill_docs(Path("."))
        {'tts': {'name': 'tts', 'desc': '...', 'tool_list': {...}, 'usage': '...', 'skill_path': '...'}}
        >>> collect_skill_docs("https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering")
        {'context-fundamentals': {...}, 'context-degradation': {...}, ...}
    """
    results: Dict[str, Dict[str, Any]] = {}
    logger.info(f"🔍 Starting to collect skills from: {root_path}")
    
    try:
        # Resolve path (handles GitHub URLs by cloning/caching)
        root_dir = resolve_skill_path(root_path, cache_dir)
    except Exception as e:
        logger.error(f"❌ Failed to resolve skill path {root_path}: {e}")
        return results
    
    if not root_dir.exists():
        logger.warning(f"⚠️ Skill directory does not exist: {root_dir}")
        return results
    
    # Search for both skill.md and SKILL.md (GitHub repository format)
    skill_patterns = ["**/skill.md", "**/SKILL.md"]
    
    for pattern in skill_patterns:
        for skill_file in root_dir.glob(pattern):
            try:
                logger.debug(f"📄 Processing skill file: {skill_file}")
                content = skill_file.read_text(encoding="utf-8").splitlines()
                front_matter, body_start = extract_front_matter(content)
                body_lines = content[body_start:]

                usage = "\n".join(body_lines).strip()
                desc = front_matter.get("desc", front_matter.get("description", ""))
                tool_list = front_matter.get("tool_list", {})
                
                # Ensure tool_list is a dict
                if isinstance(tool_list, str):
                    logger.warning(f"⚠️ tool_list for skill '{front_matter.get('name', '')}' is still a string, converting to empty dict")
                    tool_list = {}

                skill_name = skill_file.parent.name
                
                # If skill name already exists, log a warning but keep the first one found
                if skill_name in results:
                    logger.warning(f"⚠️ Duplicate skill name '{skill_name}' found at {skill_file}, skipping")
                    continue

                results[skill_name] = {
                    "name": skill_name,
                    "description": desc,
                    "tool_list": tool_list,
                    "usage": usage,
                    "type": front_matter.get("type", ""),
                    "active": front_matter.get("active", "False").lower() == "true",
                    "skill_path": skill_file.as_posix(),
                }
                logger.debug(f"✅ Collected skill: {skill_name}")
            except Exception as e:
                logger.error(f"❌ Failed to process skill file {skill_file}: {e}")
                continue

    logger.info(f"✅ Total skill count: {len(results)} -> {list(results.keys())}")
    return results


class SkillRegistry:
    """
    Registry for managing skills from multiple sources (local paths and GitHub repositories).
    
    Provides unified interface for registering skill sources, collecting skills, and managing
    skill collections with conflict resolution.
    
    Example:
        >>> registry = SkillRegistry()
        >>> # Register local skills directory
        >>> registry.register_source("./local/skills")
        >>> # Register GitHub repository
        >>> registry.register_source("https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering")
        >>> # Get all skills
        >>> all_skills = registry.get_all_skills()
        >>> # Get specific skill
        >>> skill = registry.get_skill("context-fundamentals")
        >>> # Get skills by source
        >>> github_skills = registry.get_skills_by_source("https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering")
    """
    
    def __init__(self, cache_dir: Optional[Path] = None, conflict_strategy: str = "keep_first"):
        """
        Initialize SkillRegistry.
        
        Args:
            cache_dir: Cache directory for GitHub repositories, defaults to DEFAULT_CACHE_DIR
            conflict_strategy: Strategy for handling skill name conflicts. Options:
                - "keep_first": Keep the first skill with the name (default)
                - "keep_last": Keep the last skill with the name
                - "raise": Raise an error on conflict
        """
        self._sources: Dict[str, Union[str, Path]] = {}
        self._skills: Dict[str, Dict[str, Any]] = {}
        self._source_to_skills: Dict[str, List[str]] = {}  # Map source to skill names
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._conflict_strategy = conflict_strategy
        
        # Validate conflict strategy
        if conflict_strategy not in ["keep_first", "keep_last", "raise"]:
            raise ValueError(f"Invalid conflict_strategy: {conflict_strategy}. Must be one of: keep_first, keep_last, raise")
    
    def register_source(
        self,
        source: Union[str, Path],
        source_name: Optional[str] = None,
        force_reload: bool = False
    ) -> int:
        """
        Register a skill source (local path or GitHub URL) and load skills from it.
        
        Args:
            source: Local path or GitHub URL to load skills from
            source_name: Optional name for the source. If not provided, uses the source path/URL as name
            force_reload: If True, force reload skills even if source is already registered
            
        Returns:
            Number of skills loaded from this source
            
        Example:
            >>> registry = SkillRegistry()
            >>> count = registry.register_source("https://github.com/user/repo")
            >>> print(f"Loaded {count} skills")
        """
        source_str = str(source)
        source_key = source_name or source_str
        
        # Check if source already registered
        if source_key in self._sources and not force_reload:
            logger.debug(f"ℹ️ Source already registered: {source_key}")
            return len(self._source_to_skills.get(source_key, []))
        
        # Load skills from source
        try:
            logger.info(f"📚 Registering skill source: {source_key}")
            skills = collect_skill_docs(source, cache_dir=self._cache_dir)
            
            # Track which skills came from this source
            source_skill_names = []
            
            # Merge skills into registry
            for skill_name, skill_data in skills.items():
                # Handle conflicts
                if skill_name in self._skills:
                    if self._conflict_strategy == "raise":
                        raise ValueError(
                            f"❌ Skill name conflict: '{skill_name}' already exists. "
                            f"Existing source: {self._get_skill_source(skill_name)}, "
                            f"New source: {source_key}"
                        )
                    elif self._conflict_strategy == "keep_first":
                        logger.warning(
                            f"⚠️ Skill '{skill_name}' already exists, keeping first version. "
                            f"New source: {source_key}"
                        )
                        continue
                    elif self._conflict_strategy == "keep_last":
                        logger.warning(
                            f"⚠️ Skill '{skill_name}' already exists, replacing with new version. "
                            f"Old source: {self._get_skill_source(skill_name)}, "
                            f"New source: {source_key}"
                        )
                        # Remove from old source tracking
                        old_source = self._get_skill_source(skill_name)
                        if old_source and old_source in self._source_to_skills:
                            self._source_to_skills[old_source].remove(skill_name)
                
                # Add skill to registry
                self._skills[skill_name] = skill_data
                source_skill_names.append(skill_name)
            
            # Register source
            self._sources[source_key] = source
            self._source_to_skills[source_key] = source_skill_names
            
            logger.info(
                f"✅ Registered source '{source_key}': "
                f"loaded {len(source_skill_names)} skill(s): {source_skill_names}"
            )
            return len(source_skill_names)
            
        except Exception as e:
            logger.error(f"❌ Failed to register skill source '{source_key}': {e}")
            raise
    
    def unregister_source(self, source_name: str) -> int:
        """
        Unregister a skill source and remove its skills from the registry.
        
        Args:
            source_name: Name of the source to unregister
            
        Returns:
            Number of skills removed
            
        Example:
            >>> registry.unregister_source("https://github.com/user/repo")
        """
        if source_name not in self._sources:
            logger.warning(f"⚠️ Source not registered: {source_name}")
            return 0
        
        # Get skills from this source
        skill_names = self._source_to_skills.get(source_name, [])
        
        # Remove skills (only if they're still associated with this source)
        removed_count = 0
        for skill_name in list(skill_names):
            current_source = self._get_skill_source(skill_name)
            if current_source == source_name:
                del self._skills[skill_name]
                removed_count += 1
        
        # Remove source tracking
        del self._sources[source_name]
        if source_name in self._source_to_skills:
            del self._source_to_skills[source_name]
        
        logger.info(f"✅ Unregistered source '{source_name}': removed {removed_count} skill(s)")
        return removed_count
    
    def reload_source(self, source_name: str) -> int:
        """
        Reload skills from a registered source.
        
        Args:
            source_name: Name of the source to reload
            
        Returns:
            Number of skills loaded
            
        Example:
            >>> registry.reload_source("https://github.com/user/repo")
        """
        if source_name not in self._sources:
            raise ValueError(f"❌ Source not registered: {source_name}")
        
        source = self._sources[source_name]
        return self.register_source(source, source_name=source_name, force_reload=True)
    
    def get_all_skills(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all registered skills.
        
        Returns:
            Dictionary mapping skill names to skill configurations
            
        Example:
            >>> skills = registry.get_all_skills()
            >>> for name, config in skills.items():
            ...     print(f"{name}: {config['description']}")
        """
        return self._skills.copy()
    
    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific skill by name.
        
        Args:
            skill_name: Name of the skill to retrieve
            
        Returns:
            Skill configuration dictionary, or None if not found
            
        Example:
            >>> skill = registry.get_skill("context-fundamentals")
            >>> if skill:
            ...     print(skill["description"])
        """
        return self._skills.get(skill_name)
    
    def get_skills_by_source(self, source_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all skills from a specific source.
        
        Args:
            source_name: Name of the source
            
        Returns:
            Dictionary mapping skill names to skill configurations
            
        Example:
            >>> github_skills = registry.get_skills_by_source("https://github.com/user/repo")
        """
        skill_names = self._source_to_skills.get(source_name, [])
        return {name: self._skills[name] for name in skill_names if name in self._skills}
    
    def list_sources(self) -> List[str]:
        """
        List all registered source names.
        
        Returns:
            List of source names
            
        Example:
            >>> sources = registry.list_sources()
            >>> for source in sources:
            ...     print(source)
        """
        return list(self._sources.keys())
    
    def list_skills(self) -> List[str]:
        """
        List all registered skill names.
        
        Returns:
            List of skill names
            
        Example:
            >>> skill_names = registry.list_skills()
            >>> print(f"Total skills: {len(skill_names)}")
        """
        return list(self._skills.keys())
    
    def search_skills(self, keyword: str, search_fields: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        """
        Search skills by keyword in specified fields.
        
        Args:
            keyword: Keyword to search for
            search_fields: Fields to search in. Defaults to ["name", "description", "usage"].
                If None, searches in name, description, and usage fields.
            
        Returns:
            Dictionary of matching skills
            
        Example:
            >>> results = registry.search_skills("context")
            >>> results = registry.search_skills("browser", search_fields=["name", "description"])
        """
        if search_fields is None:
            search_fields = ["name", "description", "usage"]
        
        keyword_lower = keyword.lower()
        results = {}
        
        for skill_name, skill_data in self._skills.items():
            for field in search_fields:
                field_value = skill_data.get(field, "")
                if isinstance(field_value, str) and keyword_lower in field_value.lower():
                    results[skill_name] = skill_data
                    break
        
        return results
    
    def get_skills_by_regex(
        self,
        pattern: str,
        match_field: str = "name",
        flags: int = 0
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get skills matching a regular expression pattern.
        
        Args:
            pattern: Regular expression pattern to match
            match_field: Field to match against. Options: "name", "description", "usage", "type".
                Defaults to "name".
            flags: Optional regex flags (e.g., re.IGNORECASE, re.MULTILINE)
            
        Returns:
            Dictionary of matching skills
            
        Raises:
            ValueError: If match_field is invalid
            
        Example:
            >>> # Match all skills starting with "context"
            >>> results = registry.get_skills_by_regex(r"^context-.*")
            >>> # Case-insensitive match
            >>> results = registry.get_skills_by_regex(r"browser", match_field="name", flags=re.IGNORECASE)
            >>> # Match by description
            >>> results = registry.get_skills_by_regex(r"automation", match_field="description")
        """
        valid_fields = ["name", "description", "usage", "type"]
        if match_field not in valid_fields:
            raise ValueError(
                f"Invalid match_field: {match_field}. Must be one of: {valid_fields}"
            )
        
        try:
            compiled_pattern = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{pattern}': {e}")
        
        results = {}
        
        for skill_name, skill_data in self._skills.items():
            # Get the field value to match
            if match_field == "name":
                field_value = skill_name
            else:
                field_value = skill_data.get(match_field, "")
            
            # Match against the pattern
            if isinstance(field_value, str) and compiled_pattern.search(field_value):
                results[skill_name] = skill_data
        
        return results
    
    def get_skill_configs(self) -> Dict[str, Dict[str, Any]]:
        """
        Get skill configurations in the format expected by AgentConfig.
        
        This converts the internal skill format to the format used by AgentConfig,
        mapping 'description' to 'desc' for compatibility.
        
        Returns:
            Dictionary of skill configurations compatible with AgentConfig
            
        Example:
            >>> configs = registry.get_skill_configs()
            >>> agent_config = AgentConfig(skill_configs=configs)
        """
        configs = {}
        for skill_name, skill_data in self._skills.items():
            configs[skill_name] = {
                "name": skill_data.get("name", skill_name),
                "desc": skill_data.get("description", skill_data.get("desc", "")),
                "usage": skill_data.get("usage", ""),
                "tool_list": skill_data.get("tool_list", {}),
                "type": skill_data.get("type", ""),
                "active": skill_data.get("active", False),
            }
        return configs
    
    def _get_skill_source(self, skill_name: str) -> Optional[str]:
        """
        Get the source name for a skill.
        
        Args:
            skill_name: Name of the skill
            
        Returns:
            Source name, or None if skill not found or not tracked
        """
        for source_name, skill_names in self._source_to_skills.items():
            if skill_name in skill_names:
                return source_name
        return None
    
    def update_cache(self, source_name: Optional[str] = None) -> None:
        """
        Update cached GitHub repositories.
        
        Args:
            source_name: Optional specific source to update. If None, updates all GitHub sources.
            
        Example:
            >>> # Update all GitHub sources
            >>> registry.update_cache()
            >>> # Update specific source
            >>> registry.update_cache("https://github.com/user/repo")
        """
        sources_to_update = [source_name] if source_name else list(self._sources.keys())
        
        for source_key in sources_to_update:
            if source_key not in self._sources:
                logger.warning(f"⚠️ Source not found: {source_key}")
                continue
            
            source = self._sources[source_key]
            source_str = str(source)
            
            # Only update GitHub sources
            if 'github.com' in source_str or source_str.startswith('git@github.com'):
                try:
                    repo_info = parse_github_url(source_str)
                    if repo_info:
                        logger.info(f"🔄 Updating cache for: {source_key}")
                        clone_or_update_github_repo(repo_info, cache_dir=self._cache_dir, force_update=False)
                        # Reload skills after cache update
                        self.reload_source(source_key)
                except Exception as e:
                    logger.error(f"❌ Failed to update cache for '{source_key}': {e}")
            else:
                logger.debug(f"ℹ️ Skipping non-GitHub source: {source_key}")
    
    def clear(self) -> None:
        """
        Clear all registered sources and skills.
        
        Example:
            >>> registry.clear()
        """
        self._sources.clear()
        self._skills.clear()
        self._source_to_skills.clear()
        logger.info("🧹 Registry cleared")


