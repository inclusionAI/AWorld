"""
Global SkillRegistry manager for aworld-cli.

Provides a singleton SkillRegistry instance that automatically loads skills from
the default skills directory (./skills) and supports registering additional sources
from environment variables or programmatic configuration.
"""
import os
from pathlib import Path
from typing import Optional, List

from aworld.utils.skill_loader import SkillRegistry, DEFAULT_CACHE_DIR

from aworld.logs.util import logger

# Global SkillRegistry instance
_global_registry: Optional[SkillRegistry] = None

# Environment variable names
ENV_SKILLS_PATH = "SKILLS_PATH"  # Semicolon-separated list of skill sources
ENV_SKILLS_DIR = "SKILLS_DIR"    # Single skills directory (legacy, for backward compatibility)
ENV_SKILLS_CACHE_DIR = "SKILLS_CACHE_DIR"  # Custom cache directory for GitHub repos


def get_user_skills_paths() -> List[Path]:
    """
    Return the list of directories where user skills are stored.

    Resolution order:
    1. SKILLS_PATH env (semicolon-separated list of paths)
    2. If unset, default to ~/.aworld/skills
    3. SKILLS_DIR env (legacy, single directory) is appended if set

    Returns:
        List of resolved Paths; directories may or may not exist.

    Example:
        >>> paths = get_user_skills_paths()
        >>> for p in paths:
        ...     collect_skill_docs(p)
    """
    paths: List[Path] = []
    skills_path_env = os.getenv(ENV_SKILLS_PATH)
    if skills_path_env:
        paths = [
            Path(os.path.expanduser(s.strip())).resolve()
            for s in skills_path_env.split(";")
            if s.strip()
        ]
    else:
        paths = [Path.home() / ".aworld" / "skills"]
    skills_dir_env = os.getenv(ENV_SKILLS_DIR)
    if skills_dir_env:
        paths.append(Path(os.path.expanduser(skills_dir_env)).resolve())
    return paths


def get_skill_registry(
    skills_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    skill_paths: Optional[List[str]] = None,
    auto_init: bool = True
) -> SkillRegistry:
    """
    Get or initialize the global SkillRegistry instance.
    
    On first call, automatically registers skill sources from:
    1. Environment variables (SKILLS_PATH, SKILLS_DIR)
    2. Default skills directory (./skills) if exists
    3. Provided parameters (skill_paths, skills_dir)
    
    Subsequent calls return the same instance.
    
    Args:
        skills_dir: Optional custom skills directory to use as default.
            If None, uses "./skills" relative to current working directory.
        cache_dir: Optional cache directory for GitHub repositories.
            If None, uses DEFAULT_CACHE_DIR or SKILLS_CACHE_DIR env var.
        skill_paths: Optional list of skill source paths to register.
            Can be local paths or GitHub URLs.
        auto_init: If True, automatically register default skills directory and env vars.
            If False, return registry without auto-initialization.
    
    Returns:
        Global SkillRegistry instance
        
    Example:
        >>> registry = get_skill_registry()
        >>> # Register additional source
        >>> registry.register_source("https://github.com/user/repo")
        >>> # Get skills by name
        >>> skills = registry.get_all_skills()
    """
    global _global_registry
    
    if _global_registry is None:
        # Determine cache directory
        if cache_dir is None:
            env_cache_dir = os.getenv(ENV_SKILLS_CACHE_DIR)
            if env_cache_dir:
                # Expand ~ in path if present
                cache_dir = Path(os.path.expanduser(env_cache_dir))
            else:
                cache_dir = DEFAULT_CACHE_DIR
        
        _global_registry = SkillRegistry(cache_dir=cache_dir)
        
        if auto_init:
            # Register skills from environment variables
            _register_from_env(_global_registry)

            # Register skills from provided skill_paths parameter
            if skill_paths:
                for skill_path in skill_paths:
                    try:
                        count = _global_registry.register_source(skill_path, source_name=skill_path)
                        if count > 0:
                            logger.info(f"📚 Registered skill source: {skill_path} ({count} skills)")
                        logger.info(f"📚 Registered skill source from parameter: {skill_path}")
                    except Exception as e:
                        logger.error(f"⚠️ Failed to register skill source '{skill_path}': {e}")
                        logger.warning(f"⚠️ Failed to register skill source '{skill_path}': {e}")
            
            # Register default skills directory if provided or exists
            if skills_dir is None:
                # Default to ./skills in current working directory
                default_skills_dir = Path.cwd() / "skills"
            else:
                default_skills_dir = Path(skills_dir).resolve()
            
            # Register default skills directory if it exists
            if default_skills_dir.exists() and default_skills_dir.is_dir():
                try:
                    from .._globals import console
                    
                    count = _global_registry.register_source(
                        str(default_skills_dir),
                        source_name="default_skills"
                    )
                    if count > 0:
                        console.print(f"[dim]📚 Registered default skills directory: {default_skills_dir} ({count} skills)[/dim]")
                    logger.info(f"📚 Auto-registered default skills directory: {default_skills_dir} ({count} skills)")
                except Exception as e:
                    logger.debug(f"ℹ️ Default skills directory already registered or failed: {default_skills_dir}: {e}")
            else:
                logger.debug(f"ℹ️ Default skills directory not found: {default_skills_dir}, skipping auto-registration")
    
    return _global_registry


def _register_from_env(registry: SkillRegistry) -> None:
    """
    Register skill sources from environment variables.
    
    Args:
        registry: SkillRegistry instance to register sources to
    """
    # Register from SKILLS_PATH (semicolon-separated list)
    skills_path_env = os.getenv(ENV_SKILLS_PATH)
    if skills_path_env:
        skill_sources = [s.strip() for s in skills_path_env.split(';') if s.strip()]
        for source in skill_sources:
            try:
                count = registry.register_source(source, source_name=source)
                if count > 0:
                    print(f"📚 Registered skill source from {ENV_SKILLS_PATH}: {source} ({count} skills)")
                logger.info(f"📚 Registered skill source from {ENV_SKILLS_PATH}: {source}")
            except Exception as e:
                print(f"⚠️ Failed to register skill source from env '{source}': {e}")
                logger.warning(f"⚠️ Failed to register skill source from env '{source}': {e}")
    else:
        # Default to ~/.aworld/skills if ENV_SKILLS_PATH is not set
        default_skills_path = Path.home() / ".aworld" / "skills"
        try:
            # Create directory if it doesn't exist
            default_skills_path.mkdir(parents=True, exist_ok=True)
            # Register the default directory
            count = registry.register_source(str(default_skills_path), source_name=str(default_skills_path))
            if count > 0:
                print(f"📚 Registered default skill source: {default_skills_path} ({count} skills)")
            logger.info(f"📚 Registered default skill source: {default_skills_path} ({count} skills)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register default skill source '{default_skills_path}': {e}")
    
    # Register from SKILLS_DIR (legacy, single directory for backward compatibility)
    skills_dir_env = os.getenv(ENV_SKILLS_DIR)
    if skills_dir_env:
        try:
            # Expand ~ in path if present
            skills_dir_path = Path(os.path.expanduser(skills_dir_env)).resolve()
            if skills_dir_path.exists() and skills_dir_path.is_dir():
                count = registry.register_source(str(skills_dir_path), source_name=str(skills_dir_path))
                if count > 0:
                    print(f"📚 Registered skill source from {ENV_SKILLS_DIR}: {skills_dir_path} ({count} skills)")
                logger.info(f"📚 Registered skill source from {ENV_SKILLS_DIR}: {skills_dir_path}")
            else:
                logger.warning(f"⚠️ {ENV_SKILLS_DIR} directory not found: {skills_dir_path}")
        except Exception as e:
            print(f"⚠️ Failed to register skill source from {ENV_SKILLS_DIR}: {e}")
            logger.warning(f"⚠️ Failed to register skill source from {ENV_SKILLS_DIR}: {e}")


def reset_skill_registry() -> None:
    """
    Reset the global SkillRegistry instance.
    
    Useful for testing or when you need to reinitialize the registry.
    
    Example:
        >>> reset_skill_registry()
        >>> registry = get_skill_registry(skills_dir="./new_skills")
    """
    global _global_registry
    _global_registry = None


def register_skill_source(source: str, source_name: Optional[str] = None) -> int:
    """
    Register a skill source (local path or GitHub URL) in the global registry.
    
    Args:
        source: Local path or GitHub URL
        source_name: Optional name for the source
        
    Returns:
        Number of skills loaded
        
    Example:
        >>> register_skill_source("https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering")
    """
    registry = get_skill_registry()
    return registry.register_source(source, source_name=source_name)

