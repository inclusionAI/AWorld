import asyncio
import inspect
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Callable, Any, Union, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style as PromptToolkitStyle
from rich import box
from rich.color import Color
from rich.panel import Panel
from rich.prompt import Prompt
from rich.style import Style
from rich.table import Table
from rich.text import Text

from aworld.logs.util import logger
from ._globals import console
from .core.command_system import CommandRegistry, CommandContext
from .models import AgentInfo
from .steering.observability import log_queued_steering_event
from .user_input import UserInputHandler


# ... existing imports ...

# Notification polling configuration
NOTIFICATION_POLL_INTERVAL = 2.0  # Seconds (1-3s latency target)
_ESC_INTERRUPT_SENTINEL = "__aworld_cli_interrupt__"


class CronAwareCompleter(Completer):
    """Wrap the static slash completer with dynamic cron job ID suggestions."""

    _CRON_JOB_ID_PREFIXES = (
        "/cron show ",
        "/cron remove ",
        "/cron rm ",
        "/cron delete ",
        "/cron run ",
        "/cron enable ",
        "/cron disable ",
        "/cron inbox ",
    )

    def __init__(
        self,
        words: List[str],
        meta_dict: dict[str, str],
        runtime: Any = None,
        event_loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self._base_completer = WordCompleter(
            words,
            ignore_case=True,
            sentence=True,
            meta_dict=meta_dict,
        )
        self._runtime = runtime
        self._event_loop = event_loop

    def get_completions(self, document, complete_event):
        yield from self._base_completer.get_completions(document, complete_event)

        prefix = self._match_cron_job_prefix(document.text_before_cursor)
        if not prefix:
            return

        partial_job_id = document.text_before_cursor[len(prefix):]
        if partial_job_id.strip() and " " in partial_job_id.strip():
            return

        for job in sorted(self._list_cron_jobs(), key=self._job_sort_key):
            if not self._matches_job(partial_job_id, job):
                continue
            yield Completion(
                text=job.id,
                start_position=-len(partial_job_id),
                display_meta=self._job_meta(job),
            )

    def _match_cron_job_prefix(self, text: str) -> Optional[str]:
        for prefix in self._CRON_JOB_ID_PREFIXES:
            if text.startswith(prefix):
                return prefix
        return None

    def _list_cron_jobs(self) -> List[Any]:
        runtime = self._runtime
        scheduler = getattr(runtime, "_scheduler", None) if runtime else None
        if scheduler is None or not hasattr(scheduler, "list_jobs"):
            return []

        coro = scheduler.list_jobs(enabled_only=False)

        if self._event_loop and self._event_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(coro, self._event_loop)
                return future.result(timeout=0.5)
            except Exception:
                return []

        try:
            return asyncio.run(coro)
        except Exception:
            return []

    def _matches_job(self, partial_job_id: str, job: Any) -> bool:
        query = (partial_job_id or "").strip().lower()
        if not query:
            return True

        job_id = getattr(job, "id", "")
        job_name = getattr(job, "name", "")
        return query in job_id.lower() or query in job_name.lower()

    def _job_meta(self, job: Any) -> str:
        name = getattr(job, "name", "")
        enabled = getattr(job, "enabled", True)
        state = getattr(job, "state", None)
        last_status = getattr(state, "last_status", None) if state else None
        status_label = "Enabled" if enabled else "Disabled"
        last_label = self._format_last_status(last_status)
        return f"Name: {name or '(unnamed)'} | State: {status_label} | Last: {last_label}"

    def _job_sort_key(self, job: Any) -> tuple[int, int, str, str]:
        enabled_rank = 0 if getattr(job, "enabled", True) else 1
        state = getattr(job, "state", None)
        last_status = getattr(state, "last_status", None) if state else None
        status_rank = self._status_priority(last_status)
        name = (getattr(job, "name", "") or "").lower()
        job_id = (getattr(job, "id", "") or "").lower()
        return enabled_rank, status_rank, name, job_id

    def _format_last_status(self, last_status: Optional[str]) -> str:
        normalized = (last_status or "").strip().lower()
        if not normalized:
            return "Never"

        mapping = {
            "ok": "OK",
            "error": "Error",
            "timeout": "Timeout",
        }
        return mapping.get(normalized, normalized.capitalize())

    def _status_priority(self, last_status: Optional[str]) -> int:
        normalized = (last_status or "").strip().lower()
        ranking = {
            "error": 0,
            "timeout": 1,
            "ok": 2,
            "": 3,
        }
        return ranking.get(normalized, 4)


class AWorldCLI:
    def __init__(self):
        self.console = console
        self.user_input = UserInputHandler(console)
        # self.team_handler = InteractiveTeamHandler(console)

        # Notification polling state
        self._is_agent_executing = False
        self._notification_poll_task = None
        self._notification_stop_event = None
        self._notification_drain_lock = asyncio.Lock()
        self._notification_center_listener = self._handle_notification_center_change
        self._subscribed_notification_center = None
        self._active_prompt_session = None
        self._current_executor_task = None
        self._toolbar_workspace_name = self._detect_workspace_name()
        self._toolbar_git_branch = self._detect_git_branch()
        self._pending_skill_overrides: list[str] = []

    def _detect_workspace_name(self) -> str:
        """Detect the current workspace name for status-bar display."""
        workspace_name = Path.cwd().name.strip()
        return workspace_name or "workspace"

    def _detect_git_branch(self) -> str:
        """Best-effort detection of the current git branch for status-bar display."""
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
                check=False,
                timeout=0.5,
            )
        except Exception:
            return "n/a"

        if result.returncode != 0:
            return "n/a"

        branch = (result.stdout or "").strip()
        return branch or "detached"

    def _build_status_bar_text(
        self,
        runtime,
        agent_name: str = "Aworld",
        mode: str = "Chat",
        max_width: int | None = 160,
    ) -> str:
        """
        Build plain-text status-bar content.

        The bar is intentionally always present to avoid layout jumps.
        """
        if not self._should_render_status_bar(runtime):
            return ""

        if runtime and hasattr(runtime, "build_hud_context"):
            hud_context = runtime.build_hud_context(
                agent_name=agent_name,
                mode=mode,
                workspace_name=self._toolbar_workspace_name,
                git_branch=self._toolbar_git_branch,
            )
        else:
            notification_center = getattr(runtime, "_notification_center", None)
            if not notification_center:
                unread_count = -1
            else:
                unread_count = notification_center.get_unread_count()
            hud_context = {
                "workspace": {"name": self._toolbar_workspace_name},
                "session": {"agent": agent_name, "mode": mode},
                "notifications": {"cron_unread": unread_count},
                "vcs": {"branch": self._toolbar_git_branch},
            }

        try:
            plugin_lines = runtime.get_hud_lines(hud_context) if runtime and hasattr(runtime, "get_hud_lines") else []
        except Exception as exc:
            logger.warning(f"Failed to render HUD plugin lines: {exc}")
            plugin_lines = []

        if not plugin_lines:
            fallback_segments = self._fallback_status_segments(hud_context, agent_name, mode)
            return self._render_status_line(fallback_segments, max_width)

        rendered_lines = []
        for line in plugin_lines[:2]:
            segments = list(getattr(line, "segments", ()) or ())
            if not segments:
                text = getattr(line, "text", "")
                if text:
                    segments = [str(text)]
            if not segments:
                continue
            section = getattr(line, "section", "")
            rendered_lines.append(self._render_status_line(segments, max_width, section=section))

        if not rendered_lines:
            fallback_segments = self._fallback_status_segments(hud_context, agent_name, mode)
            return self._render_status_line(fallback_segments, max_width)

        return "\n".join(rendered_lines)

    def _should_render_status_bar(self, runtime) -> bool:
        if runtime is None:
            return False

        if hasattr(runtime, "active_plugin_capabilities"):
            try:
                return "hud" in tuple(runtime.active_plugin_capabilities())
            except Exception:
                return True

        return True

    def _fallback_status_segments(self, hud_context: dict[str, Any], agent_name: str, mode: str) -> list[str]:
        unread_count = hud_context.get("notifications", {}).get("cron_unread", -1)
        if unread_count < 0:
            cron_status = "Cron: offline"
        elif unread_count > 0:
            cron_status = f"Cron: {unread_count} unread"
        else:
            cron_status = "Cron: clear"

        return [
            f"Agent: {hud_context.get('session', {}).get('agent', agent_name)}",
            f"Mode: {hud_context.get('session', {}).get('mode', mode)}",
            cron_status,
            f"Workspace: {hud_context.get('workspace', {}).get('name', self._toolbar_workspace_name)}",
            f"Branch: {hud_context.get('vcs', {}).get('branch', self._toolbar_git_branch)}",
        ]

    def _reduce_segments(
        self,
        segments: list[str],
        max_width: int | None,
        priority_labels: set[str] | None = None,
    ) -> list[str]:
        if max_width is None:
            return list(segments)
        kept = list(segments)
        priority_labels = priority_labels or set()
        while len(kept) > 1 and len(" | ".join(kept)) > max_width:
            if len(kept) <= 2:
                kept.pop()
                continue
            removable_index = None
            for index in range(len(kept) - 1, -1, -1):
                segment = kept[index]
                label = segment.split(":", 1)[0].strip().lower()
                if label not in priority_labels:
                    removable_index = index
                    break
            if removable_index is None:
                removable_index = len(kept) - 1
            kept.pop(removable_index)
        return kept

    def _render_status_line(self, segments: list[str], max_width: int | None, section: str | None = None) -> str:
        priority_labels = None
        if section == "activity":
            priority_labels = {"task", "ctx"}
        reduced = self._reduce_segments(segments, max_width, priority_labels=priority_labels)
        text = " | ".join(reduced)
        if max_width is not None and max_width > 3 and len(text) > max_width:
            return text[: max_width - 3].rstrip() + "..."
        return text

    def _build_status_bar(self, runtime, agent_name: str = "Aworld", mode: str = "Chat") -> HTML:
        """Build a styled prompt-toolkit status bar inspired by segmented system prompts."""
        columns = shutil.get_terminal_size(fallback=(160, 0)).columns
        max_width = columns if columns > 0 else None
        text = self._build_status_bar_text(runtime, agent_name=agent_name, mode=mode, max_width=max_width)
        lines = text.splitlines() or [text]
        divider_style = "#4f5877"

        def _render_line(line_text: str) -> str:
            segments = [segment.strip() for segment in line_text.split("|")]
            has_unread = any("unread" in segment for segment in segments)
            segment_styles = [
                "#84c7c6",
                "#d8def5",
                "#f2c14e" if has_unread else "#8ed081",
                "#b8c0da",
                "#a88bd8",
                "#8ea0c4",
            ]

            parts = []
            for index, segment in enumerate(segments):
                fg = segment_styles[index] if index < len(segment_styles) else "#d8def5"
                escaped = (
                    segment.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                parts.append(f"<style fg='{fg}'> {escaped} </style>")
                if index < len(segments) - 1:
                    parts.append(f"<style fg='{divider_style}'> | </style>")
            if max_width is not None:
                rendered_width = sum(len(segment) + 2 for segment in segments) + max(0, len(segments) - 1) * 3
                pad_width = max(0, max_width - rendered_width)
                if pad_width:
                    parts.append(" " * pad_width)
            return "".join(parts)

        rendered_lines = []
        if max_width is not None:
            rendered_lines.append(f"<style fg='{divider_style}'>{'─' * max_width}</style>")
        rendered_lines.extend(_render_line(line) for line in lines if line)
        return HTML("\n".join(rendered_lines))

    def _build_prompt_kwargs(self, runtime, agent_name: str = "Aworld", mode: str = "Chat") -> dict[str, Any]:
        prompt_kwargs: dict[str, Any] = {"bottom_toolbar": None}
        if runtime and self._should_render_status_bar(runtime):
            prompt_kwargs["bottom_toolbar"] = lambda: self._build_status_bar(
                runtime,
                agent_name=agent_name,
                mode=mode,
            )
            prompt_kwargs["style"] = PromptToolkitStyle.from_dict(
                {
                    "bottom-toolbar": "fg:#d8def5 bg:default noreverse",
                }
            )
            prompt_kwargs["refresh_interval"] = 0.1
        return prompt_kwargs

    def _build_completion_entries(
        self,
        agent_names: Optional[List[str]] = None,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> tuple[list[str], dict[str, str]]:
        """
        Build slash-command completion phrases and descriptions for prompt_toolkit.

        Returns:
            Tuple of (completion phrases, phrase -> description)
        """
        agent_names = agent_names or []

        builtin_cmds = [
            "/agents", "/skills", "/new", "/restore", "/latest",
            "/exit", "/switch", "/cost", "/cost -all", "/compact",
            "/team",
            "/memory", "/memory view", "/memory reload", "/memory status",
        ]
        builtin_meta = {
            "/agents": "List available agents",
            "/skills": "List available skills",
            "/new": "Create a new session",
            "/restore": "Restore to a previous session",
            "/latest": "Restore to the latest session",
            "/exit": "Exit chat",
            "/switch": "Switch to another agent",
            "/cost": "View query history (current session)",
            "/cost -all": "View global history (all sessions)",
            "/compact": "Run context compression",
            "/memory": "Edit AWORLD.md project context",
            "/memory view": "View current memory content",
            "/memory reload": "Reload memory from file",
            "/memory status": "Show memory system status",
            "/team": "Agent team management commands",
            "exit": "Exit chat",
        }

        words = set(builtin_cmds)
        meta_dict = builtin_meta.copy()
        for phrase, description in self._skill_command_completion_entries(
            agent_name=agent_name,
            executor_instance=executor_instance,
        ).items():
            words.add(phrase)
            meta_dict[phrase] = description

        for cmd in CommandRegistry.list_commands():
            base_phrase = f"/{cmd.name}"
            words.add(base_phrase)
            meta_dict[base_phrase] = cmd.description
            for phrase, description in cmd.completion_items.items():
                words.add(phrase)
                meta_dict[phrase] = description

        for phrase, skill_name in self._generated_skill_alias_map(
            agent_name=agent_name,
            executor_instance=executor_instance,
        ).items():
            words.add(phrase)
            meta_dict[phrase] = f"Force skill on next task: {skill_name}"

        for agent_name in agent_names:
            phrase = f"/switch {agent_name}"
            words.add(phrase)
            meta_dict[phrase] = f"Switch to agent: {agent_name}"

        words.add("exit")

        return sorted(words), meta_dict

    def _build_session_completer(
        self,
        agent_names: Optional[List[str]] = None,
        agent_name: str | None = None,
        executor_instance: Any = None,
        runtime: Any = None,
        event_loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> Completer:
        """Build the prompt completer with static slash entries plus dynamic cron job IDs."""
        all_words, meta_dict = self._build_completion_entries(
            agent_names=agent_names,
            agent_name=agent_name,
            executor_instance=executor_instance,
        )
        return CronAwareCompleter(
            all_words,
            meta_dict,
            runtime=runtime,
            event_loop=event_loop,
        )

    def _create_prompt_session(
        self,
        completer: Completer,
        *,
        on_escape: Callable[[], Any] | None = None,
    ) -> PromptSession:
        history_path = Path.home() / ".aworld" / "cli_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        key_bindings = None
        if on_escape is not None:
            key_bindings = KeyBindings()

            @key_bindings.add("escape")
            def _interrupt(event) -> None:
                try:
                    on_escape()
                finally:
                    event.app.exit(result=_ESC_INTERRUPT_SENTINEL)

        session = PromptSession(
            completer=completer,
            complete_while_typing=True,
            history=FileHistory(str(history_path)),
            key_bindings=key_bindings,
        )
        self._active_prompt_session = session
        return session

    def _ensure_prompt_session(self, session: PromptSession | None, completer: Completer) -> PromptSession:
        if session is not None and session is self._active_prompt_session:
            return session
        return self._create_prompt_session(completer)

    def _handle_runtime_plugin_capability_refresh(self, previous_capabilities: tuple[str, ...], runtime) -> None:
        had_hud = "hud" in tuple(previous_capabilities or ())
        has_hud = False
        if runtime is not None and hasattr(runtime, "active_plugin_capabilities"):
            try:
                has_hud = "hud" in tuple(runtime.active_plugin_capabilities())
            except Exception:
                has_hud = had_hud

        if had_hud != has_hud:
            self._active_prompt_session = None

    def _get_gradient_text(self, text: str, start_color: str, end_color: str) -> Text:
        """Create a Text object with a horizontal gradient."""
        result = Text()
        lines = text.strip().split("\n")
        max_width = max(len(line) for line in lines)
        
        c1 = Color.parse(start_color).get_truecolor()
        c2 = Color.parse(end_color).get_truecolor()
        
        for line in lines:
            for i, char in enumerate(line):
                if char.isspace():
                    result.append(char)
                    continue
                    
                ratio = i / max_width if max_width > 0 else 0
                r = int(c1[0] + (c2[0] - c1[0]) * ratio)
                g = int(c1[1] + (c2[1] - c1[1]) * ratio)
                b = int(c1[2] + (c2[2] - c1[2]) * ratio)
                color_hex = f"#{r:02x}{g:02x}{b:02x}"
                result.append(char, style=f"bold {color_hex}")
            result.append("\n")
        return result

    def display_welcome(self):
        # "ANSI Shadow" font style for AWORLD - cleaner and modern
        ascii_art = """
 █████╗ ██╗    ██╗ ██████╗ ██████╗ ██╗     ██████╗ 
██╔══██╗██║    ██║██╔═══██╗██╔══██╗██║     ██╔══██╗
███████║██║ █╗ ██║██║   ██║██████╔╝██║     ██║  ██║
██╔══██║██║███╗██║██║   ██║██╔══██╗██║     ██║  ██║
██║  ██║╚███╔███╔╝╚██████╔╝██║  ██║███████╗██████╔╝
╚═╝  ╚═╝ ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝ 
"""
        # Single color Blue (#4285F4)
        banner = Text(ascii_art, style="bold #4285F4")
        
        # Display the Logo standalone (without a box), left aligned
        self.console.print(banner)
        
        # Config source
        from .core.config import get_config
        source_type, source_path = get_config().get_config_source(".env")
        if source_type == "local":
            self.console.print(f"[dim]📁 Using local config: {source_path}[/dim]")
        else:
            self.console.print(f"[dim]🌐 Using global config: {source_path}[/dim]")

        # Subtitle / Version
        subtitle = Text("🤖 Interact with your agents directly from the terminal", style="italic #875fff")
        version = Text("\n v0.1.0", style="dim white")
        
        self.console.print(Text.assemble(subtitle, version))
        self.console.print() # Padding

    async def _interactive_config_editor(self):
        """Interactive configuration editor. First menu: choose config type (e.g. model)."""
        from .core.config import get_config
        from rich.panel import Panel

        config = get_config()
        current_config = config.load_config()
        
        self.console.print(Panel(
            "[bold cyan]Configuration Editor[/bold cyan]\n\n"
            f"Config file: [dim]{config.get_config_path()}[/dim]",
            title="Config",
            border_style="cyan"
        ))
        
        # First menu: select configuration type (Back/cancel uses largest number)
        config_types = [
            ("1", "Model configuration", self._edit_models_config),
            ("2", "Skills configuration", self._edit_skills_config),
            ("3", "Output configuration", self._edit_output_config),
            ("4", "Filesystem configuration", self._edit_filesystem_config),
        ]
        back_key = str(len(config_types) + 1)
        self.console.print("\n[bold]Select configuration type:[/bold]")
        for key, label, _ in config_types:
            self.console.print(f"  {key}. {label}")
        self.console.print(f"  {back_key}. Back (cancel)")
        
        choice = Prompt.ask("\nChoice", default="1")
        if choice == back_key:
            self.console.print("[dim]Cancelled.[/dim]")
            return
        
        handler = None
        for key, label, fn in config_types:
            if choice == key:
                handler = fn
                break
        if not handler:
            self.console.print("[red]Invalid selection.[/red]")
            return
        
        await handler(config, current_config)
    
    async def _edit_models_config(self, config, current_config: dict):
        """Edit models.default (flat: provider, api_key, model, base_url)."""
        from rich.table import Table

        if 'models' not in current_config:
            current_config['models'] = {}
        if 'default' not in current_config['models']:
            current_config['models']['default'] = {}
        default_cfg = current_config['models']['default']
        # Migrate legacy nested format to flat (take openai first, then anthropic, gemini)
        if not default_cfg.get('api_key') and isinstance(default_cfg, dict):
            for p in ('openai', 'anthropic', 'gemini'):
                if isinstance(default_cfg.get(p), dict) and default_cfg[p].get('api_key'):
                    default_cfg['api_key'] = default_cfg[p].get('api_key', '')
                    default_cfg['model'] = default_cfg[p].get('model', '')
                    default_cfg['base_url'] = default_cfg[p].get('base_url', '')
                    for k in ('openai', 'anthropic', 'gemini'):
                        default_cfg.pop(k, None)
                    break

        self.console.print("\n[bold]Default LLM configuration[/bold]")
        self.console.print("  [dim]Provider: openai (default)[/dim]\n")
        current_api_key = default_cfg.get('api_key', '')
        if current_api_key:
            masked_key = current_api_key[:8] + "..." if len(current_api_key) > 8 else "***"
            self.console.print(f"  [dim]Current API key: {masked_key}[/dim]")
        api_key = Prompt.ask("  OPENAI_API_KEY", default=current_api_key, password=True)
        if api_key:
            default_cfg['api_key'] = api_key

        current_model = default_cfg.get('model', '')
        self.console.print("  [dim]e.g. gpt-4, claude-3-opus · Enter to leave empty[/dim]")
        model = Prompt.ask("  Model name", default=current_model)
        if model:
            default_cfg['model'] = model
        else:
            default_cfg.pop('model', None)

        current_base_url = default_cfg.get('base_url', '')
        self.console.print("  [dim]Optional · Enter to leave empty[/dim]")
        base_url = Prompt.ask("  Base URL", default=current_base_url)
        if base_url:
            default_cfg['base_url'] = base_url
        else:
            default_cfg.pop('base_url', None)

        # Diffusion (models.diffusion -> DIFFUSION_* for diffusion agent)
        self.console.print("\n[bold]Diffusion configuration[/bold] [dim](optional, for diffusion agent)[/dim]")
        self.console.print("  [dim]Leave empty to use Media LLM or default LLM config above[/dim]\n")
        if 'diffusion' not in current_config['models']:
            # Migrate from legacy models.diffusion
            current_config['models']['diffusion'] = current_config['models'].get('diffusion') or {}
            current_config['models'].pop('diffusion', None)
        diff_cfg = current_config['models']['diffusion']

        current_diff_api_key = diff_cfg.get('api_key', '')
        if current_diff_api_key:
            masked = current_diff_api_key[:8] + "..." if len(current_diff_api_key) > 8 else "***"
            self.console.print(f"  [dim]Current DIFFUSION_API_KEY: {masked}[/dim]")
        diff_api_key = Prompt.ask("  DIFFUSION_API_KEY", default=current_diff_api_key, password=True)
        if diff_api_key:
            diff_cfg['api_key'] = diff_api_key
        else:
            diff_cfg.pop('api_key', None)

        current_diff_model = diff_cfg.get('model', '')
        self.console.print("  [dim]e.g. claude-3-5-sonnet-20241022 · Enter to inherit from Media/default[/dim]")
        diff_model = Prompt.ask("  DIFFUSION_MODEL_NAME", default=current_diff_model)
        if diff_model:
            diff_cfg['model'] = diff_model
        else:
            diff_cfg.pop('model', None)

        current_diff_base_url = diff_cfg.get('base_url', '')
        diff_base_url = Prompt.ask("  DIFFUSION_BASE_URL", default=current_diff_base_url)
        if diff_base_url:
            diff_cfg['base_url'] = diff_base_url
        else:
            diff_cfg.pop('base_url', None)

        current_diff_provider = diff_cfg.get('provider', 'video')
        diff_provider = Prompt.ask("  DIFFUSION_PROVIDER", default=current_diff_provider)
        if diff_provider:
            diff_cfg['provider'] = diff_provider
        else:
            diff_cfg.pop('provider', None)

        current_diff_temp = diff_cfg.get('temperature', 0.1)
        diff_temp = Prompt.ask("  DIFFUSION_TEMPERATURE", default=str(current_diff_temp))
        if diff_temp:
            try:
                diff_cfg['temperature'] = float(diff_temp)
            except ValueError:
                diff_cfg.pop('temperature', None)
        else:
            diff_cfg.pop('temperature', None)

        if not diff_cfg:
            current_config['models'].pop('diffusion', None)

        # Avatar (models.avatar -> AVATAR_* for avatar agent)
        self.console.print("\n[bold]Avatar configuration[/bold] [dim](optional, for avatar agent)[/dim]")
        self.console.print("  [dim]Leave empty to inherit from Diffusion/Media/default config above[/dim]\n")
        if 'avatar' not in current_config['models']:
            current_config['models']['avatar'] = {}
        avatar_cfg = current_config['models']['avatar']

        current_avatar_api_key = avatar_cfg.get('api_key', '')
        if current_avatar_api_key:
            masked = current_avatar_api_key[:8] + "..." if len(current_avatar_api_key) > 8 else "***"
            self.console.print(f"  [dim]Current AVATAR_API_KEY: {masked}[/dim]")
        avatar_api_key = Prompt.ask("  AVATAR_API_KEY", default=current_avatar_api_key, password=True)
        if avatar_api_key:
            avatar_cfg['api_key'] = avatar_api_key
        else:
            avatar_cfg.pop('api_key', None)

        current_avatar_model = avatar_cfg.get('model', '')
        self.console.print("  [dim]e.g. kling-avatar-v1 · Enter to inherit from Diffusion/default[/dim]")
        avatar_model = Prompt.ask("  AVATAR_MODEL_NAME", default=current_avatar_model)
        if avatar_model:
            avatar_cfg['model'] = avatar_model
        else:
            avatar_cfg.pop('model', None)

        current_avatar_base_url = avatar_cfg.get('base_url', '')
        avatar_base_url = Prompt.ask("  AVATAR_BASE_URL", default=current_avatar_base_url)
        if avatar_base_url:
            avatar_cfg['base_url'] = avatar_base_url
        else:
            avatar_cfg.pop('base_url', None)

        current_avatar_provider = avatar_cfg.get('provider', 'kling_avatar')
        avatar_provider = Prompt.ask("  AVATAR_PROVIDER", default=current_avatar_provider)
        if avatar_provider:
            avatar_cfg['provider'] = avatar_provider
        else:
            avatar_cfg.pop('provider', None)

        current_avatar_submit_ep = avatar_cfg.get('submit_endpoint', '')
        avatar_submit_ep = Prompt.ask("  AVATAR_SUBMIT_ENDPOINT", default=current_avatar_submit_ep)
        if avatar_submit_ep:
            avatar_cfg['submit_endpoint'] = avatar_submit_ep
        else:
            avatar_cfg.pop('submit_endpoint', None)

        current_avatar_status_ep = avatar_cfg.get('status_endpoint', '')
        avatar_status_ep = Prompt.ask("  AVATAR_STATUS_ENDPOINT", default=current_avatar_status_ep)
        if avatar_status_ep:
            avatar_cfg['status_endpoint'] = avatar_status_ep
        else:
            avatar_cfg.pop('status_endpoint', None)

        current_avatar_temp = avatar_cfg.get('temperature', 0.1)
        avatar_temp = Prompt.ask("  AVATAR_TEMPERATURE", default=str(current_avatar_temp))
        if avatar_temp:
            try:
                avatar_cfg['temperature'] = float(avatar_temp)
            except ValueError:
                avatar_cfg.pop('temperature', None)
        else:
            avatar_cfg.pop('temperature', None)

        if not avatar_cfg:
            current_config['models'].pop('avatar', None)

        # Audio (models.audio -> AUDIO_* for audio agent)
        self.console.print("\n[bold]Audio configuration[/bold] [dim](optional, for audio agent)[/dim]")
        self.console.print("  [dim]Leave empty to use Media LLM or default LLM config above[/dim]\n")
        if 'audio' not in current_config['models']:
            current_config['models']['audio'] = {}
        audio_cfg = current_config['models']['audio']

        current_audio_api_key = audio_cfg.get('api_key', '')
        if current_audio_api_key:
            masked = current_audio_api_key[:8] + "..." if len(current_audio_api_key) > 8 else "***"
            self.console.print(f"  [dim]Current AUDIO_API_KEY: {masked}[/dim]")
        audio_api_key = Prompt.ask("  AUDIO_API_KEY", default=current_audio_api_key, password=True)
        if audio_api_key:
            audio_cfg['api_key'] = audio_api_key
        else:
            audio_cfg.pop('api_key', None)

        current_audio_model = audio_cfg.get('model', '')
        self.console.print("  [dim]e.g. claude-3-5-sonnet-20241022 · Enter to inherit from Media/default[/dim]")
        audio_model = Prompt.ask("  AUDIO_MODEL_NAME", default=current_audio_model)
        if audio_model:
            audio_cfg['model'] = audio_model
        else:
            audio_cfg.pop('model', None)

        current_audio_base_url = audio_cfg.get('base_url', '')
        audio_base_url = Prompt.ask("  AUDIO_BASE_URL", default=current_audio_base_url)
        if audio_base_url:
            audio_cfg['base_url'] = audio_base_url
        else:
            audio_cfg.pop('base_url', None)

        current_audio_provider = audio_cfg.get('provider', 'openai')
        audio_provider = Prompt.ask("  AUDIO_PROVIDER", default=current_audio_provider)
        if audio_provider:
            audio_cfg['provider'] = audio_provider
        else:
            audio_cfg.pop('provider', None)

        current_audio_temp = audio_cfg.get('temperature', 0.1)
        audio_temp = Prompt.ask("  AUDIO_TEMPERATURE", default=str(current_audio_temp))
        if audio_temp:
            try:
                audio_cfg['temperature'] = float(audio_temp)
            except ValueError:
                audio_cfg.pop('temperature', None)
        else:
            audio_cfg.pop('temperature', None)

        if not audio_cfg:
            current_config['models'].pop('audio', None)

        legacy_image_cfg = current_config['models'].get('image')
        if 'text_to_image' not in current_config['models'] and isinstance(legacy_image_cfg, dict):
            current_config['models']['text_to_image'] = dict(legacy_image_cfg)
        current_config['models'].pop('image', None)

        def edit_image_model_config(section_key: str, title: str, env_prefix: str):
            self.console.print(f"\n[bold]{title}[/bold] [dim](optional, for image agent)[/dim]")
            self.console.print("  [dim]Leave empty to use default LLM config above[/dim]\n")
            if section_key not in current_config['models']:
                current_config['models'][section_key] = {}
            image_cfg = current_config['models'][section_key]

            current_api_key = image_cfg.get('api_key', '')
            if current_api_key:
                masked = current_api_key[:8] + "..." if len(current_api_key) > 8 else "***"
                self.console.print(f"  [dim]Current {env_prefix}_API_KEY: {masked}[/dim]")
            image_api_key = Prompt.ask(f"  {env_prefix}_API_KEY", default=current_api_key, password=True)
            if image_api_key:
                image_cfg['api_key'] = image_api_key
            else:
                image_cfg.pop('api_key', None)

            current_model = image_cfg.get('model', '')
            self.console.print("  [dim]e.g. qwen-image · Enter to inherit from default[/dim]")
            image_model = Prompt.ask(f"  {env_prefix}_MODEL_NAME", default=current_model)
            if image_model:
                image_cfg['model'] = image_model
            else:
                image_cfg.pop('model', None)

            current_base_url = image_cfg.get('base_url', '')
            image_base_url = Prompt.ask(f"  {env_prefix}_BASE_URL", default=current_base_url)
            if image_base_url:
                image_cfg['base_url'] = image_base_url
            else:
                image_cfg.pop('base_url', None)

            current_provider = image_cfg.get('provider', 'image')
            image_provider = Prompt.ask(f"  {env_prefix}_PROVIDER", default=current_provider)
            if image_provider:
                image_cfg['provider'] = image_provider
            else:
                image_cfg.pop('provider', None)

            current_temp = image_cfg.get('temperature', 0.1)
            image_temp = Prompt.ask(f"  {env_prefix}_TEMPERATURE", default=str(current_temp))
            if image_temp:
                try:
                    image_cfg['temperature'] = float(image_temp)
                except ValueError:
                    image_cfg.pop('temperature', None)
            else:
                image_cfg.pop('temperature', None)

            if not image_cfg:
                current_config['models'].pop(section_key, None)

        edit_image_model_config('text_to_image', "Text-to-image configuration", "TEXT_TO_IMAGE")
        edit_image_model_config('image_to_image', "Image-to-image configuration", "IMAGE_TO_IMAGE")

        config.save_config(current_config)
        self.console.print(f"\n[green]✅ Configuration saved to {config.get_config_path()}[/green]")
        table = Table(title="Default LLM Configuration", box=box.ROUNDED)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        for key, value in default_cfg.items():
            if key == 'provider':
                continue  # Not exposed, defaults to openai
            if key == 'api_key':
                masked_value = value[:8] + "..." if len(str(value)) > 8 else "***"
                table.add_row(key, masked_value)
            else:
                table.add_row(key, str(value))
        self.console.print()
        self.console.print(table)
        if current_config['models'].get('diffusion'):
            diff_table = Table(title="Diffusion Configuration (DIFFUSION_*)", box=box.ROUNDED)
            diff_table.add_column("Setting", style="cyan")
            diff_table.add_column("Value", style="green")
            for key, value in current_config['models']['diffusion'].items():
                if key == 'api_key':
                    masked_value = value[:8] + "..." if len(str(value)) > 8 else "***"
                    diff_table.add_row(key, masked_value)
                else:
                    diff_table.add_row(key, str(value))
            self.console.print()
            self.console.print(diff_table)

        if current_config['models'].get('audio'):
            audio_table = Table(title="Audio Configuration (AUDIO_*)", box=box.ROUNDED)
            audio_table.add_column("Setting", style="cyan")
            audio_table.add_column("Value", style="green")
            for key, value in current_config['models']['audio'].items():
                if key == 'api_key':
                    masked_value = value[:8] + "..." if len(str(value)) > 8 else "***"
                    audio_table.add_row(key, masked_value)
                else:
                    audio_table.add_row(key, str(value))
            self.console.print()
            self.console.print(audio_table)

        if current_config['models'].get('text_to_image'):
            image_table = Table(title="Text-to-image Configuration (TEXT_TO_IMAGE_*)", box=box.ROUNDED)
            image_table.add_column("Setting", style="cyan")
            image_table.add_column("Value", style="green")
            for key, value in current_config['models']['text_to_image'].items():
                if key == 'api_key':
                    masked_value = value[:8] + "..." if len(str(value)) > 8 else "***"
                    image_table.add_row(key, masked_value)
                else:
                    image_table.add_row(key, str(value))
            self.console.print()
            self.console.print(image_table)

        if current_config['models'].get('image_to_image'):
            edit_table = Table(title="Image-to-image Configuration (IMAGE_TO_IMAGE_*)", box=box.ROUNDED)
            edit_table.add_column("Setting", style="cyan")
            edit_table.add_column("Value", style="green")
            for key, value in current_config['models']['image_to_image'].items():
                if key == 'api_key':
                    masked_value = value[:8] + "..." if len(str(value)) > 8 else "***"
                    edit_table.add_row(key, masked_value)
                else:
                    edit_table.add_row(key, str(value))
            self.console.print()
            self.console.print(edit_table)

    async def _edit_skills_config(self, config, current_config: dict):
        """Edit skills section of config (global SKILLS_PATH and per-agent XXX_SKILLS_PATH)."""
        default_skills_path = str(Path.home() / ".aworld" / "skills")
        if 'skills' not in current_config:
            current_config['skills'] = {}

        skills_cfg = current_config['skills']
        print('skills_cfg: ', skills_cfg)
        self.console.print("\n[bold]Skills paths:[/bold]")
        self.console.print("  [dim]Paths are relative to home or absolute. Use semicolon (;) to separate multiple paths. Enter to keep, '-' to clear.[/dim]\n")

        # Global SKILLS_PATH
        current = skills_cfg.get('default_skills_path', '')
        val = Prompt.ask("  SKILLS_PATH (default)", default=current or default_skills_path)
        v = val.strip() if val else ''
        if v and v != '-':
            skills_cfg['default_skills_path'] = v
        elif v == '-' or (not v and current):
            skills_cfg.pop('default_skills_path', None)

        # Per-agent paths (same default as SKILLS_PATH)
        for label, key in [
            ("EVALUATOR_SKILLS_PATH (evaluator)", "evaluator_skills_path"),
            ("MEDIA_SKILLS_PATH (media)", "media_skills_path"),
            ("AWORLD_SKILLS_PATH (aworld)", "aworld_skills_path"),
            ("DEVELOPER_SKILLS_PATH (developer)", "developer_skills_path"),
        ]:
            current = skills_cfg.get(key, '')
            val = Prompt.ask(f"  {label}", default=current or default_skills_path)
            v = val.strip() if val else ''
            if v and v != '-':
                skills_cfg[key] = v
            elif v == '-' or (not v and current):
                skills_cfg.pop(key, None)

        if not skills_cfg:
            current_config.pop('skills', None)
        else:
            current_config['skills'] = skills_cfg

        config.save_config(current_config)
        self.console.print(f"\n[green]✅ Configuration saved to {config.get_config_path()}[/green]")
        table = Table(title="Skills Configuration", box=box.ROUNDED)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        for k, v in (current_config.get('skills') or {}).items():
            table.add_row(k, str(v)[:60] + ("..." if len(str(v)) > 60 else ""))
        if current_config.get('skills'):
            self.console.print()
            self.console.print(table)

    async def _edit_output_config(self, config, current_config: dict):
        """Edit output section: stream (STREAM), no_truncate (NO_TRUNCATE), limit_tokens (LIMIT_TOKENS)."""
        from .core.config import (
            apply_stream_env,
            resolve_limit_tokens_value,
            resolve_no_truncate_value,
            resolve_stream_value,
        )

        if 'output' not in current_config:
            current_config['output'] = {}

        out = current_config['output']

        self.console.print("\n[bold]Output configuration[/bold]")
        self.console.print("  [dim]Stream: enable streaming display. No truncate: show full tool/output without folding. Limit tokens: max context tokens (e.g. 128000).[/dim]\n")

        stream_str = 'true' if resolve_stream_value(current_config) == '1' else 'false'
        stream_choice = Prompt.ask("  STREAM (true/false)", default=stream_str)
        if str(stream_choice).lower() in ('true', '1', 'yes'):
            out['stream'] = True
            os.environ['STREAM'] = '1'
        else:
            out['stream'] = False
            os.environ['STREAM'] = '0'

        no_truncate_val = resolve_no_truncate_value(current_config)
        if no_truncate_val is None:
            no_truncate_val = (os.environ.get('NO_TRUNCATE') or '').strip().lower()
        no_truncate_str = 'true' if no_truncate_val in ('1', 'true', 'yes') else 'false'
        no_truncate_choice = Prompt.ask("  NO_TRUNCATE (true/false)", default=no_truncate_str)
        if str(no_truncate_choice).lower() in ('true', '1', 'yes'):
            out['no_truncate'] = True
            os.environ['NO_TRUNCATE'] = '1'
        else:
            out['no_truncate'] = False
            os.environ['NO_TRUNCATE'] = '0'

        limit_tokens_val = resolve_limit_tokens_value(current_config) or (os.environ.get('LIMIT_TOKENS') or '').strip()
        limit_tokens_choice = Prompt.ask("  LIMIT_TOKENS (max context tokens, e.g. 128000; empty to unset)", default=limit_tokens_val or "")
        if limit_tokens_choice.strip():
            try:
                n = int(limit_tokens_choice.strip())
                if n > 0:
                    out['limit_tokens'] = n
                    os.environ['LIMIT_TOKENS'] = str(n)
                else:
                    out.pop('limit_tokens', None)
                    os.environ.pop('LIMIT_TOKENS', None)
            except ValueError:
                out.pop('limit_tokens', None)
                os.environ.pop('LIMIT_TOKENS', None)
        else:
            out.pop('limit_tokens', None)
            os.environ.pop('LIMIT_TOKENS', None)

        apply_stream_env(current_config)
        config.save_config(current_config)
        self.console.print(f"\n[green]✅ Configuration saved to {config.get_config_path()}[/green]")
        table = Table(title="Output Configuration", box=box.ROUNDED)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("stream", str(out.get('stream', '')))
        table.add_row("no_truncate", str(out.get('no_truncate', '')))
        table.add_row("limit_tokens", str(out.get('limit_tokens', '')) or "(unset)")
        self.console.print()
        self.console.print(table)

    async def _edit_filesystem_config(self, config, current_config: dict):
        """Edit filesystem section: artifact_directory (ARTIFACT_DIRECTORY env)."""
        if 'filesystem' not in current_config:
            current_config['filesystem'] = {}

        fs_cfg = current_config['filesystem']
        default_cwd = str(Path.cwd())
        current = fs_cfg.get('artifact_directory') or fs_cfg.get('working_directory', '')
        if not current and os.environ.get('ARTIFACT_DIRECTORY'):
            current = os.environ.get('ARTIFACT_DIRECTORY', '')

        self.console.print("\n[bold]Filesystem configuration[/bold]")
        self.console.print("  [dim]artifact_directory: Base path for agent operations (shell, files). Overrides ARTIFACT_DIRECTORY env. Empty = use current directory. Enter to keep, '-' to clear.[/dim]\n")

        val = Prompt.ask("  ARTIFACT_DIRECTORY (working directory path)", default=current or default_cwd)
        v = val.strip() if val else ''
        if v and v != '-':
            fs_cfg['artifact_directory'] = v
            fs_cfg.pop('working_directory', None)  # migrate from old key
            os.environ['ARTIFACT_DIRECTORY'] = v
        elif v == '-' or (not v and current):
            fs_cfg.pop('artifact_directory', None)
            fs_cfg.pop('working_directory', None)
            os.environ.pop('ARTIFACT_DIRECTORY', None)

        if not fs_cfg:
            current_config.pop('filesystem', None)
        else:
            current_config['filesystem'] = fs_cfg

        config.save_config(current_config)
        self.console.print(f"\n[green]✅ Configuration saved to {config.get_config_path()}[/green]")
        table = Table(title="Filesystem Configuration", box=box.ROUNDED)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        for k, v in (current_config.get('filesystem') or {}).items():
            table.add_row(k, str(v)[:60] + ("..." if len(str(v)) > 60 else ""))
        if current_config.get('filesystem'):
            self.console.print()
            self.console.print(table)

    def display_agents(self, agents: List[AgentInfo], source_type: str = "LOCAL", source_location: str = ""):
        """
        Display available agents in a table.
        
        Args:
            agents: List of agents to display
            source_type: Source type (LOCAL, REMOTE, etc.) - used as fallback if agent doesn't have source_type
            source_location: Source location (directory path or URL) - used as fallback if agent doesn't have source_location
        """
        if not agents:
            self.console.print(f"[red]No agents available ({source_type}: {source_location}).[/red]")
            return

        from pathlib import Path
        table = Table(title="Available Agents", box=box.ROUNDED)
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        _addr_max = 48
        table.add_column("Address", style="dim", no_wrap=False, max_width=_addr_max)

        for agent in agents:
            desc = getattr(agent, "desc", "No description") or "No description"
            # Always use agent's own source_location if it exists and is valid
            # Fallback to provided parameters only if agent doesn't have this attribute
            agent_source_location = getattr(agent, "source_location", None)

            # Use agent's source_location if it exists and is valid, otherwise use fallback
            if agent_source_location and agent_source_location.strip() != "":
                # Use agent's own source_location
                pass
            else:
                # Use fallback
                agent_source_location = source_location

            if not agent_source_location or agent_source_location.strip() == "":
                addr_cell = Text("—", style="dim")
            else:
                address = agent_source_location.strip()
                p = Path(address)
                link_target = p.parent if p.suffix else p
                try:
                    link_url = link_target.resolve().as_uri()
                except (OSError, RuntimeError):
                    link_url = ""
                if link_url and len(address) > _addr_max:
                    addr_display = address[: _addr_max - 3] + "..."
                    addr_cell = Text(addr_display, style=Style(dim=True, link=link_url))
                elif link_url:
                    addr_cell = Text(address, style=Style(dim=True, link=link_url))
                else:
                    addr_display = address[: _addr_max - 3] + "..." if len(address) > _addr_max else address
                    addr_cell = Text(addr_display, style="dim")

            table.add_row(agent.name, desc, addr_cell)

        self.console.print(table)

    def select_agent(self, agents: List[AgentInfo], source_type: str = "LOCAL", source_location: str = "") -> Optional[AgentInfo]:
        """
        Prompt user to select an agent from the list.
        
        Args:
            agents: List of available agents
            source_type: Source type (LOCAL, REMOTE, etc.)
            source_location: Source location (directory path or URL)
            
        Returns:
            Selected agent or None if selection cancelled
        """
        if not agents:
            self.console.print(f"[red]No agents available ({source_type}: {source_location}).[/red]")
            return None

        # Default: use first agent (Aworld) without prompting or showing the agents table
        selected_agent = agents[0]
        return selected_agent
    
    def select_team(self, teams: List[AgentInfo], source_type: str = "LOCAL", source_location: str = "") -> Optional[AgentInfo]:
        """
        Alias for select_agent for backward compatibility.
        Use select_agent instead.
        """
        return self.select_agent(teams, source_type, source_location)

    def _visualize_team(self, executor_instance: Any):
        """Visualize the structure of the current team in a full-width split-screen layout."""
        from rich.columns import Columns
        try:
            from rich.console import Group
        except ImportError:
            try:
                from rich import Group
            except ImportError:
                # Fallback for older Rich versions
                class Group:
                    """Fallback Group class for older Rich versions."""
                    def __init__(self, *renderables):
                        self.renderables = renderables
                    
                    def __rich_console__(self, console, options):
                        for renderable in self.renderables:
                            yield renderable
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.align import Align
        from rich import box

        # 1. Get swarm from executor
        swarm = getattr(executor_instance, "swarm", None)
        if not swarm:
            self.console.print("[yellow]Current agent does not support visualization (not a swarm).[/yellow]")
            return

        # 2. Get agent graph
        graph = getattr(swarm, "agent_graph", None)
        if not graph:
            self.console.print("[yellow]No agent graph found in swarm.[/yellow]")
            return

        # --- Gather Data ---

        # Goal
        goal_text = "Run task"
        if hasattr(executor_instance, "task"):
            task = executor_instance.task
            if hasattr(task, "input") and task.input:
                 goal_text = str(task.input)
            elif hasattr(task, "name") and task.name:
                 goal_text = task.name

        if goal_text == "Run task" and hasattr(swarm, "task") and swarm.task:
             goal_text = str(swarm.task)

        if len(goal_text) > 100:
            goal_text = goal_text[:97] + "..."

        # Active skills
        active_skill_names = set()
        if hasattr(executor_instance, 'get_skill_status'):
             try:
                 status = executor_instance.get_skill_status()
                 active_names = status.get('active_names', [])
                 if active_names:
                     active_skill_names = set(active_names)
             except:
                 pass

        # Build Agent Panels
        agent_panels = []
        if graph and graph.agents:
            for agent in graph.agents.values():
                agent_skills = set()
                if hasattr(agent, "skill_configs") and agent.skill_configs:
                     for skill_name in agent.skill_configs.keys():
                         agent_skills.add(skill_name)

                agent_tools = set()
                mcp_tools = set()
                if hasattr(agent, "tools") and agent.tools:
                    for tool in agent.tools:
                        if isinstance(tool, dict) and "function" in tool:
                            agent_tools.add(tool["function"].get("name", "unknown"))
                        elif hasattr(tool, "name"):
                             agent_tools.add(tool.name)

                if hasattr(agent, "mcp_servers") and agent.mcp_servers:
                    for s in agent.mcp_servers:
                         mcp_tools.add(s)

                content_parts = []
                if agent_skills:
                    skills_list = []
                    for s in list(agent_skills)[:5]:
                        if s in active_skill_names:
                             skills_list.append(f"[bold green]• {s}[/bold green]")
                        else:
                             skills_list.append(f"• {s}")
                    if len(agent_skills) > 5:
                        skills_list.append(f"[dim]...({len(agent_skills)-5})[/dim]")
                    content_parts.append(f"[bold cyan]Skills:[/bold cyan]\n" + "\n".join(skills_list))

                tools_list = []
                built_in_list = ["Read", "Write", "Bash", "Grep"]
                has_builtins = any(t in agent_tools for t in built_in_list)
                if has_builtins:
                     tools_list.append("Built-in")
                if mcp_tools:
                     tools_list.append(f"MCP: {len(mcp_tools)}")
                custom_tools = [t for t in agent_tools if t not in built_in_list]
                if custom_tools:
                     tools_list.append(f"Custom: {len(custom_tools)}")

                if tools_list:
                    content_parts.append(f"[bold yellow]Tools:[/bold yellow]\n" + ", ".join(tools_list))

                agent_content = "\n".join(content_parts) if content_parts else "[dim]-[/dim]"

                agent_panel = Panel(
                    agent_content,
                    title=f"[bold]{agent.name()}[/bold]",
                    box=box.ROUNDED,
                    border_style="blue",
                    padding=(0, 1),
                    expand=True
                )
                agent_panels.append(agent_panel)

        # --- Build Layout ---

        layout = Layout()
        layout.split_row(
            Layout(name="process", ratio=1),
            Layout(name="team", ratio=1)
        )

        # --- Process Column (Left) ---

        goal_panel = Panel(
            Align.center(f"[bold]GOAL[/bold]\n\"{goal_text}\""),
            box=box.ROUNDED,
            style="white",
            border_style="green"
        )

        loop_panel = Panel(
             Align.center("[bold]AGENT LOOP[/bold]\n[dim]observe → think → act → learn → repeat[/dim]"),
             box=box.ROUNDED,
             style="white"
        )

        hooks_panel = Panel(
             Align.center("[bold]HOOKS[/bold]\n[dim]guard rails, logging, human-in-the-loop[/dim]"),
             box=box.ROUNDED,
             style="white"
        )

        output_panel = Panel(
             Align.center("[bold]STRUCTURED OUTPUT[/bold]\n[dim]validated JSON matching your schema[/dim]"),
             box=box.ROUNDED,
             style="white",
             border_style="green"
        )

        arrow_down = Align.center("│\n▼")

        process_content = Group(
            goal_panel,
            arrow_down,
            loop_panel,
            arrow_down,
            hooks_panel,
            arrow_down,
            output_panel
        )

        layout["process"].update(Panel(process_content, title="Process Flow", box=box.ROUNDED))

        # --- Team Column (Right) ---

        swarm_label = Align.center("[bold]SWARM[/bold]")

        # Use Columns for agents if there are many, or Stack if few
        # Using Columns with expand=True to fill width
        if len(agent_panels) > 1:
             agents_display = Columns(agent_panels, expand=True, equal=True)
        else:
             agents_display = Group(*[Align.center(p) for p in agent_panels])

        team_content = Group(
            swarm_label,
            Align.center("│\n▼"),
            agents_display
        )

        layout["team"].update(Panel(team_content, title="Team Structure", box=box.ROUNDED))

        # Print layout full width
        self.console.print(layout)

    def _display_system_info(self, help_text: str = ""):
        """
        Display comprehensive system information including help commands,
        configuration, skills, agents, and memory loading locations.
        Consistent styling with _show_banner from main.py.
        """

        # Section 1: Help Commands
        self.console.print(Panel(help_text or "Available Commands", title="📋 Help", style="blue", border_style="bright_cyan"))

    def _display_conf_info(self):
        from pathlib import Path
        from rich.table import Table

        # Keep visual style aligned with main banner output.
        self.console.print("[bold bright_cyan]⚙ System Configuration[/bold bright_cyan]")

        info_table = Table(show_header=False, box=None, padding=(0, 2))
        info_table.add_column("Icon", style="bright_yellow", justify="left")
        info_table.add_column("Component", style="bold bright_green")
        info_table.add_column("Details", style="bright_white")
        info_table.add_column("Status", justify="right")

        # 1. Configuration
        try:
            from .core.config import get_config
            config = get_config()
            source_type, source_path = config.get_config_source(".env")
            # Keep source selection aligned with load_config_with_env:
            # local .env has higher priority; otherwise use global aworld.json.
            config_path = source_path if (source_type == "local" and source_path) else config.get_config_path()
            config_status = "[bold bright_green]ONLINE[/bold bright_green]"
            config_loc = f"[dim]{config_path}[/dim]"
        except Exception as e:
            config_status = "[bold red]ERROR[/bold red]"
            config_loc = f"[dim red]Error: {e}[/dim red]"
        info_table.add_row("⚡", "Config", config_loc, )

        # 2. Memory
        try:
            from aworld.memory.main import _default_file_memory_store
            memory_store = _default_file_memory_store()
            memory_location = getattr(memory_store, 'memory_root', 'Unknown')
            memory_status = "[bold bright_green]ACTIVE[/bold bright_green]"
            memory_loc = f"[dim]{memory_location}[/dim]"
        except Exception as e:
            memory_status = "[bold red]ERROR[/bold red]"
            memory_loc = f"[dim red]Error: {e}[/dim red]"
        info_table.add_row("💾", "Memory", memory_loc, )

        # 3. Skills
        try:
            from .core.runtime_skill_registry import build_runtime_skill_registry_view

            registry = build_runtime_skill_registry_view()
            skills_count = len(registry.get_all_skills())
            skills_loc = f"[dim]{list(registry.source_paths)}[/dim]"
            skills_status = f"[bold bright_green]{skills_count} LOADED[/bold bright_green]"
        except Exception as e:
            skills_status = "[bold red]ERROR[/bold red]"
            skills_loc = f"[dim red]Error: {e}[/dim red]"
        info_table.add_row("🎯", "Skills", skills_loc, )

        # 4. Agents
        try:
            agents_loc = os.path.expanduser(os.environ.get('AGENTS_PATH', '~/.aworld/agents'))
            agents_status = (
                "[bold bright_green]READY[/bold bright_green]"
                if Path(agents_loc).exists()
                else "[bold yellow]MISSING[/bold yellow]"
            )
            agents_loc_styled = f"[dim]{agents_loc}[/dim]"
        except Exception as e:
            agents_status = "[bold red]ERROR[/bold red]"
            agents_loc_styled = f"[dim red]Error: {e}[/dim red]"
        info_table.add_row("🤖", "Agents", agents_loc_styled, )

        self.console.print(info_table)
        self.console.print()

    def render_cron_notifications(self, notifications: List[Any]) -> None:
        """
        Render pending cron notifications.

        Args:
            notifications: List of CronNotification objects from notification center

        Design (per Section 8.7):
            - Renders up to 3 notifications inline with color-coded status
            - Shows overflow count if more than 3 pending
            - Uses fixed-template summaries (no raw error text)
            - For failed tasks, directs user to /cron list for details
        """
        if not notifications:
            return

        # Cap visible notifications at 3
        visible = notifications[:3]
        overflow = len(notifications) - 3

        for notif in visible:
            status = getattr(notif, 'status', 'ok')
            status_colors = {
                'ok': 'green',
                'error': 'red',
                'timeout': 'yellow'
            }
            color = status_colors.get(status, 'white')

            # Main notification line (fixed template summary)
            summary = getattr(notif, 'summary', '')
            self.console.print(f"[{color}][Cron] {summary}[/{color}]")

            detail = getattr(notif, 'detail', None)
            if detail:
                self.console.print(f"  [bold]内容：[/bold]{detail}")

            # Show next run time for recurring jobs
            next_run_at = getattr(notif, 'next_run_at', None)
            if next_run_at:
                self.console.print(f"  [dim]next run: {next_run_at}[/dim]")

            # For failed/timeout tasks, direct to /cron list for details
            if status in ('error', 'timeout'):
                self.console.print(f"  [dim]details: /cron list[/dim]")

        # Show overflow count
        if overflow > 0:
            self.console.print(f"[dim]... and {overflow} more cron notifications[/dim]")

        # Add spacing after notifications
        self.console.print()

    async def _drain_notifications_safe(self, runtime) -> List:
        """
        Thread-safe wrapper for draining notifications.

        Prevents concurrent drain from poller and main loop using asyncio.Lock.
        Returns empty list on any error to ensure graceful failure.
        """
        async with self._notification_drain_lock:
            try:
                return await runtime._drain_notifications()
            except Exception:
                return []

    def _invalidate_active_prompt(self) -> None:
        session = self._active_prompt_session
        if session is None:
            return
        app = getattr(session, "app", None)
        if app is None or not hasattr(app, "invalidate"):
            return
        try:
            app.invalidate()
        except Exception:
            pass

    def _handle_notification_center_change(self) -> None:
        self._invalidate_active_prompt()

    def _bind_notification_center_listener(self, notification_center) -> None:
        if notification_center is self._subscribed_notification_center:
            return
        self._unbind_notification_center_listener()
        if notification_center is None or not hasattr(notification_center, "add_change_listener"):
            return
        try:
            notification_center.add_change_listener(self._notification_center_listener)
            self._subscribed_notification_center = notification_center
        except Exception:
            self._subscribed_notification_center = None

    def _unbind_notification_center_listener(self) -> None:
        notification_center = self._subscribed_notification_center
        if notification_center is None or not hasattr(notification_center, "remove_change_listener"):
            self._subscribed_notification_center = None
            return
        try:
            notification_center.remove_change_listener(self._notification_center_listener)
        except Exception:
            pass
        finally:
            self._subscribed_notification_center = None
    async def _render_cron_notifications_safe(self, notifications: List[Any]) -> None:
        if not notifications:
            return

        def _render() -> None:
            self.console.print()
            self.render_cron_notifications(notifications)

        if self._active_prompt_session is not None:
            await run_in_terminal(_render)
            return

        _render()

    async def _ensure_notification_poller(self, runtime) -> None:
        notification_center = getattr(runtime, "_notification_center", None) if runtime is not None else None
        if runtime is None or notification_center is None:
            return
        self._bind_notification_center_listener(notification_center)
        if self._notification_poll_task and not self._notification_poll_task.done():
            return
        self._notification_stop_event = asyncio.Event()
        self._notification_poll_task = asyncio.create_task(
            self._notification_poller(runtime, stop_event=self._notification_stop_event)
        )

    async def _stop_notification_poller(self) -> None:
        if not self._notification_poll_task:
            self._unbind_notification_center_listener()
            return
        if self._notification_stop_event:
            self._notification_stop_event.set()
        try:
            await asyncio.wait_for(self._notification_poll_task, timeout=1.0)
        except asyncio.TimeoutError:
            self._notification_poll_task.cancel()
            try:
                await self._notification_poll_task
            except asyncio.CancelledError:
                pass
        finally:
            self._notification_poll_task = None
            self._notification_stop_event = None
            self._unbind_notification_center_listener()

    def _hud_owns_notification_state(self, runtime) -> bool:
        if runtime is None or not hasattr(runtime, "active_plugin_capabilities"):
            return False
        try:
            return "hud" in tuple(runtime.active_plugin_capabilities())
        except Exception:
            return False

    async def _notification_poller(
        self,
        runtime,
        poll_interval: float = NOTIFICATION_POLL_INTERVAL,
        stop_event: asyncio.Event = None
    ) -> None:
        """
        Background task that polls for cron notifications during idle prompt.

        Runs continuously while user is at input prompt, checking every
        `poll_interval` seconds for new notifications. Skips polling during
        agent execution to avoid duplicate drains.
        """
        while not stop_event.is_set():
            try:
                # Only poll when idle (not executing agent)
                if not self._is_agent_executing:
                    # When HUD is active, keep notifications unread so the toolbar/HUD
                    # can surface the live inbox count until the user explicitly opens it.
                    if self._hud_owns_notification_state(runtime):
                        self._invalidate_active_prompt()
                        await asyncio.sleep(poll_interval)
                        continue
                    notifications = await self._drain_notifications_safe(runtime)
                    if notifications:
                        await self._render_cron_notifications_safe(notifications)

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                break
            except Exception:
                # Graceful failure - never crash on notification error
                pass

    async def _esc_key_listener(self):
        """
        Background listener for Esc key to interrupt currently executing tasks.
        This function runs in the background, continuously listening for keyboard input.
        """
        try:
            from prompt_toolkit import Application
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import Layout
            from prompt_toolkit.layout.containers import Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.formatted_text import FormattedText
            
            # Create a hidden window to capture Esc key
            kb = KeyBindings()
            
            # Store reference to currently executing task
            if not hasattr(self, '_current_executor_task'):
                self._current_executor_task = None
            
            def handle_esc(event):
                """Handle Esc key press"""
                if hasattr(self, '_current_executor_task') and self._current_executor_task:
                    if not self._current_executor_task.done():
                        self._current_executor_task.cancel()
                        self.console.print("\n[yellow]⚠️ Task interrupted by Esc key[/yellow]")
            
            kb.add("escape")(handle_esc)
            
            # Create an invisible control
            control = FormattedTextControl(
                text=FormattedText([("", "")]),
                focusable=True
            )
            
            window = Window(content=control, height=0)
            layout = Layout(window)
            
            # Create a hidden application to listen for keyboard
            app = Application(
                layout=layout,
                key_bindings=kb,
                full_screen=False,
                mouse_support=False
            )
            
            # Run application in background
            await asyncio.to_thread(app.run)
        except Exception:
            # If prompt_toolkit is not available or error occurs, fail silently
            pass

    async def _interactive_permission_prompt(self, reason: str, context: Optional[dict] = None) -> str:
        """Interactive permission prompt for 'ask' mode

        Called by permission handler when a hook returns 'ask' decision.
        Presents the user with context and asks for allow/deny.

        Args:
            reason: Reason for permission request
            context: Optional context (tool_name, args, etc.)

        Returns:
            'allow' or 'deny'
        """
        try:
            # Build permission request message
            self.console.print("\n[bold yellow]⚠️  Permission Required[/bold yellow]")
            self.console.print(f"[yellow]Reason:[/yellow] {reason}")

            if context:
                tool_name = context.get('tool_name', 'unknown')
                self.console.print(f"[yellow]Tool:[/yellow] {tool_name}")

                # Show action details if available
                actions = context.get('action', [])
                if actions and len(actions) > 0:
                    first_action = actions[0]
                    if isinstance(first_action, dict):
                        action_name = first_action.get('action_name', 'unknown')
                        params = first_action.get('params', {})
                        self.console.print(f"[yellow]Action:[/yellow] {action_name}")
                        if params:
                            self.console.print(f"[yellow]Parameters:[/yellow] {params}")

            # Prompt for decision
            self.console.print("\n[bold]Allow this action?[/bold]")
            self.console.print("  [green]y/yes[/green] - Allow this action")
            self.console.print("  [red]n/no[/red]  - Deny this action")

            # Use prompt_toolkit if available, otherwise fallback to input()
            if sys.stdin.isatty():
                user_response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: Prompt.ask("\nYour choice", choices=["y", "yes", "n", "no"], default="n")
                )
            else:
                # Fallback for non-interactive environments
                logger.warning("Non-interactive environment detected during permission prompt")
                return 'deny'

            # Parse response
            if user_response.lower() in ['y', 'yes']:
                self.console.print("[green]✓ Permission granted[/green]\n")
                return 'allow'
            else:
                self.console.print("[red]✗ Permission denied[/red]\n")
                return 'deny'

        except Exception as e:
            logger.error(f"Interactive permission prompt failed: {e}")
            self.console.print(f"[red]Error during permission prompt: {e}[/red]")
            return 'deny'

    def _resolve_hook_text(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            content = value.get('content')
            if isinstance(content, str):
                return content
        return str(value)

    def _get_plugin_runtime(self, executor_instance: Any = None) -> Any:
        if executor_instance and hasattr(executor_instance, '_base_runtime'):
            return executor_instance._base_runtime
        return None

    async def _run_plugin_hooks(
        self,
        hook_point: str,
        event: dict[str, Any],
        executor_instance: Any = None,
    ) -> list[tuple[Any, Any]]:
        runtime = self._get_plugin_runtime(executor_instance)
        if runtime is None or not hasattr(runtime, 'get_plugin_hooks'):
            return []

        if hasattr(runtime, "run_plugin_hooks"):
            result = runtime.run_plugin_hooks(
                hook_point,
                event=dict(event),
                executor_instance=executor_instance,
            )
            if inspect.isawaitable(result):
                return await result
            return result or []

        results = []
        for hook in runtime.get_plugin_hooks(hook_point):
            try:
                state = {}
                if hasattr(runtime, 'build_plugin_hook_state'):
                    state = runtime.build_plugin_hook_state(hook.plugin_id, hook.scope, executor_instance)
                results.append((hook, await hook.run(event=dict(event), state=state)))
            except Exception as e:
                logger.warning(
                    f"Plugin hook '{getattr(hook, 'entrypoint_id', 'unknown')}' failed "
                    f"at '{hook_point}': {e}"
                )
        return results

    def _normalize_skill_names(
        self, skill_names: Optional[list[str] | tuple[str, ...]]
    ) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for skill_name in skill_names or []:
            value = str(skill_name).strip()
            if not value or value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return normalized

    def _agent_name_for_resolution(self, agent: Any) -> str | None:
        name_attr = getattr(agent, "name", None)
        if callable(name_attr):
            try:
                resolved = name_attr()
                return str(resolved) if resolved else None
            except Exception:
                return None
        if isinstance(name_attr, str) and name_attr:
            return name_attr
        return None

    def _resolve_swarm_agent(self, executor_instance: Any, agent_name: str | None) -> Any | None:
        iter_agents = getattr(executor_instance, "_iter_swarm_agents", None)
        if callable(iter_agents):
            for agent in iter_agents():
                resolved_name = self._agent_name_for_resolution(agent)
                if agent_name is None or resolved_name == agent_name:
                    return agent
        return None

    def _skill_package_roots_for_agent(self, plugin_manager: Any, agent_name: str | None) -> tuple[Path, ...]:
        roots: list[Path] = []
        normalized_agent = (agent_name or "").strip().lower()

        for package in plugin_manager.list_skill_packages(include_disabled=False):
            metadata = package.get("metadata", {})
            scope = str(
                metadata.get("scope")
                if isinstance(metadata, dict) and metadata.get("scope") is not None
                else package.get("activation_scope", "global")
            )
            if scope == "global":
                pass
            elif (
                scope.startswith("agent:")
                and normalized_agent
                and scope.lower() == f"agent:{normalized_agent}"
            ):
                pass
            else:
                continue

            plugin_path = Path(str(package["path"])).resolve()
            if plugin_path.exists() and plugin_path.is_dir():
                roots.append(plugin_path)

        return tuple(roots)

    def _skill_resolution_environment(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> tuple[Any, tuple[Path, ...], dict[str, Any]]:
        from .core.plugin_manager import PluginManager

        plugin_manager = PluginManager()
        runtime_plugin_roots = tuple(
            Path(item).resolve()
            for item in plugin_manager.get_runtime_plugin_roots()
        )
        skill_package_roots = self._skill_package_roots_for_agent(plugin_manager, agent_name)

        resolver_inputs: dict[str, Any] = {}
        selected_agent = None
        if executor_instance is not None:
            selected_agent = self._resolve_swarm_agent(executor_instance, agent_name)
        if selected_agent is not None:
            agent_conf = getattr(selected_agent, "conf", None)
            if agent_conf is not None and isinstance(getattr(agent_conf, "ext", None), dict):
                resolver_inputs = dict(agent_conf.ext.get("skill_resolver_inputs", {}))

        plugin_roots = runtime_plugin_roots + skill_package_roots + tuple(
            Path(item).resolve()
            for item in resolver_inputs.get("plugin_roots", [])
        )
        return plugin_manager, plugin_roots, resolver_inputs

    def _generated_skill_alias_map(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> dict[str, str]:
        reserved = {
            "exit",
            "quit",
            "help",
            "new",
            "restore",
            "latest",
            "switch",
            "skills",
            "agents",
            "cost",
            "compact",
            "team",
            "memory",
            "sessions",
            "visualize_trajectory",
        }
        reserved.update(command.name.lower() for command in CommandRegistry.list_commands())

        try:
            resolved = self._resolve_visible_skills(
                agent_name=agent_name,
                executor_instance=executor_instance,
            )
        except Exception:
            return {}

        aliases: dict[str, str] = {}
        for skill_name in sorted(resolved.skill_configs):
            normalized = str(skill_name).strip()
            if not normalized:
                continue
            if normalized.lower() in reserved:
                continue
            aliases[f"/{normalized}"] = normalized
        return aliases

    def _match_generated_skill_alias(
        self,
        user_input: str,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> str | None:
        normalized_input = str(user_input or "").strip()
        if not normalized_input.startswith("/"):
            return None

        alias_token = normalized_input.split(maxsplit=1)[0].lower()
        for alias, skill_name in self._generated_skill_alias_map(
            agent_name=agent_name,
            executor_instance=executor_instance,
        ).items():
            if alias.lower() == alias_token:
                return skill_name
        return None

    def _match_disabled_skill_alias(
        self,
        user_input: str,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> str | None:
        from .core.skill_state_manager import SkillStateManager
        from .core.runtime_skill_registry import build_runtime_skill_registry_view

        normalized_input = str(user_input or "").strip()
        if not normalized_input.startswith("/"):
            return None

        alias_token = normalized_input.split(maxsplit=1)[0].lower()
        disabled = set(SkillStateManager().disabled_skill_names())
        if not disabled:
            return None

        try:
            resolved = self._resolve_visible_skills(
                agent_name=agent_name,
                executor_instance=executor_instance,
                apply_disabled_filter=False,
            )
            skill_names = list(getattr(resolved, "skill_configs", {}))
        except Exception:
            skill_names = []

        if not skill_names:
            try:
                runtime_view = build_runtime_skill_registry_view()
                skill_names = list(runtime_view.get_all_skills())
            except Exception:
                return None

        for skill_name in skill_names:
            normalized_skill = str(skill_name).strip()
            if not normalized_skill or normalized_skill.lower() not in disabled:
                continue
            if f"/{normalized_skill}".lower() == alias_token:
                return normalized_skill
        return None

    def _rewrite_generated_skill_alias_input(
        self,
        user_input: str,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> tuple[bool, str]:
        skill_name = self._match_generated_skill_alias(
            user_input,
            agent_name=agent_name,
            executor_instance=executor_instance,
        )
        if skill_name is None:
            return False, user_input

        parts = str(user_input or "").strip().split(maxsplit=1)
        self._pending_skill_overrides = [skill_name]
        if len(parts) == 1:
            self.console.print(
                f"[green]Will force skill on next task:[/green] {skill_name}"
            )
            return True, ""
        return True, parts[1].strip()

    def _load_skill_commands(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> list[Any]:
        from aworld.plugins.discovery import discover_plugins
        from .plugin_capabilities.skill_commands import load_plugin_skill_commands

        _, plugin_roots, _ = self._skill_resolution_environment(
            agent_name=agent_name,
            executor_instance=executor_instance,
        )
        return load_plugin_skill_commands(discover_plugins(plugin_roots))

    def _skill_command_completion_entries(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> dict[str, str]:
        entries: dict[str, str] = {}
        for command in self._load_skill_commands(
            agent_name=agent_name,
            executor_instance=executor_instance,
        ):
            names = [command.name, *(getattr(command, "aliases", tuple()) or tuple())]
            for name in names:
                normalized = str(name).strip()
                if normalized:
                    entries[f"/skills {normalized}"] = command.description
        return entries

    def _skill_command_usage_text(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> str:
        usages = ["/skills"]
        for command in self._load_skill_commands(
            agent_name=agent_name,
            executor_instance=executor_instance,
        ):
            usage = str(getattr(command, "usage", "") or "").strip()
            usages.append(usage or f"/skills {command.name}")
        return " | ".join(dict.fromkeys(usages))

    def _build_skill_help_lines(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> list[str]:
        lines = ["Type '/skills' to list available skills."]
        for command in self._load_skill_commands(
            agent_name=agent_name,
            executor_instance=executor_instance,
        ):
            usage = str(getattr(command, "usage", "") or "").strip()
            lines.append(
                f"Type '{usage or f'/skills {command.name}'}' to {command.description}."
            )
        lines.append("Type '/<skill-name>' to force that visible skill on the next task.")
        return lines

    def _provider_commands_by_skill(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> dict[str, list[str]]:
        from aworld.plugins.discovery import discover_plugins
        from .plugin_capabilities.commands import commands_for_plugin

        _, plugin_roots, _ = self._skill_resolution_environment(
            agent_name=agent_name,
            executor_instance=executor_instance,
        )

        commands_by_skill: dict[str, list[str]] = {}
        for plugin in discover_plugins(plugin_roots):
            visible_commands = commands_for_plugin(plugin)
            if not visible_commands:
                continue
            for entrypoint in plugin.manifest.entrypoints.get("skills", ()):
                commands_by_skill.setdefault(
                    entrypoint.entrypoint_id,
                    list(visible_commands),
                )
        return commands_by_skill

    def _resolve_visible_skills(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
        requested_skill_names: Optional[list[str] | tuple[str, ...]] = None,
        apply_disabled_filter: bool = True,
    ):
        from .core.skill_activation_resolver import SkillActivationResolver, SkillResolverRequest
        from .core.skill_state_manager import SkillStateManager

        _, plugin_roots, resolver_inputs = self._skill_resolution_environment(
            agent_name=agent_name,
            executor_instance=executor_instance,
        )

        request = SkillResolverRequest(
            plugin_roots=plugin_roots,
            runtime_scope="session",
            agent_name=agent_name,
            requested_skill_names=tuple(self._normalize_skill_names(requested_skill_names)),
            disabled_skill_names=(
                SkillStateManager().disabled_skill_names()
                if apply_disabled_filter
                else tuple()
            ),
            compatibility_sources=tuple(
                str(item)
                for item in resolver_inputs.get("compatibility_sources", [])
            ),
            compatibility_skill_patterns=tuple(
                str(item)
                for item in resolver_inputs.get("compatibility_skill_patterns", [])
            ),
        )
        return SkillActivationResolver().resolve(request)

    def _supports_requested_skill_names(self, executor_instance: Any, executor: Callable[[str], Any]) -> bool:
        candidates = []
        if executor_instance is not None and hasattr(executor_instance, "chat"):
            candidates.append(executor_instance.chat)
        candidates.append(executor)

        for candidate in candidates:
            try:
                if "requested_skill_names" in inspect.signature(candidate).parameters:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    async def _run_executor_prompt(
        self,
        prompt: str,
        executor: Callable[[str], Any],
        *,
        executor_instance: Any = None,
    ) -> Any:
        requested_skill_names = self._normalize_skill_names(self._pending_skill_overrides)
        if requested_skill_names and self._supports_requested_skill_names(executor_instance, executor):
            self._pending_skill_overrides = []
            chat_callable = executor_instance.chat if executor_instance is not None and hasattr(executor_instance, "chat") else executor
            return await chat_callable(prompt, requested_skill_names=requested_skill_names)
        return await executor(prompt)

    async def _handle_active_task_input(
        self,
        user_input: str,
        *,
        runtime: Any,
        session_id: str | None,
        executor_task: asyncio.Task,
        workspace_path: str | None = None,
        task_id: str | None = None,
    ) -> bool:
        normalized = (user_input or "").strip()
        if not normalized:
            return True

        if normalized in {_ESC_INTERRUPT_SENTINEL, "/interrupt"}:
            interrupt_requested = normalized == _ESC_INTERRUPT_SENTINEL
            if not interrupt_requested and runtime is not None and hasattr(runtime, "request_session_interrupt"):
                interrupt_requested = bool(runtime.request_session_interrupt(session_id))
            if not executor_task.done():
                executor_task.cancel()
            if interrupt_requested:
                self.console.print("[dim]Interrupt requested.[/dim]")
            else:
                self.console.print("[yellow]No active steerable task to interrupt.[/yellow]")
            return True

        if normalized.startswith("/"):
            self.console.print("[yellow]Only /interrupt is available while a task is active.[/yellow]")
            return True

        steering = getattr(runtime, "_steering", None) if runtime is not None else None
        if steering is None or not session_id:
            return False

        item = steering.enqueue_text(session_id, normalized)
        snapshot = steering.snapshot(session_id)
        log_queued_steering_event(
            workspace_path=workspace_path,
            session_id=session_id,
            task_id=task_id or snapshot.get("task_id"),
            steering_item=item,
            pending_count=int(snapshot.get("pending_count", 0) or 0),
        )
        self.console.print("[dim]Steering queued for the next checkpoint.[/dim]")
        return True

    async def _prompt_active_task_input(
        self,
        *,
        session: PromptSession,
        runtime: Any,
        agent_name: str,
        wait_started_at: float | None = None,
    ) -> str:
        prompt_kwargs = self._build_prompt_kwargs(
            runtime,
            agent_name=agent_name,
            mode="Steering",
        )
        prompt_kwargs["placeholder"] = lambda: self._build_active_task_wait_text(wait_started_at)
        prompt_kwargs["reserve_space_for_menu"] = 0
        with patch_stdout():
            return await session.prompt_async(HTML("<b><yellow>›</yellow></b> "), **prompt_kwargs)

    def _build_active_task_wait_text(self, wait_started_at: float | None = None) -> str:
        elapsed = max(0.0, time.monotonic() - wait_started_at) if wait_started_at is not None else 0.0
        return (
            f"Waiting for background task ({self._format_active_task_wait_elapsed(elapsed)} "
            "• type to steer • Esc to interrupt)"
        )

    def _format_active_task_wait_elapsed(self, elapsed_seconds: float) -> str:
        total_seconds = max(0, int(elapsed_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    async def _await_active_executor_result(self, executor_task: asyncio.Task) -> Any:
        try:
            return await executor_task
        except asyncio.CancelledError:
            return None

    async def _cancel_active_executor_task(self, executor_task: asyncio.Task) -> None:
        if executor_task.done():
            await self._await_active_executor_result(executor_task)
            return

        executor_task.cancel()
        try:
            await executor_task
        except (asyncio.CancelledError, Exception):
            return

    async def _run_terminal_fallback_continuation(
        self,
        *,
        runtime: Any,
        session_id: str | None,
        executor: Callable[[str], Any],
        completer: Completer | None,
        agent_name: str,
        executor_instance: Any = None,
        is_terminal: bool = False,
    ) -> tuple[bool, Any]:
        steering = getattr(runtime, "_steering", None) if runtime is not None else None
        if steering is None or not session_id or not is_terminal:
            return False, None

        snapshot = steering.snapshot(session_id)
        if int(snapshot.get("pending_count", 0) or 0) <= 0:
            return False, None
        if bool(snapshot.get("interrupt_requested")):
            return False, None

        follow_up_prompt = steering.consume_terminal_fallback_prompt(session_id)
        if not follow_up_prompt:
            return False, None

        self.console.print("[dim]Applying queued steering in a follow-up turn.[/dim]")
        result = await self._run_executor_with_active_steering(
            prompt=follow_up_prompt,
            executor=executor,
            completer=completer,
            runtime=runtime,
            agent_name=agent_name,
            executor_instance=executor_instance,
            is_terminal=is_terminal,
        )
        return True, result

    async def _run_executor_with_active_steering(
        self,
        *,
        prompt: str,
        executor: Callable[[str], Any],
        completer: Completer | None,
        runtime: Any,
        agent_name: str,
        executor_instance: Any = None,
        is_terminal: bool = False,
    ) -> Any:
        session_id = getattr(executor_instance, "session_id", None)
        wait_started_at = time.monotonic()
        previous_loading_suppressed = None
        previous_stream_suppressed = None
        if executor_instance is not None and is_terminal:
            previous_loading_suppressed = getattr(
                executor_instance,
                "_suppress_interactive_loading_status",
                False,
            )
            previous_stream_suppressed = getattr(
                executor_instance,
                "_suppress_interactive_stream_output",
                False,
            )
            executor_instance._suppress_interactive_loading_status = True
            executor_instance._suppress_interactive_stream_output = True
        executor_task = asyncio.create_task(
            self._run_executor_prompt(
                prompt,
                executor,
                executor_instance=executor_instance,
            )
        )
        self._current_executor_task = executor_task

        if runtime is not None and session_id and hasattr(runtime, "_steering"):
            try:
                task_id = getattr(getattr(executor_instance, "context", None), "task_id", None)
                runtime._steering.begin_task(
                    session_id,
                    task_id or f"interactive-{id(executor_task)}",
                )
            except Exception:
                pass

        try:
            if not is_terminal or completer is None or runtime is None or not session_id:
                return await self._await_active_executor_result(executor_task)

            while True:
                if executor_task.done():
                    result = await self._await_active_executor_result(executor_task)
                    continued, follow_up_result = await self._run_terminal_fallback_continuation(
                        runtime=runtime,
                        session_id=session_id,
                        executor=executor,
                        completer=completer,
                        agent_name=agent_name,
                        executor_instance=executor_instance,
                        is_terminal=is_terminal,
                    )
                    return follow_up_result if continued else result

                steering_session = self._create_prompt_session(
                    completer,
                    on_escape=lambda: runtime.request_session_interrupt(session_id),
                )
                prompt_task = asyncio.create_task(
                    self._prompt_active_task_input(
                        session=steering_session,
                        runtime=runtime,
                        agent_name=agent_name,
                        wait_started_at=wait_started_at,
                    )
                )

                done, pending = await asyncio.wait(
                    {executor_task, prompt_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if executor_task in done:
                    prompt_task.cancel()
                    try:
                        await prompt_task
                    except BaseException:
                        pass
                    result = await self._await_active_executor_result(executor_task)
                    continued, follow_up_result = await self._run_terminal_fallback_continuation(
                        runtime=runtime,
                        session_id=session_id,
                        executor=executor,
                        completer=completer,
                        agent_name=agent_name,
                        executor_instance=executor_instance,
                        is_terminal=is_terminal,
                    )
                    return follow_up_result if continued else result

                try:
                    user_input = await prompt_task
                except BaseException:
                    await self._cancel_active_executor_task(executor_task)
                    raise

                await self._handle_active_task_input(
                    user_input,
                    runtime=runtime,
                    session_id=session_id,
                    executor_task=executor_task,
                    workspace_path=getattr(getattr(executor_instance, "context", None), "workspace_path", None),
                    task_id=getattr(getattr(executor_instance, "context", None), "task_id", None),
                )
                if executor_task.done() or executor_task.cancelling():
                    result = await self._await_active_executor_result(executor_task)
                    continued, follow_up_result = await self._run_terminal_fallback_continuation(
                        runtime=runtime,
                        session_id=session_id,
                        executor=executor,
                        completer=completer,
                        agent_name=agent_name,
                        executor_instance=executor_instance,
                        is_terminal=is_terminal,
                    )
                    return follow_up_result if continued else result
        finally:
            steering = getattr(runtime, "_steering", None) if runtime is not None else None
            if steering is not None and session_id:
                try:
                    steering.end_task(session_id, clear_pending=True)
                except Exception:
                    pass
            if executor_instance is not None and previous_loading_suppressed is not None:
                executor_instance._suppress_interactive_loading_status = previous_loading_suppressed
            if executor_instance is not None and previous_stream_suppressed is not None:
                executor_instance._suppress_interactive_stream_output = previous_stream_suppressed
            self._current_executor_task = None

    async def _render_skills_table(
        self,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> None:
        from .core.skill_state_manager import SkillStateManager

        try:
            manageable = self._resolve_visible_skills(
                agent_name=agent_name,
                executor_instance=executor_instance,
                apply_disabled_filter=False,
            )
            resolved = self._resolve_visible_skills(
                agent_name=agent_name,
                executor_instance=executor_instance,
                requested_skill_names=self._pending_skill_overrides,
            )
        except Exception as exc:
            self.console.print(f"[red]Error loading skills: {exc}[/red]")
            return

        if not manageable.skill_configs:
            self.console.print("[yellow]No skills available.[/yellow]")
            return

        rows = sorted(manageable.skill_configs.items(), key=lambda item: item[0])
        pending = set(self._normalize_skill_names(self._pending_skill_overrides))
        disabled = set(SkillStateManager().disabled_skill_names())
        active = set(getattr(resolved, "active_skill_names", ()) or ())
        alias_map = self._generated_skill_alias_map(
            agent_name=agent_name,
            executor_instance=executor_instance,
        )
        commands_by_skill = self._provider_commands_by_skill(
            agent_name=agent_name,
            executor_instance=executor_instance,
        )
        table = Table(title="Skills", box=box.ROUNDED)
        table.add_column("Name", style="magenta")
        table.add_column("Description", style="green")
        table.add_column("Status", style="cyan")
        table.add_column("Alias", style="yellow")
        table.add_column("Commands", style="yellow", no_wrap=False, max_width=28)
        table.add_column("Address", style="dim", no_wrap=False, max_width=48)

        for skill_name, skill_data in rows:
            desc = skill_data.get("description") or skill_data.get("desc") or "No description"
            address = skill_data.get("skill_path", "") or "—"
            if skill_name in pending:
                status = "pending"
            elif skill_name.lower() in disabled:
                status = "disabled"
            elif skill_name in active:
                status = "active"
            else:
                status = "available"
            if address == "—":
                addr_cell = Text("—", style="dim")
            else:
                path = Path(address)
                link_target = path.parent if path.suffix else path
                link_url = link_target.resolve().as_uri()
                display = address if len(address) <= 48 else address[:45] + "..."
                addr_cell = Text(display, style=Style(dim=True, link=link_url))
            if status == "disabled":
                generated_alias = "—"
            else:
                generated_alias = alias_map.get(f"/{skill_name}", f"/{skill_name}")
            commands = ", ".join(commands_by_skill.get(skill_name, ())) or "—"
            table.add_row(skill_name, str(desc), status, generated_alias, commands, addr_cell)

        self.console.print(table)
        if pending:
            self.console.print(f"[dim]Pending explicit selection: {', '.join(sorted(pending))}[/dim]")
        self.console.print(f"[dim]Total: {len(rows)} skill(s)[/dim]")

    async def _handle_skills_command(
        self,
        user_input: str,
        *,
        agent_name: str | None = None,
        executor_instance: Any = None,
    ) -> bool:
        normalized = " ".join(user_input.strip().lower().split())
        if normalized in ("/skills", "skills"):
            await self._render_skills_table(
                agent_name=agent_name,
                executor_instance=executor_instance,
            )
            return True

        if normalized.startswith("/skills"):
            remainder = user_input.strip()[len("/skills"):].strip()
            if remainder:
                command_name, _, args_text = remainder.partition(" ")
                lookup = command_name.strip().lower()
                for command in self._load_skill_commands(
                    agent_name=agent_name,
                    executor_instance=executor_instance,
                ):
                    aliases = tuple(getattr(command, "aliases", tuple()) or tuple())
                    names = [command.name, *aliases]
                    if lookup not in {
                        str(name).strip().lower()
                        for name in names
                        if str(name).strip()
                    }:
                        continue
                    result = command.run(
                        self,
                        args_text.strip(),
                        agent_name=agent_name,
                        executor_instance=executor_instance,
                    )
                    if inspect.isawaitable(result):
                        result = await result
                    return True if result is None else bool(result)
            self.console.print(
                f"[yellow]Usage: {self._skill_command_usage_text(agent_name=agent_name, executor_instance=executor_instance)}[/yellow]"
            )
            return True

        if normalized.startswith("/"):
            alias_skill_name = self._match_generated_skill_alias(
                user_input,
                agent_name=agent_name,
                executor_instance=executor_instance,
            )
            if alias_skill_name:
                self._pending_skill_overrides = [alias_skill_name]
                self.console.print(
                    f"[green]Will force skill on next task:[/green] {alias_skill_name}"
                )
                return True

        return False

    async def _execute_follow_up_prompt(
        self,
        agent_name: str,
        executor: Callable[[str], Any],
        follow_up_prompt: str,
    ) -> None:
        self.console.print(f"[bold green]{agent_name}[/bold green]:")
        self._is_agent_executing = True
        try:
            await executor(follow_up_prompt)
        finally:
            self._is_agent_executing = False

    async def _apply_stop_hooks(
        self,
        executor_instance: Any = None,
        *,
        force: bool = False,
    ) -> tuple[bool, Optional[str]]:
        from .core.context import get_default_history_path

        if force:
            return True, None

        context = None
        if executor_instance and hasattr(executor_instance, 'context'):
            context = executor_instance.context

        workspace_path = getattr(context, 'workspace_path', None) or os.getcwd()
        event = {
            'transcript_path': str(get_default_history_path()),
            'workspace_path': workspace_path,
            'session_id': getattr(executor_instance, 'session_id', None),
            'task_id': getattr(context, 'task_id', None) if context else None,
        }

        for _, result in await self._run_plugin_hooks(
            hook_point='stop',
            event=event,
            executor_instance=executor_instance,
        ):
            if result.system_message:
                self.console.print(f"[dim]{result.system_message}[/dim]")

            if result.action == 'allow':
                continue

            if result.action == 'deny':
                reason = result.reason or 'Session termination blocked by plugin hook'
                self.console.print(f"[yellow]⚠️ {reason}[/yellow]")
                return False, None

            if result.action == 'block_and_continue':
                follow_up_prompt = self._resolve_hook_text(result.follow_up_prompt or result.updated_input)
                if not follow_up_prompt:
                    reason = result.reason or 'Session termination blocked by plugin hook'
                    self.console.print(f"[yellow]⚠️ {reason}[/yellow]")
                    return False, None
                return False, follow_up_prompt

        return True, None

    async def _apply_user_input_hooks(self, user_input: str, executor_instance: Any = None) -> tuple[bool, str]:
        """Run CLI user_input hooks and return whether execution should continue."""
        from aworld.core.event.base import Message
        from aworld.runners.hook.hooks import HookPoint
        from aworld.runners.hook.utils import run_hooks
        from aworld.runners.hook.v2.permission import get_permission_handler

        context = None
        if executor_instance and hasattr(executor_instance, 'context'):
            context = executor_instance.context

        user_input_msg = Message(
            category='user_input',
            payload=user_input,
            session_id=getattr(context, 'session_id', 'unknown') if context else 'unknown',
            sender='cli_user',
            headers={'context': context} if context else {}
        )
        if context:
            user_input_msg.context = context

        workspace_path = getattr(context, 'workspace_path', None) or os.getcwd()
        should_execute = True

        handler = get_permission_handler()
        handler.set_interactive_prompt(self._interactive_permission_prompt)

        async for hook_result in run_hooks(
            context=context,
            hook_point=HookPoint.USER_INPUT_RECEIVED,
            hook_from='cli',
            message=user_input_msg,
            workspace_path=workspace_path
        ):
            if not hook_result or not hasattr(hook_result, 'headers'):
                continue

            permission_decision = hook_result.headers.get('permission_decision')
            if permission_decision in ('deny', 'ask'):
                decision_reason = hook_result.headers.get(
                    'permission_decision_reason',
                    'User input blocked by hook'
                )

                if permission_decision == 'ask':
                    try:
                        context_dict = {
                            'user_input': user_input,
                            'hook_point': 'USER_INPUT_RECEIVED'
                        }
                        final_decision, resolution_reason = await handler.resolve_permission(
                            permission_decision, decision_reason, context_dict
                        )
                        if final_decision == 'deny':
                            self.console.print(f"[red]🚫 {resolution_reason}[/red]")
                            should_execute = False
                            break
                    except Exception as e:
                        self.console.print(f"[red]🚫 Permission resolution failed: {e}[/red]")
                        should_execute = False
                        break
                else:
                    self.console.print(f"[red]🚫 {decision_reason}[/red]")
                    should_execute = False
                    break

            updated_input = hook_result.headers.get('updated_input')
            if updated_input:
                resolved_input = self._resolve_hook_text(updated_input)
                if resolved_input:
                    user_input = resolved_input

            if hook_result.headers.get('prevent_continuation'):
                stop_reason = hook_result.headers.get('stop_reason', 'Hook stopped execution')
                self.console.print(f"[yellow]⚠️ {stop_reason}[/yellow]")
                should_execute = False
                break

        if should_execute:
            plugin_event = {
                'user_input': user_input,
                'workspace_path': workspace_path,
                'session_id': getattr(user_input_msg, 'session_id', None),
                'task_id': getattr(context, 'task_id', None) if context else None,
            }
            for _, result in await self._run_plugin_hooks(
                hook_point='user_input_received',
                event=plugin_event,
                executor_instance=executor_instance,
            ):
                if result.system_message:
                    self.console.print(f"[dim]{result.system_message}[/dim]")

                resolved_input = self._resolve_hook_text(result.updated_input)
                if resolved_input:
                    user_input = resolved_input
                    plugin_event['user_input'] = user_input

                if result.action == 'deny':
                    decision_reason = result.reason or 'User input blocked by plugin hook'
                    self.console.print(f"[red]🚫 {decision_reason}[/red]")
                    should_execute = False
                    break

        return should_execute, user_input

    async def run_chat_session(self, agent_name: str, executor: Callable[[str], Any], available_agents: List[AgentInfo] = None, executor_instance: Any = None) -> Union[bool, str]:
        """
        Run an interactive chat session with an agent.

        Args:
            agent_name: Name of the agent to chat with
            executor: Async function that executes chat messages
            available_agents: List of available agents for switching
            executor_instance: Optional executor instance (for session management)

        Returns:
            False if user wants to exit, True if wants to switch (show list), or agent name string to switch to
        """
        # Setup permission handler callback for interactive prompting
        from aworld.runners.hook.v2.permission import get_permission_handler
        handler = get_permission_handler()
        handler.set_interactive_prompt(self._interactive_permission_prompt)

        # Get current session_id if available
        session_id_info = ""
        if executor_instance and hasattr(executor_instance, 'session_id'):
            session_id_info = f"\nCurrent session: [dim]{executor_instance.session_id}[/dim]"

        # Get agent configuration info (PTC, MCP servers, and Skills)
        config_info = ""
        if available_agents:
            # Find current agent
            current_agent = next((a for a in available_agents if a.name == agent_name), None)
            if current_agent and current_agent.metadata:
                metadata = current_agent.metadata
                
                # Check PTC status - only show if enabled
                ptc_tools = metadata.get("ptc_tools", [])
                ptc_info = ""
                if ptc_tools:
                    ptc_count = len(ptc_tools) if isinstance(ptc_tools, list) else 0
                    ptc_status_text = f"✅ Enabled ({ptc_count} tools)" if ptc_count > 0 else "✅ Enabled"
                    ptc_info = f"\nPTC: [dim]{ptc_status_text}[/dim]"
                
                # Get MCP servers list
                mcp_servers = metadata.get("mcp_servers", [])
                if isinstance(mcp_servers, list) and mcp_servers:
                    mcp_list = ", ".join(mcp_servers)
                    mcp_info = f"\nMCP Servers: [dim]{mcp_list}[/dim]"
                else:
                    mcp_info = ""  # Don't show "None" either

                config_info = f"{ptc_info}{mcp_info}"
        
        # Get skill status from executor if available
        skill_info = ""
        if executor_instance and hasattr(executor_instance, 'get_skill_status'):
            try:
                skill_status = executor_instance.get_skill_status()
                total = skill_status.get('total', 0)
                active = skill_status.get('active', 0)
                inactive = skill_status.get('inactive', 0)
                active_names = skill_status.get('active_names', [])

                if total > 0:
                    if active > 0 and active_names:
                        # Show active skill names
                        active_names_str = ", ".join(active_names)
                        skill_info = f"\nSkills: [dim]Total: {total}, Active: {active} ({active_names_str}), Inactive: {inactive}[/dim]"
                    else:
                        skill_info = f"\nSkills: [dim]Total: {total}, Active: {active}, Inactive: {inactive}[/dim]"
                else:
                    skill_info = f"\nSkills: [dim]None[/dim]"
            except Exception:
                # If getting skill status fails, don't show skill info
                pass

        # Display welcome message with configuration details
        self.console.print(f"Starting chat with [bold]{agent_name}[/bold].{session_id_info}{config_info}{skill_info}")
        self.console.print("[dim]Type /help for available commands.[/dim]\n")

        # Build help text with both built-in and registered commands
        help_lines = [
            f"Starting chat session with [bold]{agent_name}[/bold].{session_id_info}{config_info}{skill_info}\n",
            "Type 'exit' to quit. Use 'exit!' to force quit without stop hooks.",
            "Type '/switch [agent_name]' to switch agent.",
            "Type '/new' to create a new session.",
            "Type '/restore' or '/latest' to restore to the latest session.",
            "Type '/agents' to list all available agents.",
            "Type '/cost' for current session, '/cost -all' for global history.",
            "Type '/compact' to run context compression.",
            "Type '/team' for agent team management.",
            "Type '/memory' to edit project context, '/memory view' to view, '/memory status' for status.",
        ]
        help_lines.extend(
            self._build_skill_help_lines(
                agent_name=agent_name,
                executor_instance=executor_instance,
            )
        )

        # Add registered commands from CommandRegistry
        registered_commands = CommandRegistry.list_commands()
        if registered_commands:
            help_lines.append("\n[bold cyan]Slash Commands:[/bold cyan]")
            for cmd in sorted(registered_commands, key=lambda c: c.name):
                help_lines.append(f"  /{cmd.name:<12} {cmd.description}")

        help_lines.append("\nUse @filename to include images or text files (e.g., @photo.jpg or @document.txt).")

        help_text = "\n".join(help_lines)

        # Check if we're in a real terminal (not IDE debugger or redirected input)
        is_terminal = sys.stdin.isatty()

        runtime = None
        if executor_instance and hasattr(executor_instance, '_base_runtime'):
            runtime = executor_instance._base_runtime

        # Setup completer and session only if in terminal
        agent_names = [a.name for a in available_agents] if available_agents else []
        session = None
        completer = None

        if is_terminal:
            completer = self._build_session_completer(
                agent_names=agent_names,
                agent_name=agent_name,
                executor_instance=executor_instance,
                runtime=runtime,
                event_loop=asyncio.get_running_loop(),
            )
            session = self._create_prompt_session(completer)
            await self._ensure_notification_poller(runtime)
        else:
            self._active_prompt_session = None

        while True:
            try:
                # Use prompt_toolkit in terminal, plain input() in non-terminal (e.g., IDE debugger)
                if is_terminal and completer:
                    session = self._ensure_prompt_session(session, completer)
                    await self._ensure_notification_poller(runtime)
                    # Use prompt_toolkit for input with completion
                    # We use HTML for basic coloring of the prompt
                    prompt_text = "<b><cyan>You</cyan></b>: "
                    prompt_kwargs = self._build_prompt_kwargs(
                        runtime,
                        agent_name=agent_name,
                        mode="Chat",
                    )
                    user_input = await asyncio.to_thread(session.prompt, HTML(prompt_text), **prompt_kwargs)
                else:
                    # Fallback to plain input() for non-terminal environments
                    self.console.print("[cyan]You[/cyan]: ", end="")
                    user_input = await asyncio.to_thread(input)
                
                user_input = user_input.strip()

                # Skip empty input (user just pressed Enter)
                if not user_input:
                    continue

                # Handle slash commands (custom command system)
                if user_input.startswith("/"):
                    generated_alias_handled, rewritten_user_input = (
                        self._rewrite_generated_skill_alias_input(
                            user_input,
                            agent_name=agent_name,
                            executor_instance=executor_instance,
                        )
                    )
                    if generated_alias_handled:
                        user_input = rewritten_user_input.strip()
                        if not user_input:
                            continue

                    if user_input.startswith("/"):
                        parts = user_input[1:].split(maxsplit=1)
                        cmd_name = parts[0]
                        cmd_args = parts[1] if len(parts) > 1 else ""

                        # Skip built-in commands (let them fall through)
                        builtin_commands = {
                            "exit", "quit", "help", "new", "restore", "latest",
                            "switch", "skills", "agents", "cost", "compact",
                            "team", "sessions", "visualize_trajectory"
                        }
                        if cmd_name.lower() not in builtin_commands:
                            command = CommandRegistry.get(cmd_name)
                            if command is None:
                                disabled_skill_name = self._match_disabled_skill_alias(
                                    user_input,
                                    agent_name=agent_name,
                                    executor_instance=executor_instance,
                                )
                                if disabled_skill_name is not None:
                                    self.console.print(
                                        f"[yellow]Skill '{disabled_skill_name}' is disabled.[/yellow]"
                                    )
                                    self.console.print(
                                        f"[dim]Use /skills enable {disabled_skill_name} to enable it[/dim]"
                                    )
                                    continue
                                # Command not found in registry
                                self.console.print(f"[yellow]Unknown command: /{cmd_name}[/yellow]")
                                self.console.print("[dim]Type /help to see available commands[/dim]")
                                continue
                            if command:
                                # Create command context
                                cmd_context = CommandContext(
                                    cwd=os.getcwd(),
                                    user_args=cmd_args,
                                    sandbox=None,  # TODO: Pass actual sandbox if available
                                    agent_config=None,  # TODO: Pass agent config if needed
                                    runtime=runtime,
                                    session_id=getattr(executor_instance, "session_id", None),
                                )

                                try:
                                    # Pre-execute validation
                                    error = await command.pre_execute(cmd_context)
                                    if error:
                                        self.console.print(f"[red]Error: {error}[/red]")
                                        continue

                                    # Route by command type
                                    if command.command_type == "tool":
                                        # Tool command: Direct execution
                                        result = await command.execute(cmd_context)
                                        self.console.print(result)
                                        continue
                                    else:
                                        # Prompt command: Generate prompt for agent, then execute with tool filtering
                                        prompt = await command.get_prompt(cmd_context)

                                        # Apply tool filtering if executor_instance has a swarm
                                        if executor_instance and hasattr(executor_instance, 'swarm'):
                                            from .core.tool_filter import temporary_tool_filter

                                            # Get allowed tools from command
                                            allowed_tools = command.allowed_tools if command.allowed_tools else None

                                            if allowed_tools:
                                                logger.info(f"Command /{cmd_name} restricting tools to: {allowed_tools}")

                                            # Execute with tool filtering
                                            with temporary_tool_filter(executor_instance.swarm, allowed_tools):
                                                # Print agent name before response
                                                self.console.print(f"[bold green]{agent_name}[/bold green]:")

                                                # Execute the prompt
                                                try:
                                                    response = await self._run_executor_prompt(
                                                        prompt,
                                                        executor,
                                                        executor_instance=executor_instance,
                                                    )
                                                    # Response is returned for potential future use
                                                except Exception as exec_error:
                                                    import traceback
                                                    logger.error(f"Error executing command /{cmd_name}: {exec_error}\n{traceback.format_exc()}")
                                                    self.console.print(f"[bold red]Error executing command: {exec_error}[/bold red]")

                                            # Command executed, continue to next input
                                            continue
                                        else:
                                            # No tool filtering available, execute normally
                                            logger.warning(f"Tool filtering not available for command /{cmd_name} (no swarm found)")
                                            user_input = prompt  # Replace input with generated prompt
                                            # Fall through to normal execution

                                except Exception as e:
                                    import traceback
                                    logger.error(f"Error executing command /{cmd_name}: {e}\n{traceback.format_exc()}")
                                    self.console.print(f"[red]Error executing command: {e}[/red]")
                                    continue

                # Handle explicit exit commands
                normalized_input = user_input.lower()
                force_exit = normalized_input in ("exit!", "quit!", "/exit!", "/quit!")
                if normalized_input in ("exit", "quit", "/exit", "/quit") or force_exit:
                    try:
                        should_exit, follow_up_prompt = await self._apply_stop_hooks(
                            executor_instance=executor_instance,
                            force=force_exit,
                        )
                    except Exception as e:
                        logger.warning(f"STOP hook execution failed: {e}")
                        should_exit, follow_up_prompt = True, None

                    if not should_exit:
                        if follow_up_prompt:
                            await self._execute_follow_up_prompt(
                                agent_name=agent_name,
                                executor=executor,
                                follow_up_prompt=follow_up_prompt,
                            )
                        continue
                    self.console.print("[dim]Bye[/dim]")
                    await self._stop_notification_poller()
                    return False

                # Handle help command - show system information
                if user_input.lower() in ("/help", "help"):
                    self._display_system_info(help_text)
                    continue

                # Handle new session command
                if user_input.lower() in ("/new", "new"):
                    if executor_instance and hasattr(executor_instance, 'new_session'):
                        executor_instance.new_session()
                        # Update session_id_info display
                        if hasattr(executor_instance, 'session_id'):
                            self.console.print(f"[dim]Current session: {executor_instance.session_id}[/dim]")
                    else:
                        self.console.print("[yellow]⚠️ Session management not available for this executor.[/yellow]")
                    continue
                
                # Handle restore session command
                if user_input.lower() in ("/restore", "restore", "/latest", "latest"):
                    if executor_instance and hasattr(executor_instance, 'restore_session'):
                        restored_id = executor_instance.restore_session()
                        # Update session_id_info display
                        self.console.print(f"[dim]Current session: {restored_id}[/dim]")
                    else:
                        self.console.print("[yellow]⚠️ Session restore not available for this executor.[/yellow]")
                    continue
                
                # Handle switch command
                if user_input.lower().startswith(("/switch", "switch")):
                    parts = user_input.split(maxsplit=1)
                    if len(parts) > 1:
                         target_agent = parts[1]
                         # Validate agent existence
                         if target_agent in agent_names:
                             await self._stop_notification_poller()
                             return target_agent
                         else:
                             self.console.print(f"[red]Agent '{target_agent}' not found.[/red]")
                             continue
                    else:
                        await self._stop_notification_poller()
                        return True # Return True to switch agent (show list)
                
                # Handle skills command
                if (
                    user_input.lower().startswith("/skills")
                    or user_input.lower() == "skills"
                    or (user_input.startswith("/") and " " not in user_input.strip())
                ):
                    handled = await self._handle_skills_command(
                        user_input,
                        agent_name=agent_name,
                        executor_instance=executor_instance,
                    )
                    if handled:
                        continue
                
                # Handle cost command (query history + token usage)
                cost_input = user_input.strip().lower()
                if cost_input in ("/cost", "cost") or cost_input in ("/cost -all", "cost -all"):
                    try:
                        from pathlib import Path
                        from .history import JSONLHistory
                        
                        history_path = Path.home() / ".aworld" / "cli_history.jsonl"
                        if not history_path.exists():
                            self.console.print("[yellow]No history file found. Start chatting to generate history.[/yellow]")
                            continue
                        
                        history = JSONLHistory(str(history_path))
                        show_all = "-all" in cost_input
                        
                        if show_all:
                            self.console.print(history.format_cost_display(session_id=None))
                        else:
                            current_session_id = None
                            if executor_instance and hasattr(executor_instance, 'session_id'):
                                current_session_id = executor_instance.session_id
                            if current_session_id:
                                self.console.print(history.format_cost_display(session_id=current_session_id))
                            else:
                                self.console.print("[yellow]No current session. Use /cost -all for global history.[/yellow]")
                        
                    except Exception as e:
                        self.console.print(f"[red]Error displaying cost: {e}[/red]")
                        import traceback
                        traceback.print_exc()
                    continue

                # Handle compact command (context compression)
                if user_input.lower() in ("/compact", "compact"):
                    try:
                        from .core.context import run_context_optimization, check_session_token_limit
                        from .executors.stats import format_context_bar, format_tokens
                        from aworld.models.utils import ModelUtils

                        if not executor_instance:
                            self.console.print("[yellow]⚠️ No executor instance available for compression.[/yellow]")
                            continue

                        # Get session_id from executor
                        session_id = getattr(executor_instance, 'session_id', None)
                        if not session_id:
                            self.console.print("[yellow]⚠️ No session ID available for compression.[/yellow]")
                            continue

                        # Get agent name from executor or swarm
                        agent_id = agent_name
                        if hasattr(executor_instance, 'swarm') and executor_instance.swarm:
                            swarm = executor_instance.swarm
                            if hasattr(swarm, 'agent_graph') and swarm.agent_graph:
                                agents = swarm.agent_graph.agents
                                if agents:
                                    # Get first agent name
                                    agent_id = list(agents.keys())[0] if agents.keys() else agent_name

                        # Get current context usage BEFORE compression
                        try:
                            _, stats_before, _ = check_session_token_limit(
                                session_id=session_id,
                                agent_name=agent_id
                            )

                            # Get model name from executor config
                            model_name = None
                            if hasattr(executor_instance, 'conf') and hasattr(executor_instance.conf, 'llm_model_name'):
                                model_name = executor_instance.conf.llm_model_name
                            elif hasattr(executor_instance, 'swarm') and hasattr(executor_instance.swarm, 'conf'):
                                model_name = executor_instance.swarm.conf.llm_model_name

                            # Calculate and display current usage
                            if stats_before and model_name:
                                by_agent = stats_before.get("by_agent", {})
                                agent_stats = by_agent.get(agent_id, {})
                                current_tokens = agent_stats.get("context_window_tokens", 0)

                                if current_tokens > 0:
                                    max_tokens = ModelUtils.get_context_window(model_name)
                                    if max_tokens > 0:
                                        context_bar = format_context_bar(current_tokens, max_tokens, bar_width=10)
                                        self.console.print(f"[dim]Current usage: {context_bar}  ({format_tokens(current_tokens)}/{format_tokens(max_tokens)})[/dim]")
                        except Exception as e:
                            logger.debug(f"Could not display pre-compression stats: {e}")

                        # Run compression with progress indicator, timeout, and interrupt handling
                        try:
                            self.console.print("[bold cyan]⏳ Starting context compression...[/bold cyan]")
                            self.console.print("[dim]Press Ctrl+C to cancel[/dim]")

                            # Create the compression task
                            compression_task = asyncio.create_task(
                                run_context_optimization(
                                    agent_id=agent_id,
                                    session_id=session_id
                                )
                            )

                            # Wait with timeout
                            ok, tokens_before, tokens_after, msg, compressed_content = await asyncio.wait_for(
                                compression_task,
                                timeout=90.0
                            )

                            if ok:
                                # If this is first compression (tokens_before == 0), show generation message
                                if tokens_before == 0:
                                    self.console.print(
                                        f"[green]✓[/green] Context compressed: Generated {tokens_after:,} tokens from history"
                                    )
                                else:
                                    ratio = ((tokens_before - tokens_after) / tokens_before) * 100
                                    self.console.print(
                                        f"[green]✓[/green] Context compressed: {tokens_before:,} → {tokens_after:,} tokens ([bold]{ratio:.1f}%[/bold] reduction)"
                                    )

                                # Show AFTER compression context usage
                                try:
                                    _, stats_after, _ = check_session_token_limit(
                                        session_id=session_id,
                                        agent_name=agent_id
                                    )

                                    if stats_after and model_name:
                                        by_agent = stats_after.get("by_agent", {})
                                        agent_stats = by_agent.get(agent_id, {})
                                        new_tokens = agent_stats.get("context_window_tokens", 0)

                                        if new_tokens > 0:
                                            max_tokens = ModelUtils.get_context_window(model_name)
                                            if max_tokens > 0:
                                                context_bar = format_context_bar(new_tokens, max_tokens, bar_width=10)
                                                self.console.print(f"[dim]New usage:     {context_bar}  ({format_tokens(new_tokens)}/{format_tokens(max_tokens)})[/dim]")
                                except Exception as e:
                                    logger.debug(f"Could not display post-compression stats: {e}")

                                # Compressed content contains internal analysis (XML tags, etc.) - not shown to user
                                # It's saved to memory for agent use, no need to display technical details
                            else:
                                self.console.print(f"[yellow]⚠️ {msg}[/yellow]")

                        except asyncio.TimeoutError:
                            # Cancel the task on timeout
                            if not compression_task.done():
                                compression_task.cancel()
                                try:
                                    await compression_task
                                except asyncio.CancelledError:
                                    pass
                            self.console.print("[red]✗[/red] Context compression timed out (90s limit)")
                            self.console.print("[dim]Operation was cancelled. Try again later or check your API configuration.[/dim]")

                        except asyncio.CancelledError:
                            self.console.print("[yellow]⚠️[/yellow] Context compression cancelled by user")

                    except KeyboardInterrupt:
                        self.console.print("\n[yellow]⚠️[/yellow] Context compression interrupted by user (Ctrl+C)")
                        # Don't re-raise, just continue to next prompt
                        continue

                    except Exception as e:
                        self.console.print(f"[red]✗[/red] Error running compression: {e}")
                        import traceback
                        logger.error(f"Compression error: {e}\n{traceback.format_exc()}")
                    continue

                # Handle team command
                if user_input.lower().startswith("/team"):
                    # await self.team_handler.handle_command(user_input)
                    continue

                # Handle agents command
                if user_input.lower() in ("/agents", "agents"):
                    try:
                        from .runtime.cli import CliRuntime
                        from .runtime.loaders import PluginLoader
                        from aworld_cli.core.agent_scanner import global_agent_registry
                        from pathlib import Path

                        built_in_agents = []
                        user_agents = []
                        base_path = os.path.expanduser(
                            os.environ.get('AGENTS_PATH', '~/.aworld/agents'))

                        # Load Built-in agents from plugins using PluginLoader
                        try:
                            # Get built-in plugin directories
                            runtime = CliRuntime()
                            plugin_dirs = list(runtime.plugin_dirs)
                            plugin_dirs.extend(getattr(runtime, "builtin_agent_dirs", []))

                            # Load agents from each plugin using PluginLoader
                            for plugin_dir in plugin_dirs:
                                try:
                                    loader = PluginLoader(plugin_dir, console=self.console)
                                    # Load agents from plugin (this also loads skills internally)
                                    plugin_agents = await loader.load_agents()
                                    # Mark as Built-in agents
                                    for agent in plugin_agents:
                                        if not hasattr(agent, 'source_type') or not agent.source_type:
                                            agent.source_type = "BUILT-IN"
                                    built_in_agents.extend(plugin_agents)
                                except Exception as e:
                                    logger.info(f"Failed to load Built-in agents from plugin {plugin_dir.name}: {e}")
                                    import traceback
                                    logger.debug(traceback.format_exc())
                        except Exception as e:
                            logger.info(f"Failed to load Built-in agents from plugins: {e}")
                        
                        # Load User agents from AgentScanner default instance
                        try:
                            agent_list = await global_agent_registry.list_desc()
                            for item in agent_list:
                                # Handle both old format (4-tuple with version) and new format (3-tuple)
                                if len(item) == 4:
                                    name, desc, path, version = item
                                else:
                                    name, desc, path = item
                                    version = None
                                agent_info = AgentInfo(
                                    name=name,
                                    desc=desc,
                                    metadata={"version": version} if version else {},
                                    source_type="USER",
                                    source_location=base_path
                                )
                                user_agents.append(agent_info)
                        except Exception as e:
                            logger.info(f"Failed to load User agents from registry: {e}")
                        
                        # Log summary
                        total_agents = len(built_in_agents) + len(user_agents)
                        logger.info(f"Loaded {total_agents} agent(s): {len(built_in_agents)} from Built-in plugins, {len(user_agents)} from User registry ({base_path})")
                        
                        # Display Built-in agents in a separate table
                        if built_in_agents:
                            self.console.print("\n[bold cyan]Built-in Agents:[/bold cyan]")
                            self.display_agents(built_in_agents, source_type="BUILT-IN")
                        else:
                            self.console.print("[dim]No Built-in agents available.[/dim]")
                        
                        # Display User agents in a separate table
                        if user_agents:
                            self.console.print("\n[bold cyan]User Agents:[/bold cyan]")
                            self.display_agents(user_agents, source_type="USER", source_location=base_path)
                        else:
                            self.console.print("[dim]No User agents available.[/dim]")
                        
                        if not built_in_agents and not user_agents:
                            self.console.print("[yellow]⚠️  No agents available.[/yellow]")
                    except Exception as e:
                        logger.info(f"Error loading agents: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())
                    continue

                # Handle visualize command
                if user_input.lower() in ("/visualize_trajectory", "visualize_trajectory"):
                    self._visualize_team(executor_instance)
                    continue

                # Handle sessions command
                if user_input.lower() in ("/sessions", "sessions"):
                    if executor_instance:
                        # Debug: Print session related attributes
                        session_attrs = {k: v for k, v in executor_instance.__dict__.items() if 'session' in k.lower()}
                        # Also check if context has session info
                        if hasattr(executor_instance, 'context') and executor_instance.context:
                            context_session_attrs = {k: v for k, v in executor_instance.context.__dict__.items() if
                                                     'session' in k.lower()}
                            session_attrs.update({f"context.{k}": v for k, v in context_session_attrs.items()})

                        if session_attrs:
                            self.console.print(f"[dim]Session Info: {session_attrs}[/dim]")
                    else:
                        self.console.print("[yellow]No executor instance available.[/yellow]")
                    continue

                # Print agent name before response
                self.console.print(f"[bold green]{agent_name}[/bold green]:")

                try:
                    # Hooks V2: allow hooks to rewrite or deny user input before executor runs.
                    try:
                        should_execute, user_input = await self._apply_user_input_hooks(
                            user_input,
                            executor_instance=executor_instance
                        )
                    except Exception as e:
                        logger.warning(f"USER_INPUT_RECEIVED hook execution failed: {e}")
                        should_execute = True

                    if not should_execute:
                        continue

                    # File parsing is now handled by FileParseHook automatically.
                    self._is_agent_executing = True
                    try:
                        response = await self._run_executor_with_active_steering(
                            prompt=user_input,
                            executor=executor,
                            completer=completer,
                            runtime=runtime,
                            agent_name=agent_name,
                            executor_instance=executor_instance,
                            is_terminal=is_terminal,
                        )
                        # Response is returned for potential future use, but content is already printed by executor
                    finally:
                        self._is_agent_executing = False
                except Exception as e:
                    import traceback
                    logger.error(f"Error executing task: {e} {traceback.format_exc()}")
                    self.console.print(f"[bold red]Error executing task: {e}[/bold red]")
                    continue

            except KeyboardInterrupt:
                buf_content = ""
                if is_terminal and session is not None:
                    try:
                        buf = getattr(session, "default_buffer", None)
                        if buf is not None:
                            buf_content = (buf.text or "").strip()
                    except Exception:
                        pass
                if buf_content:
                    logger.info(f"\n[yellow]Interrupted. Input buffer: {buf_content!r}[/yellow]")
                    continue  # Stay in chat loop, show prompt again
                else:
                    try:
                        should_exit, follow_up_prompt = await self._apply_stop_hooks(
                            executor_instance=executor_instance
                        )
                    except Exception as e:
                        logger.warning(f"STOP hook execution failed: {e}")
                        should_exit, follow_up_prompt = True, None

                    if not should_exit:
                        if follow_up_prompt:
                            await self._execute_follow_up_prompt(
                                agent_name=agent_name,
                                executor=executor,
                                follow_up_prompt=follow_up_prompt,
                            )
                        continue
                    logger.info("\n[yellow]Interrupted. Exiting...[/yellow]")
                    self.console.print("[dim]Bye[/dim]")
                    await self._stop_notification_poller()
                    return False  # Exit CLI when buffer is empty
            except Exception as e:
                import traceback
                logger.error(f"Error executing task: {e} {traceback.format_exc()}")
                self.console.print(f"[red]An unexpected error occurred: {e}[/red]")
                continue  # Add continue to prevent fall-through

        await self._stop_notification_poller()
        return False
