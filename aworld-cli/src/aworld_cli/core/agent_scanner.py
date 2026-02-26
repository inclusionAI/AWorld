# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import re
import sys
import traceback
from pathlib import Path
from threading import RLock
from typing import Optional, Dict, List

from aworld.agents.llm_agent import Agent
from aworld.core.context.amni import DirArtifact
from aworld_cli.core.scanner import Scanner
from aworld.logs.util import logger
from aworld.output.artifact import ArtifactAttachment

class AgentCodeScanner(Scanner):
    """Scanner for Python code agents with @agent decorator."""
    
    def __init__(self, context):
        Scanner.__init__(self, context)
        self._lock = RLock()


    def _has_agent_decorator_fast(self, file_path: Path) -> bool:
        """Check if a Python file contains @agent decorator."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if '@agent' in line or '@agent(' in line:
                        return True
            return False
        except Exception:
            return False

    def _matches_file(self, attachment: ArtifactAttachment, name: str, suffix: str, base_path: str = None) -> bool:
        """Check if an attachment matches. For .py files, also checks for @agent decorator."""
        if suffix != ".py":
            return super()._matches_file(attachment, name, suffix, base_path)
        
        if not super()._matches_file(attachment, name, suffix, base_path):
            return False
        
        if base_path:
            file_path = Path(base_path) / attachment.path
            if file_path.exists():
                return self._has_agent_decorator_fast(file_path)
        
        return False

    def _scan_files_by_suffix(self, suffix: str) -> List[str]:
        """Scan files by suffix. For .py files, only includes files with @agent decorator."""
        return super()._scan_files_by_suffix(suffix)

    async def get_agent_from_base_path(
        self,
        base_path: str,
        agent_name: str,
        storage_type: str = "local",
        oss_config: Optional[Dict[str, str]] = None
    ) -> Optional[Agent]:
        """Load Python agent from base_path."""
        try:
            import importlib.util
            
            if storage_type == 'oss' and oss_config:
                dir_artifact = DirArtifact.with_oss_repository(
                    access_key_id=oss_config.get('access_key_id'),
                    access_key_secret=oss_config.get('access_key_secret'),
                    endpoint=oss_config.get('endpoint'),
                    bucket_name=oss_config.get('bucket_name'),
                    base_path=base_path
                )
            else:
                dir_artifact = DirArtifact.with_local_repository(base_path)
            
            attachment = await Scanner.resolve_resource_from_artifact(
                dir_artifact=dir_artifact,
                name=agent_name,
                suffix=".py"
            )
            
            if not attachment:
                logger.warning(
                    "load_agent: no .py resource found for agent_name=%s under base_path=%s",
                    agent_name, base_path,
                )
                return None
            
            file_path = Path(dir_artifact.base_path) / attachment.path
            
            if not file_path.exists():
                logger.error("load_agent: Python agent file not found: %s (base_path=%s)", file_path, base_path)
                return None
            
            if not self._has_agent_decorator_fast(file_path):
                logger.warning("load_agent: file has no @agent decorator: %s", file_path)
                return None
            
            module_name = file_path.stem
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                logger.error("load_agent: could not create spec for %s", file_path)
                return None
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            from aworld_cli.core.agent_registry import LocalAgentRegistry
            local_agent = LocalAgentRegistry.get_agent(agent_name)

            if not local_agent:
                logger.warning(
                    "load_agent: LocalAgentRegistry.get_agent(%s) returned None after loading module from %s. "
                    "Ensure the @agent decorator name matches '%s' and the module registered the agent.",
                    agent_name, file_path, agent_name,
                )
                return None

            swarm = await local_agent.get_swarm()
            if not swarm or not swarm.agents:
                logger.warning(
                    "load_agent: local_agent '%s' has no swarm or empty swarm.agents (path=%s)",
                    agent_name, getattr(local_agent, "path", None),
                )
                return None
            agent_id, agent = next(iter(swarm.agents.items()))
            return agent
            
        except Exception as e:
            logger.error(f"Failed to load Python agent from base_path {base_path}: {e} {traceback.format_exc()}")
            return None


    async def list_as_source(self) -> List[str]:
        """List all available resources by scanning .py files (with @agent decorator)."""
        return self._scan_files_by_suffix(".py")

    async def load_as_source(self, name: str) -> Optional[str]:
        """Load resource as source content."""
        return await self._load_as_source_by_suffix(name=name, suffix=".py")

    async def list_desc(self) -> List[tuple]:
        """List all available resources with their descriptions and paths."""
        resources = self._scan_files_by_suffix(".py")
        resources_with_desc = []
        
        from aworld_cli.core.agent_registry import LocalAgentRegistry
        import os
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
        local_agents_dict = {}
        local_agents_by_dir = {}
        try:
            local_agents = LocalAgentRegistry.list_agents()
            for local_agent in local_agents:
                if local_agent.name:
                    local_agents_dict[local_agent.name] = local_agent
                    if local_agent.register_dir:
                        dir_name = os.path.basename(local_agent.register_dir.rstrip('/'))
                        if dir_name:
                            local_agents_by_dir[dir_name] = local_agent
                    resource_dir = os.path.join(base_path, local_agent.name)
                    if os.path.exists(resource_dir):
                        local_agents_by_dir[local_agent.name] = local_agent
        except Exception:
            pass
        
        for name in resources:
            try:
                desc = None
                path = None
                local_agent = None
                
                if name in local_agents_dict:
                    local_agent = local_agents_dict[name]
                    desc = local_agent.desc or "No description"
                    path = local_agent.path or "Unknown path"
                elif name in local_agents_by_dir:
                    local_agent = local_agents_by_dir[name]
                    desc = local_agent.desc or "No description"
                    path = local_agent.path or "Unknown path"
                
                if not desc:
                    agent = await self.load_agent(agent_name=name)
                    if agent:
                        desc = agent.desc() or "No description"
                    else:
                        desc = "No description"
                
                if not path:
                    path = "Unknown path"
                
                resources_with_desc.append((name, desc, path))
            except Exception as e:
                logger.warning(f"Failed to get description for {name}: {e} {traceback.format_exc()}")
                resources_with_desc.append((name, "No description", "Unknown path"))
        
        return resources_with_desc

    async def load_agent(self, agent_name: str) -> Optional[Agent]:
        base_path = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
        storage_type = self._get_storage_type()
        oss_config = self._get_oss_config()

        return await self.get_agent_from_base_path(
            base_path=base_path,
            agent_name=agent_name,
            storage_type=storage_type,
            oss_config=oss_config
        )



class AgentScanner(Scanner):
    """Unified scanner that combines both DSL (markdown) and Code (Python) agents."""
    
    def __init__(self, context):
        Scanner.__init__(self, context)
        self._lock = RLock()
        
        agents_path_env = os.environ.get('AGENTS_PATH', '~/.aworld/agents')
        base_paths = [os.path.expanduser(p.strip()) for p in agents_path_env.split(':') if p.strip()]
        
        if not base_paths:
            base_paths = [os.path.expanduser('~/.aworld/agents')]
        
        self._base_paths = base_paths
        
        self._code_registries = []
        for base_path in base_paths:
            original_path = os.environ.get('AGENTS_PATH')
            try:
                os.environ['AGENTS_PATH'] = base_path
                code_registry = AgentCodeScanner(context)
                self._code_registries.append((base_path, code_registry))
            finally:
                if original_path:
                    os.environ['AGENTS_PATH'] = original_path
                else:
                    os.environ['AGENTS_PATH'] = base_paths[0]
        
        for base_path in base_paths:
            if base_path not in sys.path:
                sys.path.insert(0, base_path)
        
        logger.debug(f"AGENTS_PATH: {':'.join(base_paths)}")


    async def list_as_source(self) -> List[str]:
        """List all available resources from all configured directories."""
        all_resources = set()
        for base_path, code_registry in self._code_registries:
            try:
                original_path = os.environ.get('AGENTS_PATH')
                try:
                    os.environ['AGENTS_PATH'] = base_path
                    resources = await code_registry.list_as_source()
                    all_resources.update(resources)
                finally:
                    if original_path:
                        os.environ['AGENTS_PATH'] = original_path
                    else:
                        os.environ['AGENTS_PATH'] = base_path
            except Exception as e:
                logger.warning(f"Failed to scan agents from {base_path}: {e}")
        
        return sorted(list(all_resources))

    async def list_desc(self) -> List[tuple]:
        """List all available resources with their descriptions and paths."""
        all_descriptions = {}
        for base_path, code_registry in self._code_registries:
            try:
                original_path = os.environ.get('AGENTS_PATH')
                try:
                    os.environ['AGENTS_PATH'] = base_path
                    descriptions = await code_registry.list_desc()
                    for name, desc, path in descriptions:
                        if name not in all_descriptions:
                            all_descriptions[name] = (name, desc, path)
                finally:
                    if original_path:
                        os.environ['AGENTS_PATH'] = original_path
                    else:
                        os.environ['AGENTS_PATH'] = base_path
            except Exception as e:
                logger.warning(f"Failed to get descriptions from {base_path}: {e}")
        
        return sorted(all_descriptions.values())

    async def load_agent(self, agent_name: str) -> Optional[Agent]:
        """Load agent by trying all configured directories."""
        for base_path, code_registry in self._code_registries:
            try:
                original_path = os.environ.get('AGENTS_PATH')
                try:
                    os.environ['AGENTS_PATH'] = base_path
                    agent = await code_registry.load_agent(agent_name)
                    if agent:
                        return agent
                finally:
                    if original_path:
                        os.environ['AGENTS_PATH'] = original_path
                    else:
                        os.environ['AGENTS_PATH'] = base_path
            except Exception as e:
                logger.warning(f"Failed to load agent {agent_name} from {base_path}: {e}")
                continue

        return None

    async def load_as_source(self, name: str) -> Optional[str]:
        """Load resource as source content from all configured directories."""
        for base_path, code_registry in self._code_registries:
            try:
                original_path = os.environ.get('AGENTS_PATH')
                try:
                    os.environ['AGENTS_PATH'] = base_path
                    content = await code_registry.load_as_source(name)
                    if content:
                        return content
                finally:
                    if original_path:
                        os.environ['AGENTS_PATH'] = original_path
                    else:
                        os.environ['AGENTS_PATH'] = base_path
            except Exception as e:
                logger.warning(f"Failed to load source {name} from {base_path}: {e}")
                continue
        
        return None


class DefaultContext:
    """Default context for AgentScanner when no context is provided."""

global_agent_registry = AgentScanner(DefaultContext())
