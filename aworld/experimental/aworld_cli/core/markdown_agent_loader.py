"""
Markdown agent loader for scanning and loading agents from markdown files.

This module provides functionality to parse markdown files with YAML front matter
and convert them into LocalAgent instances that can be registered in the agent registry.
"""
import json
import os
import importlib.util
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from aworld.utils.skill_loader import extract_front_matter, collect_skill_docs
from .skill_registry import get_skill_registry
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.config import AgentConfig, ModelConfig
from aworld.logs.util import logger
from aworld.mcp_client.utils import extract_mcp_servers_from_config

from .agent_registry import LocalAgent, LocalAgentRegistry


def _extract_front_matter_with_multiline_json(content_lines: List[str]) -> Tuple[Dict[str, Any], int]:
    """
    Extract YAML-like front matter with support for multiline JSON values.
    
    This is an enhanced version that handles multiline JSON objects like mcp_config.
    
    Args:
        content_lines: The content of the markdown file split into lines
        
    Returns:
        Tuple of (front_matter dict, body_start_index)
    """
    front_matter: Dict[str, Any] = {}
    if not content_lines or content_lines[0].strip() != "---":
        return front_matter, 0
    
    i = 1
    while i < len(content_lines):
        line = content_lines[i].strip()
        
        # Check if we've reached the end of front matter
        if line == "---":
            break
        
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            
            # Check if this is a multiline JSON object (starts with { or [)
            if value.startswith("{") or value.startswith("["):
                # First, check if the value is already a complete JSON on a single line
                brace_count = value.count("{") - value.count("}")
                bracket_count = value.count("[") - value.count("]")
                
                # If JSON is already complete on this line, try to parse it directly
                if brace_count == 0 and bracket_count == 0:
                    try:
                        front_matter[key] = json.loads(value)
                        logger.debug(f"✅ Successfully parsed single-line JSON for {key}")
                        i += 1
                        continue
                    except json.JSONDecodeError:
                        # If parsing fails, treat as multiline and continue
                        pass
                
                # Collect multiline JSON
                json_lines = [value]
                i += 1
                
                # Continue collecting lines until JSON is complete or we hit front matter end
                while i < len(content_lines):
                    next_line_raw = content_lines[i]
                    next_line_stripped = next_line_raw.strip()
                    
                    # Stop if we hit the end of front matter
                    if next_line_stripped == "---":
                        break
                    
                    # Stop if we encounter a new key-value pair (next line starts with a key)
                    # This prevents collecting the next field's content
                    # Only check if JSON is already balanced (should have broken already, but safety check)
                    if brace_count == 0 and bracket_count == 0:
                        # Check if next line looks like a new key-value pair
                        if ":" in next_line_stripped and not next_line_stripped.startswith((" ", "\t", "{", "[", "}", "]", '"', "'")):
                            potential_key = next_line_stripped.split(":", 1)[0].strip()
                            # Valid YAML key: no spaces, alphanumeric with underscores
                            if potential_key and " " not in potential_key and "\t" not in potential_key:
                                if potential_key.replace("_", "").replace("-", "").isalnum():
                                    # This looks like a new key, stop collecting
                                    break
                    
                    json_lines.append(next_line_raw)
                    brace_count += next_line_raw.count("{") - next_line_raw.count("}")
                    bracket_count += next_line_raw.count("[") - next_line_raw.count("]")
                    
                    # Check if JSON is complete (balanced braces/brackets)
                    if brace_count == 0 and bracket_count == 0:
                        i += 1
                        break
                    i += 1
                
                # Try to parse the collected JSON
                json_str = "\n".join(json_lines)
                try:
                    front_matter[key] = json.loads(json_str)
                    logger.debug(f"✅ Successfully parsed multiline JSON for {key}")
                except json.JSONDecodeError as e:
                    logger.warning(f"⚠️ Failed to parse multiline JSON for {key}: {e}, keeping as string")
                    front_matter[key] = json_str
            else:
                # Single line value - try to parse as JSON if it looks like JSON
                if value.startswith("[") or value.startswith("{"):
                    try:
                        front_matter[key] = json.loads(value)
                    except json.JSONDecodeError:
                        front_matter[key] = value
                else:
                    front_matter[key] = value
                i += 1
        else:
            i += 1
    
    # Return the index after the closing ---
    if i < len(content_lines) and content_lines[i].strip() == "---":
        return front_matter, i + 1
    
    return front_matter, i


def _load_mcp_config_from_file(file_path_str: str, base_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Load MCP configuration from a JSON or Python file.
    
    Supports:
    - JSON files (.json): Direct JSON parsing
    - Python files (.py): Executes the file and expects:
      - A dictionary variable named `MCP_CONFIG`, or
      - A function that returns a dictionary
    
    File paths can be:
    - Relative paths (resolved relative to base_dir)
    - Absolute paths
    
    Args:
        file_path_str: Path to the configuration file (relative or absolute)
        base_dir: Base directory for resolving relative paths (usually the markdown file's directory)
        
    Returns:
        Dictionary containing MCP configuration, or None if loading fails
        
    Example:
        >>> # JSON file: mcp.json
        >>> # {"mcpServers": {"server1": {...}}}
        >>> config = _load_mcp_config_from_file("mcp.json", Path("./agents"))
        
        >>> # Python file: mcp_config.py
        >>> # MCP_CONFIG = {"mcpServers": {"server1": {...}}}
        >>> config = _load_mcp_config_from_file("mcp_config.py", Path("./agents"))
    """
    try:
        # Resolve file path (relative to base_dir or absolute)
        if os.path.isabs(file_path_str):
            config_path = Path(file_path_str)
        else:
            config_path = base_dir / file_path_str
        
        if not config_path.exists():
            logger.warning(f"⚠️ MCP config file not found: {config_path}")
            return None
        
        # Load JSON file
        if config_path.suffix.lower() == ".json":
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                logger.info(f"✅ Loaded MCP config from JSON file: {config_path}")
                return config
        
        # Load Python file
        elif config_path.suffix.lower() == ".py":
            # Use importlib to load the Python module
            spec = importlib.util.spec_from_file_location("mcp_config_module", config_path)
            if spec is None or spec.loader is None:
                logger.error(f"❌ Failed to load Python module from {config_path}")
                return None
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Try to get MCP_CONFIG variable or call a function
            if hasattr(module, "MCP_CONFIG"):
                config = module.MCP_CONFIG
                if isinstance(config, dict):
                    logger.info(f"✅ Loaded MCP config from Python file (MCP_CONFIG): {config_path}")
                    return config
                else:
                    logger.warning(f"⚠️ MCP_CONFIG in {config_path} is not a dictionary")
                    return None
            elif hasattr(module, "get_mcp_config"):
                # Try calling a function
                config = module.get_mcp_config()
                if isinstance(config, dict):
                    logger.info(f"✅ Loaded MCP config from Python file (get_mcp_config): {config_path}")
                    return config
                else:
                    logger.warning(f"⚠️ get_mcp_config() in {config_path} did not return a dictionary")
                    return None
            else:
                logger.warning(f"⚠️ Python file {config_path} does not contain MCP_CONFIG variable or get_mcp_config() function")
                return None
        
        else:
            logger.warning(f"⚠️ Unsupported file type for MCP config: {config_path.suffix}. Only .json and .py are supported")
            return None
            
    except json.JSONDecodeError as e:
        logger.error(f"❌ Failed to parse JSON from {config_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to load MCP config from {config_path}: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def parse_markdown_agent(md_file_path: Path) -> Optional[LocalAgent]:
    """
    Parse a markdown file and create a LocalAgent instance.
    
    The markdown file should have YAML front matter with:
    - name: Agent name (required)
    - description: Agent description
    - tool_list: Dictionary of tools to use (optional, legacy format)
    - mcp_servers: List of MCP server names (optional, e.g., ["ms-playwright"])
    - mcp_config: MCP server configuration (optional). Can be:
      - Inline JSON string: '{"mcpServers": {...}}'
      - JSON file path: "mcp.json" (relative to markdown file directory)
      - Python file path: "mcp_config.py" (relative to markdown file directory)
        The Python file should contain either:
        - A variable named `MCP_CONFIG` (dict), or
        - A function `get_mcp_config()` that returns a dict
    - ptc_tools: List of tool names to enable PTC (Programmatic Tool Calling) for (optional, e.g., ["browser_navigate", "browser_snapshot"])
    - skills_path: Skill sources to register in SkillRegistry (optional, semicolon-separated).
      Can be:
      - Local path (relative to markdown file directory or absolute, e.g., "../skills")
      - GitHub URL (e.g., "https://github.com/user/repo" or "https://github.com/user/repo/tree/branch/skills")
      - Multiple sources separated by semicolon (e.g., "https://github.com/user/repo;../skills")
      Note: If not specified, skills will be loaded from the default SkillRegistry which includes:
      - ./skills directory (if exists, registered automatically by get_skill_registry)
      - ../skills directory relative to markdown file (if exists, registered automatically)
    - skill_names: Skill names to use for this agent (optional, semicolon-separated).
      Skills will be retrieved from the global SkillRegistry (includes default sources and skills_path).
      Supports both exact skill names and regex patterns (prefixed with "regex:").
      Example: "pdf;excel;browser" or "pdf;regex:^context-.*" or "regex:.*browser.*"
      Note: If skill_names is not specified, no skills will be loaded for this agent.
    
    The markdown body content will be used as part of the system prompt.
    
    Args:
        md_file_path: Path to the markdown file
        
    Returns:
        LocalAgent instance if parsing succeeds, None otherwise
        
    Example:
        >>> agent = parse_markdown_agent(Path("agents/my_agent.md"))
        >>> if agent:
        ...     LocalAgentRegistry.register(agent)
    """
    try:
        # Read markdown file
        content_lines = md_file_path.read_text(encoding="utf-8").splitlines()
        
        # Extract front matter (use enhanced version for multiline JSON support)
        front_matter, body_start = _extract_front_matter_with_multiline_json(content_lines)
        logger.info(f"✅ Front matter: {json.dumps(front_matter, indent=4)}")
        
        # Get agent name (required)
        agent_name = front_matter.get("name")
        if not agent_name:
            logger.warning(f"⚠️ Markdown file {md_file_path} missing 'name' in front matter, skipping")
            return None
        
        # Get description
        description = front_matter.get("description") or front_matter.get("desc", "")
        
        # Get tool list (legacy support)
        tool_list = front_matter.get("tool_list", {})
        if isinstance(tool_list, str):
            try:
                tool_list = json.loads(tool_list)
            except json.JSONDecodeError:
                logger.warning(f"⚠️ Failed to parse tool_list as JSON in {md_file_path}, using empty dict")
                tool_list = {}
        
        # Extract tool names from tool_list dict
        tool_names = []
        if isinstance(tool_list, dict):
            # tool_list format: {"ms-playwright": [], "other_tool": ["param1", "param2"]}
            tool_names = list(tool_list.keys())
        
        # Get MCP servers (new format)
        mcp_servers = front_matter.get("mcp_servers")
        if isinstance(mcp_servers, str):
            # Try to parse as JSON array
            try:
                mcp_servers = json.loads(mcp_servers)
            except json.JSONDecodeError:
                # Try to parse as comma-separated string
                mcp_servers = [s.strip() for s in mcp_servers.split(",") if s.strip()]
        elif mcp_servers is None:
            mcp_servers = []
        
        # Ensure mcp_servers is a list
        if not isinstance(mcp_servers, list):
            logger.warning(f"⚠️ mcp_servers should be a list in {md_file_path}, converting")
            mcp_servers = [mcp_servers] if mcp_servers else []
        
        # Get MCP config
        mcp_config = front_matter.get("mcp_config")
        if isinstance(mcp_config, str):
            # First, try to parse as inline JSON
            try:
                mcp_config = json.loads(mcp_config)
                logger.debug(f"✅ Parsed mcp_config as inline JSON")
            except json.JSONDecodeError:
                # If not valid JSON, check if it's a file path
                # File paths typically contain .json or .py extension, or look like paths
                if (".json" in mcp_config.lower() or 
                    ".py" in mcp_config.lower() or 
                    "/" in mcp_config or 
                    "\\" in mcp_config):
                    # Try to load from file
                    base_dir = md_file_path.parent
                    loaded_config = _load_mcp_config_from_file(mcp_config, base_dir)
                    if loaded_config is not None:
                        mcp_config = loaded_config
                    else:
                        logger.warning(f"⚠️ Failed to load mcp_config from file '{mcp_config}' in {md_file_path}, using None")
                        mcp_config = None
                else:
                    # Not a file path and not valid JSON, treat as None
                    logger.warning(f"⚠️ mcp_config value '{mcp_config}' is neither valid JSON nor a file path, using None")
        elif mcp_config is None:
            mcp_config = None
        
        # If mcp_servers is empty but mcp_config is available, extract servers from config
        if not mcp_servers and mcp_config and isinstance(mcp_config, dict):
            extracted_servers = extract_mcp_servers_from_config(mcp_config, [])
            if extracted_servers:
                mcp_servers = extracted_servers
                logger.info(f"✅ Auto-extracted mcp_servers from mcp_config: {mcp_servers}")
        
        # Get PTC tools (Programmatic Tool Calling)
        ptc_tools = front_matter.get("ptc_tools")
        if isinstance(ptc_tools, str):
            # Try to parse as JSON array
            try:
                ptc_tools = json.loads(ptc_tools)
            except json.JSONDecodeError:
                # Try to parse as comma-separated string
                ptc_tools = [s.strip() for s in ptc_tools.split(",") if s.strip()]
        elif ptc_tools is None:
            ptc_tools = []
        
        # Ensure ptc_tools is a list
        if not isinstance(ptc_tools, list):
            logger.warning(f"⚠️ ptc_tools should be a list in {md_file_path}, converting")
            ptc_tools = [ptc_tools] if ptc_tools else []
        
        # Get skill registry (will auto-initialize with default ./skills if exists)
        registry = get_skill_registry()
        
        # Always register ../skills directory relative to markdown file (if exists)
        # This is the default skills directory for markdown agents
        default_skills_dir = (md_file_path.parent / "../skills").resolve()
        if default_skills_dir.exists() and default_skills_dir.is_dir():
            try:
                count = registry.register_source(str(default_skills_dir), source_name=str(default_skills_dir))
                if count > 0:
                    print(f"📚 Registered skills directory: {default_skills_dir} ({count} skills)")
                logger.debug(f"📚 Registered default skills directory: {default_skills_dir}")
            except Exception as e:
                # Source might already be registered, that's fine
                logger.debug(f"ℹ️ Default skills directory registration: {default_skills_dir} ({e})")
        
        # Parse skills_path (semicolon-separated) and register to registry
        skills_path = front_matter.get("skills_path")
        if skills_path:
            try:
                # Split by semicolon to get multiple paths
                skill_sources = [s.strip() for s in str(skills_path).split(';') if s.strip()]
                
                for source in skill_sources:
                    try:
                        # Resolve path relative to markdown file directory if it's a local path
                        if 'github.com' in source or source.startswith('git@'):
                            # GitHub URL, use as-is
                            resolved_source = source
                            source_name = source
                        else:
                            # Local path, resolve relative to markdown file directory
                            if os.path.isabs(source):
                                resolved_source = Path(source)
                            else:
                                resolved_source = (md_file_path.parent / source).resolve()
                            
                            resolved_source_str = str(resolved_source)
                            source_name = resolved_source_str
                            resolved_source = resolved_source_str
                        
                        # Register source to registry
                        count = registry.register_source(resolved_source, source_name=source_name)
                        if count > 0:
                            print(f"📚 Registered skill source: {source_name} ({count} skills)")
                        logger.debug(f"📚 Registered skill source: {source_name}")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to register skill source '{source}': {e}")
            except Exception as e:
                logger.error(f"❌ Failed to parse skills_path: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        
        # Parse skill_names (semicolon-separated) and get skills from registry
        # Supports both exact skill names and regex patterns (prefixed with "regex:")
        # If skill_names is not configured, skill_configs will be empty (no skills loaded)
        skill_names_str = front_matter.get("skill_names")
        skill_configs = {}
        if skill_names_str:
            try:
                # Split by semicolon to get multiple skill names or regex patterns
                skill_patterns = [pattern.strip() for pattern in str(skill_names_str).split(';') if pattern.strip()]
                
                # Get all skills from registry
                all_registry_skills = registry.get_all_skills()
                found_skills = []
                missing_skills = []
                
                for pattern in skill_patterns:
                    # Check if this is a regex pattern (prefixed with "regex:")
                    if pattern.startswith("regex:"):
                        # Extract regex pattern (remove "regex:" prefix)
                        regex_pattern = pattern[6:].strip()
                        try:
                            # Use regex to find matching skills
                            matched_skills = registry.get_skills_by_regex(regex_pattern, match_field="name")
                            
                            for skill_name, skill_data in matched_skills.items():
                                # Skip if already added (avoid duplicates)
                                if skill_name not in skill_configs:
                                    # Convert to AgentConfig format
                                    skill_configs[skill_name] = {
                                        "name": skill_data.get("name", skill_name),
                                        "desc": skill_data.get("description", skill_data.get("desc", "")),
                                        "usage": skill_data.get("usage", ""),
                                        "tool_list": skill_data.get("tool_list", {}),
                                        "type": skill_data.get("type", ""),
                                        "active": skill_data.get("active", False)
                                    }
                                    found_skills.append(skill_name)
                        except Exception as regex_error:
                            logger.warning(f"⚠️ Invalid regex pattern '{regex_pattern}': {regex_error}")
                            missing_skills.append(pattern)
                    else:
                        # Exact skill name match
                        if pattern in all_registry_skills:
                            skill_data = all_registry_skills[pattern]
                            # Convert to AgentConfig format
                            skill_configs[pattern] = {
                                "name": skill_data.get("name", pattern),
                                "desc": skill_data.get("description", skill_data.get("desc", "")),
                                "usage": skill_data.get("usage", ""),
                                "tool_list": skill_data.get("tool_list", {}),
                                "type": skill_data.get("type", ""),
                                "active": skill_data.get("active", False)
                            }
                            found_skills.append(pattern)
                        else:
                            missing_skills.append(pattern)
                
                if found_skills:
                    skill_list_str = ", ".join(found_skills)
                    print(f"📚 Loaded {len(found_skills)} skill(s) for agent '{agent_name}': {skill_list_str}")
                    logger.info(f"✅ Loaded {len(found_skills)} skill(s) from registry: {found_skills}")
                if missing_skills:
                    missing_list_str = ", ".join(missing_skills)
                    print(f"⚠️ Skill(s) not found for agent '{agent_name}': {missing_list_str}")
                    logger.warning(f"⚠️ Skill(s) not found in registry: {missing_skills}. Available skills: {list(all_registry_skills.keys())}")
            except Exception as e:
                logger.error(f"❌ Failed to get skills from registry: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        
        # Get markdown body content as prompt
        body_lines = content_lines[body_start:]
        markdown_content = "\n".join(body_lines).strip()
        
        # Build system prompt from description and markdown content
        system_prompt_parts = []
        if description:
            system_prompt_parts.append(description)
        if markdown_content:
            system_prompt_parts.append(markdown_content)
        
        # Combine description and markdown content into system_prompt
        if system_prompt_parts:
            system_prompt = "\n\n".join(system_prompt_parts)
        else:
            system_prompt = "You are a helpful AI agent."
        

        
        # Create a factory function that builds the Swarm
        def build_swarm() -> Swarm:
            # Create agent configuration
            agent_config = AgentConfig(
                llm_config=ModelConfig(
                    llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
                    llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
                    llm_api_key=os.environ.get("LLM_API_KEY"),
                    llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
                    llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7"))
                )
            )
            """Build Swarm from markdown agent definition."""
            agent = Agent(
                name=agent_name,
                desc=description,
                conf=agent_config,
                system_prompt=system_prompt,
                tool_names=tool_names if tool_names else None,
                mcp_servers=mcp_servers if mcp_servers else None,
                mcp_config=mcp_config,
                ptc_tools=ptc_tools if ptc_tools else [],
                skill_configs=skill_configs if skill_configs else None
            )
            return Swarm(agent)
        
        # Create LocalAgent
        local_agent = LocalAgent(
            name=agent_name,
            desc=description,
            swarm=build_swarm,
            metadata={
                "source": "markdown",
                "file_path": str(md_file_path),
                "tool_list": tool_list,
                "mcp_servers": mcp_servers,
                "mcp_config": mcp_config,
                "ptc_tools": ptc_tools,
                "skills_path": skills_path,
                "skill_names": skill_names_str,
                "skill_configs": skill_configs
            }
        )
        
        logger.info(f"✅ Parsed markdown agent: {agent_name} from {md_file_path.name}")
        return local_agent
        
    except Exception as e:
        logger.error(f"❌ Failed to parse markdown agent from {md_file_path}: {e}")
        print(f"failed load markdown agent: {md_file_path}, error: {e}, please see log for more details")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def load_markdown_agents(agents_dir: Path) -> List[LocalAgent]:
    """
    Scan directory for markdown files and load them as agents.
    
    Args:
        agents_dir: Directory to scan for markdown files
        
    Returns:
        List of LocalAgent instances loaded from markdown files
        
    Example:
        >>> agents = load_markdown_agents(Path("./agents"))
        >>> for agent in agents:
        ...     LocalAgentRegistry.register(agent)
    """
    agents = []
    
    if not agents_dir.exists():
        logger.warning(f"⚠️ Agents directory not found: {agents_dir}")
        return agents
    
    # Find all markdown files recursively, excluding private files
    markdown_files = [
        f for f in agents_dir.rglob("*.md")
        if not f.name.startswith("_") and not f.name.startswith(".")
    ]
    
    if not markdown_files:
        logger.debug(f"ℹ️ No markdown files found in {agents_dir}")
        return agents
    
    logger.info(f"🔍 Found {len(markdown_files)} markdown file(s)")
    
    for md_file in markdown_files:
        try:
            agent = parse_markdown_agent(md_file)
            if agent:
                agents.append(agent)
        except Exception as e:
            print(f"failed load markdown agent: {md_file}, error: {e}, please see log for more details")
            logger.error(f"❌ Error processing {md_file}: {e}")
            continue
    
    return agents


__all__ = ["parse_markdown_agent", "load_markdown_agents"]

