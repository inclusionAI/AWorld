"""
Base runtime for CLI protocols.
Provides common functionality for both local and remote runtimes.
"""
import json
from pathlib import Path
from typing import List, Optional, Any
from aworld.logs.util import logger
from ..console import AWorldCLI
from ..models import AgentInfo
from ..executors import AgentExecutor


class BaseCliRuntime:
    """
    Base runtime for CLI protocols that interact with agents.
    Provides common functionality for agent selection and chat session management.
    
    To create a new runtime, inherit from this class and implement:
    - _load_agents(): Load available agents
    - _create_executor(): Create executor for selected agent
    - _get_source_type(): Return source type string
    - _get_source_location(): Return source location string
    
    Example:
        class CustomRuntime(BaseCliRuntime):
            async def _load_agents(self) -> List[AgentInfo]:
                # Load agents from custom source
                pass
            
            async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
                # Create custom executor
                pass
            
            def _get_source_type(self) -> str:
                return "CUSTOM"
            
            def _get_source_location(self) -> str:
                return "custom://location"
    """
    
    def __init__(self, agent_name: Optional[str] = None):
        """
        Initialize base runtime.

        Args:
            agent_name: The name of the agent to interact with. If None, user will be prompted to select.
        """
        self.agent_name = agent_name
        self._running = False
        self.cli = AWorldCLI()
        self._scheduler = None  # Cron scheduler instance
        self._notification_center = None  # Cron notification center
        self._plugins = []
        self._plugin_registry = None
        self._plugin_hooks = {}
        self._plugin_contexts = {}
        self._plugin_state_store = None
        from .hud_snapshot import HudSnapshotStore

        self._hud_snapshot_store = HudSnapshotStore()
    
    async def start(self) -> None:
        """Start the CLI interaction loop."""
        self._running = True
        # self.cli.display_welcome()

        # Start cron scheduler
        await self._start_scheduler()

        # Initialize framework plugin surfaces needed by the CLI session loop.
        self._initialize_plugin_framework()

        # Load agents (implemented by subclasses)
        agents = await self._load_agents()
        
        if not agents:
            self.cli.console.print("[red]❌ No agents available.[/red]")
            return
        
        executor = None
        try:
            while self._running:
                # Select agent
                selected_agent = await self._select_agent(agents)
                if not selected_agent:
                    return
                
                # Start chat session
                executor = await self._create_executor(selected_agent)
                if not executor:
                    self.cli.console.print("[red]❌ Failed to create executor for agent.[/red]")
                    continue

                # Store runtime reference in executor for notification access
                if executor:
                    executor._base_runtime = self

                result = await self.cli.run_chat_session(
                    selected_agent.name,
                    executor.chat,
                    available_agents=agents,
                    executor_instance=executor
                )
                
                # Handle session result
                if result is False:
                    break
                elif result is True:
                    # User wants to switch agent (show list)
                    if len(agents) == 1:
                        self.cli.console.print("[yellow]ℹ️ Only one agent available. Cannot switch.[/yellow]")
                        continue
                    selected_agent = None
                    continue
                elif isinstance(result, str):
                    # User wants to switch to specific agent
                    self.agent_name = result
                    continue
        finally:
            # Always run cleanup on exit (user exit, KeyboardInterrupt, etc.)
            if executor is not None and hasattr(executor, "cleanup_resources") and callable(getattr(executor, "cleanup_resources")):
                try:
                    await executor.cleanup_resources()
                except Exception:
                    pass
    
    async def stop(self) -> None:
        """Stop the CLI loop."""
        self._running = False

        # Stop cron scheduler
        await self._stop_scheduler()

    def _initialize_plugin_framework(self) -> None:
        plugin_dirs = getattr(self, "plugin_dirs", None) or []
        if not plugin_dirs:
            self._plugins = []
            self._plugin_registry = None
            self._plugin_hooks = {}
            self._plugin_contexts = {}
            self._plugin_state_store = None
            try:
                from ..plugin_framework.commands import sync_plugin_commands

                sync_plugin_commands([])
            except Exception:
                pass
            return

        try:
            from ..plugin_framework.context import CONTEXT_PHASES, load_plugin_contexts
            from ..plugin_framework.commands import sync_plugin_commands
            from ..plugin_framework.discovery import discover_plugins
            from ..plugin_framework.hooks import load_plugin_hooks
            from ..plugin_framework.registry import PluginCapabilityRegistry
            from ..plugin_framework.state import PluginStateStore

            plugin_roots = [Path(path) for path in plugin_dirs]
            self._plugins = discover_plugins(plugin_roots)
            self._plugin_registry = PluginCapabilityRegistry(self._plugins)
            self._plugin_hooks = load_plugin_hooks(self._plugins)
            self._plugin_contexts = load_plugin_contexts(self._plugins)
            for phase in CONTEXT_PHASES:
                self._plugin_contexts.setdefault(phase, ())
            self._plugin_state_store = PluginStateStore(Path.cwd() / ".aworld" / "plugin_state")
            sync_plugin_commands(self._plugins)
        except Exception as exc:
            logger.warning(f"Failed to initialize plugin framework surfaces: {exc}")
            self._plugins = []
            self._plugin_registry = None
            self._plugin_hooks = {}
            self._plugin_contexts = {}
            self._plugin_state_store = None
            try:
                from ..plugin_framework.commands import sync_plugin_commands

                sync_plugin_commands([])
            except Exception:
                pass

    def refresh_plugin_framework(self) -> None:
        if hasattr(self, "_get_plugin_dirs"):
            try:
                self.plugin_dirs = self._get_plugin_dirs()
            except Exception as exc:
                logger.warning(f"Failed to resolve plugin directories during refresh: {exc}")
        self._initialize_plugin_framework()

    def get_plugin_hooks(self, hook_point: str) -> list[Any]:
        normalized = (hook_point or "").strip().lower()
        return list(self._plugin_hooks.get(normalized, ()))

    def get_context_phase_handlers(self, phase: str) -> list[Any]:
        normalized = (phase or "").strip().lower()
        return list(self._plugin_contexts.get(normalized, ()))

    def active_plugin_capabilities(self) -> tuple[str, ...]:
        if self._plugin_registry is None:
            return tuple()
        return self._plugin_registry.capabilities()

    def get_active_plugins(self, capability: str) -> list[Any]:
        if self._plugin_registry is None:
            return []
        return list(self._plugin_registry.get_plugins(capability))

    def get_active_entrypoints(self, capability: str) -> list[Any]:
        if self._plugin_registry is None:
            return []
        return list(self._plugin_registry.get_entrypoints(capability))

    def build_hud_context(
        self,
        agent_name: str = "Aworld",
        mode: str = "Chat",
        workspace_name: str | None = None,
        git_branch: str | None = None,
    ) -> dict[str, Any]:
        unread_count = 0
        if self._notification_center and hasattr(self._notification_center, "get_unread_count"):
            try:
                unread_count = int(self._notification_center.get_unread_count())
            except Exception:
                unread_count = 0

        context: dict[str, Any] = {
            "workspace": {"name": workspace_name or Path.cwd().name, "path": str(Path.cwd())},
            "session": {"agent": agent_name, "mode": mode},
            "notifications": {"cron_unread": unread_count},
            "vcs": {"branch": git_branch or "n/a"},
            "plugins": {
                "active_count": len(self._plugins),
                "active_ids": [
                    plugin.manifest.plugin_id
                    for plugin in self._plugins
                    if getattr(plugin, "manifest", None) is not None
                ],
            },
        }

        for bucket, payload in self.get_hud_snapshot().items():
            context.setdefault(bucket, {})
            context[bucket].update(payload)

        return context

    def update_hud_snapshot(self, **sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return self._hud_snapshot_store.update(**sections)

    def settle_hud_snapshot(self, task_status: str = "idle") -> dict[str, dict[str, Any]]:
        return self._hud_snapshot_store.settle(task_status=task_status)

    def get_hud_snapshot(self) -> dict[str, dict[str, Any]]:
        return self._hud_snapshot_store.snapshot()

    def get_hud_lines(self, context: dict[str, Any]) -> list[Any]:
        from ..plugin_framework.hud import collect_hud_lines

        return collect_hud_lines(self._plugins, context)

    def build_plugin_hook_state(
        self,
        plugin_id: str,
        scope: str,
        executor_instance: Any = None,
    ) -> dict[str, Any]:
        context = getattr(executor_instance, "context", None) if executor_instance else None
        workspace_path = getattr(context, "workspace_path", None)
        session_id = getattr(executor_instance, "session_id", None)
        if not session_id and context is not None:
            session_id = getattr(context, "session_id", None)

        state: dict[str, Any] = {}
        state_path = self._resolve_plugin_state_path(
            plugin_id=plugin_id,
            scope=scope,
            session_id=session_id,
            workspace_path=workspace_path,
        )
        if state_path is not None and state_path.exists():
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    state.update(payload)
            except Exception as exc:
                logger.warning(f"Failed to read plugin state for {plugin_id}: {exc}")

        if session_id:
            state.setdefault("session_id", session_id)
        if workspace_path:
            state.setdefault("workspace_path", workspace_path)
        if context is not None and getattr(context, "task_id", None):
            state.setdefault("task_id", context.task_id)

        return state

    def _resolve_plugin_state_path(
        self,
        plugin_id: str,
        scope: str,
        session_id: Optional[str],
        workspace_path: Optional[str],
    ) -> Optional[Path]:
        if self._plugin_state_store is None:
            return None
        if scope == "global":
            return self._plugin_state_store.global_state(plugin_id)
        if scope == "session" and session_id:
            return self._plugin_state_store.session_state(plugin_id, session_id)
        if workspace_path:
            return self._plugin_state_store.workspace_state(plugin_id, workspace_path)
        return None

    async def _start_scheduler(self) -> None:
        """Start cron scheduler with notification center."""
        try:
            from aworld.core.scheduler import get_scheduler
            from aworld_cli.core.agent_registry import LocalAgentRegistry
            from aworld_cli.runtime.cron_notifications import CronNotificationCenter

            async def resolve_swarm(agent_name: str):
                local_agent = LocalAgentRegistry.get_agent(agent_name)
                if not local_agent:
                    return None
                return await local_agent.get_swarm()

            # Create notification center
            self._notification_center = CronNotificationCenter()

            # Get scheduler and wire notification sink
            self._scheduler = get_scheduler()
            self._scheduler.notification_sink = self._notification_center.publish
            if hasattr(self._scheduler.executor, "set_swarm_resolver"):
                self._scheduler.executor.set_swarm_resolver(resolve_swarm)

            await self._scheduler.start()
            # Silent startup - no user-facing message
        except Exception as e:
            # Scheduler startup failure should not block CLI
            logger.warning(f"Failed to start cron scheduler: {e}")

    async def _stop_scheduler(self) -> None:
        """Stop cron scheduler."""
        if self._scheduler:
            try:
                await self._scheduler.stop()
            except Exception as e:
                logger.warning(f"Failed to stop cron scheduler: {e}")

    async def _drain_notifications(self, job_id: Optional[str] = None) -> List[Any]:
        """
        Drain pending notifications from notification center.

        Args:
            job_id: Optional job ID filter to drain only matching notifications

        Returns:
            List of CronNotification objects (empty list if no center or error)

        Note:
            This is a non-blocking operation. Gracefully returns empty list
            on any error to prevent crashes in chat loop.
        """
        if not self._notification_center:
            return []

        try:
            return await self._notification_center.drain(job_id=job_id)
        except Exception as e:
            logger.warning(f"Failed to drain notifications: {e}")
            return []

    async def _load_agents(self) -> List[AgentInfo]:
        """
        Load available agents.
        Must be implemented by subclasses.
        
        Returns:
            List of available agents
        """
        raise NotImplementedError("Subclasses must implement _load_agents")
    
    async def _select_agent(self, agents: List[AgentInfo]) -> Optional[AgentInfo]:
        """
        Select an agent from the list.
        
        Args:
            agents: List of available agents
            
        Returns:
            Selected agent or None if selection cancelled
        """
        selected_agent = None
        
        # If agent_name was provided, try to find it
        if self.agent_name:
            for agent in agents:
                if agent.name == self.agent_name:
                    selected_agent = agent
                    break
            if not selected_agent:
                self.cli.console.print(f"[red]❌ Agent '{self.agent_name}' not found.[/red]")
            # Clear it so next loop we select
            self.agent_name = None
        
        if not selected_agent:
            if len(agents) == 1:
                self.cli.display_agents(agents, source_type=self._get_source_type(), source_location=self._get_source_location())
                selected_agent = agents[0]
                self.cli.console.print(f"[green]🎯 Using default agent: [bold]{selected_agent.name}[/bold][/green]")
            else:
                selected_agent = self.cli.select_agent(agents, source_type=self._get_source_type(), source_location=self._get_source_location())
        
        return selected_agent
    
    async def _create_executor(self, agent: AgentInfo) -> Optional[AgentExecutor]:
        """
        Create an executor for the selected agent.
        Must be implemented by subclasses.
        
        Args:
            agent: Selected agent
            
        Returns:
            Agent executor or None if creation failed
        """
        raise NotImplementedError("Subclasses must implement _create_executor")
    
    def _get_source_type(self) -> str:
        """
        Get the source type for display purposes.
        Must be implemented by subclasses.
        
        Returns:
            Source type string (e.g., "LOCAL", "REMOTE")
        """
        raise NotImplementedError("Subclasses must implement _get_source_type")
    
    def _get_source_location(self) -> str:
        """
        Get the source location for display purposes.
        Must be implemented by subclasses.
        
        Returns:
            Source location string (e.g., directory path or URL)
        """
        raise NotImplementedError("Subclasses must implement _get_source_location")
