"""
Browser MCP Server

This module provides MCP server functionality for browser automation and interaction.
It handles tasks such as web scraping, form submission, and automated browsing using browser-use package.

Main functions:
- mcp_browser_use: Performs browser automation tasks with LLM-friendly output
"""

import asyncio
import json
import os
import time
import traceback

from browser_use import Agent, AgentHistoryList, BrowserProfile
from browser_use.agent.memory.views import MemoryConfig
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from aworld.logs.util import Color
from examples.nanami.actions.base import ActionArguments, ActionCollection, ActionResponse


class BrowserMetadata(BaseModel):
    """Metadata for browser automation results."""

    task: str
    execution_successful: bool
    steps_taken: int | None = None
    downloaded_files: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    execution_time: float | None = None
    error_type: str | None = None
    trace_log_path: str | None = None


class BrowserActionCollection(ActionCollection):
    """MCP service for browser automation using browser-use package.

    Provides comprehensive web automation capabilities including:
    - Web scraping and content extraction
    - Form submission and interaction
    - File downloads and media handling
    - LLM-enhanced browsing with memory
    - Robot detection and paywall handling
    """

    def __init__(self, arguments: ActionArguments) -> None:
        super().__init__(arguments)

        # Load environment variables
        load_dotenv()

        # Extended system prompt for browser automation
        self.extended_browser_system_prompt = """
10. Download:
-  Save the most relevant files (text/image/pdf/...) to local path for further processing
-  **ALWAYS** download the .pdf files for further processing. DO NOT click the link to open the pdf file in new tabs.

11. Robot Detection:
- If the page is a robot detection page, abort immediately. Then navigate to the most authoritative source for similar information instead

# Efficiency Guidelines
0. if download option is available, always **DOWNLOAD** as possible! Also, report the download url link in your result.
1. Use specific search queries with key terms from the task
2. Avoid getting distracted by tangential information
3. If blocked by paywalls, try archive.org or similar alternatives
4. Document each significant finding clearly and concisely
5. Precisely extract the necessary information with minimal browsing steps.
"""

        # Initialize LLM configuration
        self.llm_config = ChatOpenAI(
            model=os.getenv("LLM_MODEL_NAME"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            temperature=1.0,
        )

        # Browser profile configuration
        self.browser_profile = BrowserProfile(
            cookies_file=os.getenv("COOKIES_FILE_PATH"),
            downloads_dir=os.getenv("FILESYSTEM_SERVER_WORKDIR", str(self.workspace)),
        )

        # Log configuration
        self.trace_log_dir = os.getenv("LOG_FILE_PATH", str(self.workspace / "logs"))
        os.makedirs(f"{self.trace_log_dir}/browser_log", exist_ok=True)

        self._color_log("Browser automation service initialized", Color.green)
        self._color_log(f"Downloads directory: {self.browser_profile.downloads_dir}", Color.blue)
        self._color_log(f"Trace logs directory: {self.trace_log_dir}/browser_log", Color.blue)

    def _create_browser_agent(self, task: str) -> Agent:
        """Create a browser agent instance with configured settings.

        Args:
            task: The task description for the browser agent

        Returns:
            Configured Agent instance
        """
        return Agent(
            task=task,
            llm=self.llm_config,
            extend_system_message=self.extended_browser_system_prompt,
            use_vision=True,
            enable_memory=True,
            memory_config=MemoryConfig(llm_instance=self.llm_config),
            browser_profile=self.browser_profile,
            save_conversation_path=f"{self.trace_log_dir}/browser_log/trace.log",
        )

    def _format_extracted_content(self, extracted_content: dict) -> str:
        """Format extracted content to be LLM-friendly.

        Args:
            extracted_content: Raw extracted content from browser execution

        Returns:
            Formatted string suitable for LLM consumption
        """
        if not extracted_content:
            return "No content extracted from browser execution."

        # Structure the content for better LLM understanding
        formatted_parts = []

        # Add main content if available
        if "text" in extracted_content:
            formatted_parts.append(f"**Extracted Text:**\n{extracted_content['text']}")

        # Add URLs if available
        if "urls" in extracted_content:
            urls = extracted_content["urls"]
            if urls:
                formatted_parts.append("**Visited URLs:**\n" + "\n".join(f"- {url}" for url in urls))

        # Add downloaded files if available
        if "downloads" in extracted_content:
            downloads = extracted_content["downloads"]
            if downloads:
                formatted_parts.append("**Downloaded Files:**\n" + "\n".join(f"- {file}" for file in downloads))

        # Add any other structured data
        for key, value in extracted_content.items():
            if key not in ["text", "urls", "downloads"] and value:
                formatted_parts.append(f"**{key.title()}:**\n{value}")

        return "\n\n".join(formatted_parts) if formatted_parts else json.dumps(extracted_content, indent=2)

    async def mcp_browser_use(
        self,
        task: str = Field(description="The task to perform using the browser automation agent"),
        max_steps: int = Field(default=50, description="Maximum number of steps for browser execution"),
        extract_format: str = Field(
            default="markdown", description="Format for extracted content: 'markdown', 'json', or 'text'"
        ),
    ) -> ActionResponse:
        """Perform browser automation tasks using the browser-use package.

        This tool provides comprehensive browser automation capabilities including:
        - Web scraping and content extraction
        - Form submission and automated interactions
        - File downloads and media handling
        - LLM-enhanced browsing with memory and vision
        - Automatic handling of robot detection and paywalls

        Args:
            task: Description of the browser automation task to perform
            max_steps: Maximum number of execution steps (default: 50)
            extract_format: Output format for extracted content

        Returns:
            ActionResponse with LLM-friendly extracted content and execution metadata
        """
        try:
            self._color_log(f"🎯 Starting browser task: {task}", Color.cyan)

            # Create browser agent
            agent = self._create_browser_agent(task)

            start_time = time.time()

            browser_execution: AgentHistoryList = await agent.run(max_steps=max_steps)

            execution_time = time.time() - start_time

            if browser_execution is not None and browser_execution.is_done() and browser_execution.is_successful():
                # Extract and format content
                extracted_content = browser_execution.extracted_content()
                final_result = browser_execution.final_result()

                # Format content based on requested format
                if extract_format.lower() == "json":
                    formatted_content = json.dumps(
                        {"summary": final_result, "extracted_data": extracted_content}, indent=2
                    )
                elif extract_format.lower() == "text":
                    formatted_content = f"{final_result}\n\n{self._format_extracted_content(extracted_content)}"
                else:  # markdown (default)
                    formatted_content = (
                        f"## Browser Automation Result\n\n**Summary:** {final_result}\n\n"
                        f"{self._format_extracted_content(extracted_content)}"
                    )

                # Prepare metadata
                metadata = BrowserMetadata(
                    task=task,
                    execution_successful=True,
                    steps_taken=len(browser_execution.history) if hasattr(browser_execution, "history") else None,
                    downloaded_files=extracted_content.get("downloads", []) if extracted_content else [],
                    visited_urls=extracted_content.get("urls", []) if extracted_content else [],
                    execution_time=execution_time,
                    trace_log_path=f"{self.trace_log_dir}/browser_log/trace.log",
                )

                self._color_log(f"✅ Browser task completed successfully in {execution_time:.2f}s", Color.green)

                return ActionResponse(success=True, message=formatted_content, metadata=metadata.model_dump())

            else:
                # Handle execution failure
                error_msg = "Browser execution failed or was not completed successfully"

                metadata = BrowserMetadata(
                    task=task,
                    execution_successful=False,
                    execution_time=execution_time,
                    error_type="execution_failure",
                    trace_log_path=f"{self.trace_log_dir}/browser_log/trace.log",
                )

                self._color_log(f"❌ {error_msg}", Color.red)

                return ActionResponse(success=False, message=error_msg, metadata=metadata.model_dump())

        except Exception as e:
            error_msg = f"Browser automation failed: {str(e)}"
            error_trace = traceback.format_exc()

            self.logger.error(f"Browser execution error: {error_trace}")

            metadata = BrowserMetadata(
                task=task,
                execution_successful=False,
                error_type="exception",
                trace_log_path=f"{self.trace_log_dir}/browser_log/trace.log",
            )

            self._color_log(f"❌ {error_msg}", Color.red)

            return ActionResponse(
                success=False, message=f"{error_msg}\n\nError details: {error_trace}", metadata=metadata.model_dump()
            )

    def mcp_get_browser_capabilities(self) -> ActionResponse:
        """Get information about browser automation capabilities and configuration.

        Returns:
            ActionResponse with browser service capabilities and current configuration
        """
        capabilities = {
            "automation_features": [
                "Web scraping and content extraction",
                "Form submission and interaction",
                "File downloads and media handling",
                "LLM-enhanced browsing with vision",
                "Memory-enabled browsing sessions",
                "Robot detection and paywall handling",
            ],
            "supported_formats": ["markdown", "json", "text"],
            "configuration": {
                "llm_model": os.getenv("LLM_MODEL_NAME", "Not configured"),
                "downloads_directory": self.browser_profile.downloads_dir,
                "cookies_enabled": bool(os.getenv("COOKIES_FILE_PATH")),
                "trace_logging": True,
                "vision_enabled": True,
                "memory_enabled": True,
            },
        }

        formatted_info = f"""# Browser Automation Service Capabilities

## Features
{chr(10).join(f"- {feature}" for feature in capabilities["automation_features"])}

## Supported Output Formats
{chr(10).join(f"- {fmt}" for fmt in capabilities["supported_formats"])}

## Current Configuration
- **LLM Model:** {capabilities["configuration"]["llm_model"]}
- **Downloads Directory:** {capabilities["configuration"]["downloads_directory"]}
- **Cookies Enabled:** {capabilities["configuration"]["cookies_enabled"]}
- **Vision Enabled:** {capabilities["configuration"]["vision_enabled"]}
- **Memory Enabled:** {capabilities["configuration"]["memory_enabled"]}
- **Trace Logging:** {capabilities["configuration"]["trace_logging"]}
"""

        return ActionResponse(success=True, message=formatted_info, metadata=capabilities)


# Example usage and entry point
if __name__ == "__main__":
    # Default arguments for testing
    args = ActionArguments(
        name="browser_automation_service",
        transport="stdio",
        workspace=None,  # Will use environment variable or home directory
        unittest=True,
    )

    # Initialize and run the browser automation service
    service = BrowserActionCollection(args)
    try:
        # Example usage (commented out for production)
        resp = asyncio.run(service.mcp_browser_use(task="Search for information about Python web scraping"))
        print(resp)
    except Exception as e:
        print(f"An error occurred: {e}: {traceback.format_exc()}")
