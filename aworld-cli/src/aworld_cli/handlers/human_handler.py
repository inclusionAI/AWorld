"""
CLI Human Handler for aworld_cli
Simple handler for human input in CLI
"""

import asyncio
from typing import Optional

from aworld.core.event.base import Constants, Message
from aworld.logs.util import logger
from aworld.runners import HandlerFactory
from aworld.runners.handler.human import DefaultHumanHandler
from rich.prompt import Prompt
from rich.panel import Panel

from .._globals import console


@HandlerFactory.register(name=f'__{Constants.HUMAN}__', prio=100)
class CLIHumanHandler(DefaultHumanHandler):
    """
    CLI Human Handler for aworld_cli
    
    Simple handler that just prompts for user input.
    
    Example:
        handler = CLIHumanHandler(runner)
        user_input = await handler.handle_user_input(message)
    """

    def __init__(self, runner):
        super().__init__(runner)
        self.runner = runner
        self.console = console

    def _format_payload(self, payload: str) -> str:
        """
        Format payload content for display, preserving structure while cleaning up.
        
        Args:
            payload: Raw payload content
            
        Returns:
            Formatted payload string
        """
        if not payload:
            return ""
        
        # Clean up excessive whitespace but preserve intentional structure
        lines = []
        for line in payload.split('\n'):
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
            elif lines and lines[-1]:  # Preserve single blank line between paragraphs
                lines.append('')
        
        return '\n'.join(lines)
    
    def _get_short_prompt(self, payload: str) -> str:
        """
        Get a simple prompt text for the input field.
        
        Args:
            payload: Full payload content (not used, kept for interface consistency)
            
        Returns:
            Simple prompt string for input field
        """
        return "Please Input"
    
    async def handle_user_input(self, message: Message) -> Optional[str]:
        """
        Handle user input - display formatted payload and prompt for input
        
        Args:
            message: Message object containing the input request
            
        Returns:
            User's input as string, or None if input failed/cancelled
        """
        try:
            message.context.cli = self.console
            
            # Add a blank line for visual separation
            self.console.print()
            
            payload = message.payload or ""
            
            # Display formatted payload content if available
            if payload:
                formatted_payload = self._format_payload(payload)
                # Display in a subtle panel without title for cleaner look
                self.console.print(
                    Panel(
                        formatted_payload,
                        border_style="dim",
                        padding=(1, 2),
                        title=None
                    )
                )
                self.console.print()  # Add spacing before prompt
            
            # Get short prompt for input field
            prompt_text = self._get_short_prompt(payload)
            
            # Display input prompt with consistent styling
            user_input = await asyncio.to_thread(
                Prompt.ask,
                f"[cyan]{prompt_text}[/cyan]",
                console=self.console
            )
            
            user_input = user_input.strip()
            
            if user_input:
                logger.info(f"✅ Human input received: {user_input[:100]}...")
            
            return user_input if user_input else None
            
        except KeyboardInterrupt:
            logger.info("❌ User cancelled input")
            return None
        except Exception as e:
            logger.error(f"❌ Error handling input: {e}")
            if self.console:
                self.console.print(f"[red]❌ Error: {e}[/red]")
            return None
