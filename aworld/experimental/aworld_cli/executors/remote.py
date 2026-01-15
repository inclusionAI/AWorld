"""
Remote agent executor with streaming support.
"""
import uuid
import json
import httpx
from typing import Optional, Union, List, Dict, Any
from rich.console import Console
from rich.markdown import Markdown
from rich.status import Status
from .base_executor import BaseAgentExecutor


class RemoteAgentExecutor(BaseAgentExecutor):
    """
    Executor for remote agents with streaming support.
    
    Only responsible for:
    - Building HTTP requests
    - Executing HTTP calls
    - Adapting HTTP SSE stream to output format
    
    All other capabilities (session management, output rendering, logging) are inherited from BaseAgentExecutor.
    """
    
    def __init__(
        self, 
        backend_url: str, 
        agent_name: str, 
        console: Optional[Console] = None,
        session_id: Optional[str] = None
    ):
        """
        Initialize remote agent executor.
        
        Args:
            backend_url: Backend server URL
            agent_name: Name of the agent
            console: Rich console for output
            session_id: Optional session ID. If None, will generate one automatically.
            
        Example:
            >>> executor = RemoteAgentExecutor("http://localhost:8000", "MyAgent")
        """
        # Initialize base executor (handles session management, logging, etc.)
        super().__init__(console=console, session_id=session_id)
        
        # Remote-specific initialization
        self.backend_url = backend_url
        self.agent_name = agent_name
        self.user_id = "cli-user"  # Could be configurable
        # Track activity status for clearing previous state
        self._activity_status: Optional[Status] = None
    
    def _process_output_data(self, data: Dict[str, Any], full_content: str) -> tuple[str, bool]:
        """
        Process parsed JSON output data and extract content.
        Classifies output by metadata["type"] according to Output base classes.
        
        Args:
            data: Parsed JSON data from SSE stream
            full_content: Current accumulated content string
            
        Returns:
            Tuple of (updated_full_content, should_continue)
            - updated_full_content: Content string with new data appended
            - should_continue: False if should stop processing (e.g., error or done)
            
        Example:
            >>> executor = RemoteAgentExecutor("http://localhost:8000", "MyAgent")
            >>> content, continue_processing = executor._process_output_data(
            ...     {"metadata": {"type": "activity"}, "activity_type": "STEP", "data": "生成大纲"},
            ...     ""
            ... )
        """
        # Get metadata and output type
        metadata = data.get("metadata", {})
        output_type = metadata.get("type", "default")
        output_data = data.get("data", "")
        
        # Classify by metadata["type"] according to Output base classes
        if output_type == "activity":
            # ActivityOutput: display data field and clear previous activity state
            if output_data:
                activity_text = str(output_data)
                
                # Use Rich Status to automatically clear and update the line
                formatted_text = f"[dim]📋 {activity_text}[/dim]"
                
                if self._activity_status:
                    # Update existing status (automatically clears previous line)
                    self._activity_status.update(formatted_text)
                else:
                    # Create new status
                    self._activity_status = Status(formatted_text, console=self.console)
                    self._activity_status.start()
                
                full_content += activity_text + "\n"
        
        elif output_type == "step":
            # StepOutput: step information
            step_name = data.get("name", "")
            alias_name = data.get("alias_name", "")
            status = data.get("status", "START")
            show_name = alias_name if alias_name else step_name
            
            if status == "START":
                self.console.print(f"[dim]🚀 Step started: {show_name}[/dim]")
            elif status == "FINISHED":
                self.console.print(f"[dim]✅ Step finished: {show_name}[/dim]")
            elif status == "FAILED":
                self.console.print(f"[red]❌ Step failed: {show_name}[/red]")
            
            if output_data:
                full_content += str(output_data) + "\n"
        
        elif output_type == "message":
            # MessageOutput: LLM message output
            response = data.get("response", "")
            reasoning = data.get("reasoning", "")
            
            if reasoning:
                self.console.print(f"[dim]💭 Reasoning: {reasoning}[/dim]")
                full_content += reasoning + "\n"
            
            if response:
                self.console.print(response)
                full_content += response
        
        elif output_type == "tool_call":
            # ToolCallOutput: tool call information
            tool_call_data = output_data if output_data else data.get("tool_call", {})
            if isinstance(tool_call_data, dict):
                function_name = tool_call_data.get("function", {}).get("name", "unknown")
                self.console.print(f"[dim]🔧 Tool call: {function_name}[/dim]")
            full_content += str(output_data) + "\n"
        
        elif output_type == "tool_call_result":
            # ToolResultOutput: tool execution result
            tool_name = data.get("tool_name", "unknown")
            action_name = data.get("action_name", "")
            tool_info = f"{tool_name}"
            if action_name:
                tool_info += f" → {action_name}"
            
            if output_data:
                self.console.print(f"[dim]🔧 Tool result: {tool_info}[/dim]")
                # Show preview for long results
                data_str = str(output_data)
                if len(data_str) > 200:
                    preview = data_str[:200] + "..."
                    self.console.print(f"[dim]  {preview}[/dim]")
                else:
                    self.console.print(f"[dim]  {data_str}[/dim]")
            full_content += str(output_data) + "\n"
        
        elif output_type == "task_result":
            # TaskResultOutput: final task result
            if output_data:
                # Check if output_data is JSON (string or dict/list)
                is_json = False
                parsed_data = None
                formatted_output = output_data
                
                # If it's already a dict or list
                if isinstance(output_data, (dict, list)):
                    is_json = True
                    parsed_data = output_data
                    formatted_output = json.dumps(output_data, indent=2, ensure_ascii=False)
                # If it's a string, try to parse as JSON
                elif isinstance(output_data, str):
                    try:
                        parsed_data = json.loads(output_data)
                        is_json = True
                        formatted_output = json.dumps(parsed_data, indent=2, ensure_ascii=False)
                    except (json.JSONDecodeError, ValueError):
                        # Not valid JSON, use as-is
                        parsed_data = None
                        formatted_output = output_data
                
                # Display formatted output
                if is_json and isinstance(parsed_data, dict) and "content" in parsed_data:
                    # If JSON has "content" field, display content as Markdown
                    content = parsed_data.get("content", "")
                    if content:
                        self.console.print(Markdown(content, code_theme="default", inline_code_theme="default"))
                    # Display other fields as JSON if any
                    other_fields = {k: v for k, v in parsed_data.items() if k != "content"}
                    if other_fields:
                        other_json = json.dumps(other_fields, indent=2, ensure_ascii=False)
                        from rich.syntax import Syntax
                        syntax = Syntax(other_json, "json", theme="default", line_numbers=False)
                        self.console.print(syntax)
                elif is_json:
                    # Use syntax highlighting for JSON
                    from rich.syntax import Syntax
                    syntax = Syntax(formatted_output, "json", theme="default", line_numbers=False)
                    self.console.print(syntax)
                else:
                    self.console.print(f"[green]{formatted_output}[/green]")
                
                full_content += str(output_data)
        
        elif output_type == "finished_signal":
            # RunFinishedSignal: task finished signal
            self.console.print("[green]✅ Task finished[/green]")
        
        # Extract content from OpenAI format (fallback for compatibility)
        elif "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            
            # Handle delta format (streaming)
            if "delta" in choice:
                delta = choice["delta"]
                if "content" in delta:
                    content_chunk = delta["content"]
                    full_content += content_chunk
                    # Print content chunk immediately for streaming effect
                    self.console.print(content_chunk, end="", style="dim")
            
            # Handle message format (non-streaming chunk)
            elif "message" in choice:
                message = choice["message"]
                if "content" in message:
                    content_chunk = message["content"]
                    full_content += content_chunk
                    self.console.print(content_chunk, end="", style="dim")
        
        # Handle default/unknown output type
        else:
            # If data field exists, try to display it
            if output_data:
                self.console.print(f"[dim]📦 Output: {output_data}[/dim]")
                full_content += str(output_data) + "\n"
        
        # Handle error (check after all type processing)
        if "error" in data:
            error_msg = data["error"].get("detail", str(data["error"]))
            self.console.print(f"\n[red]Error: {error_msg}[/red]")
            return full_content, False  # Stop processing on error
        
        return full_content, True  # Continue processing
    
    async def chat(self, message: Union[str, tuple[str, List[str]]]) -> str:
        """
        Send chat message and handle streaming response.
        
        Args:
            message: User message to send (string or tuple of (text, image_urls) for multimodal)
                    Multimodal format: (text, [image_data_url1, image_data_url2, ...])
            
        Returns:
            Complete response content as string
            
        Example:
            >>> executor = RemoteAgentExecutor("http://localhost:8000", "MyAgent")
            >>> # Text only
            >>> response = await executor.chat("Hello")
            >>> # With images (remote executor may need to convert to appropriate format)
            >>> response = await executor.chat(("Analyze this", ["data:image/jpeg;base64,..."]))
        """
        # Update session last used time (inherited from BaseAgentExecutor)
        self._update_session_last_used(self.session_id)
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            url = f"{self.backend_url}/chat/completions"
            
            # Handle both string and tuple format
            if isinstance(message, tuple):
                # Multimodal content - convert to OpenAI format
                text, image_urls = message
                content = [{"type": "text", "text": text}]
                for img_url in image_urls:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": img_url}
                    })
            else:
                # String content
                content = message
            
            payload = {
                "model": self.agent_name,
                "messages": [
                    {"role": "user", "content": content}
                ],
                "stream": True
            }
            
            headers = {
                "x-aworld-user-id": self.user_id,
                "x-aworld-session-id": self.session_id,
                "x-aworld-message-id": str(uuid.uuid4()),
                "x-aworld-task-id": str(uuid.uuid4())
            }
            
            try:
                # Use stream=True to handle streaming response
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    
                    # Check if response is streaming (text/event-stream)
                    content_type = response.headers.get("content-type", "")
                    is_streaming = "text/event-stream" in content_type or "stream" in content_type.lower()
                    if not is_streaming:
                        # Non-streaming response, parse as JSON
                        data = response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            content = data["choices"][0]["message"]["content"]
                            # Print non-streaming response
                            # Set code_theme to None to disable code block background color
                            self.console.print(Markdown(content, code_theme="default", inline_code_theme="default"))
                            return content
                        else:
                            error_msg = f"Error: Unexpected response format: {data}"
                            self.console.print(f"[red]{error_msg}[/red]")
                            return error_msg
                    
                    # Handle streaming response (SSE format)
                    # TODO: In the future, we can adapt HTTP SSE stream to unified format
                    # and use base's output rendering capabilities
                    full_content = ""
                    buffer = ""
                    
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                            
                        # Decode chunk and add to buffer
                        buffer += chunk.decode('utf-8', errors='ignore')
                        
                        # Process complete lines
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()
                            
                            if not line:
                                continue
                            
                            # Handle SSE format: "data: {...}" or "data: [DONE]"
                            if line.startswith('data: '):
                                data_str = line[6:]  # Remove "data: " prefix
                                
                                if data_str == '[DONE]':
                                    # Stream ended
                                    break
                                
                                try:
                                    # Parse JSON data
                                    data = json.loads(data_str)
                                    
                                    # Process output data using extracted method
                                    full_content, should_continue = self._process_output_data(data, full_content)
                                    
                                    # Stop processing if error occurred
                                    if not should_continue:
                                        return full_content
                                
                                except json.JSONDecodeError:
                                    # Skip invalid JSON lines
                                    continue
                    
                    # Stop activity status if still running
                    if self._activity_status:
                        self._activity_status.stop()
                        self._activity_status = None
                    
                    # Print newline after streaming completes
                    self.console.print()
                    return full_content
                    
            except httpx.HTTPStatusError as e:
                # Stop activity status on error
                if self._activity_status:
                    self._activity_status.stop()
                    self._activity_status = None
                error_msg = f"Server Error: {e.response.status_code} - {e.response.text}"
                self.console.print(f"[red]{error_msg}[/red]")
                return error_msg
            except Exception as e:
                # Stop activity status on error
                if self._activity_status:
                    self._activity_status.stop()
                    self._activity_status = None
                error_msg = f"Connection Error: {str(e)}"
                self.console.print(f"[red]{error_msg}[/red]")
                return error_msg

__all__ = ["RemoteAgentExecutor"]

