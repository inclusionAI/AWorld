"""
Remote agent executor with streaming support.
"""
import uuid
import json
import httpx
from typing import Optional
from rich.console import Console
from rich.markdown import Markdown
from .base import AgentExecutor


class RemoteAgentExecutor(AgentExecutor):
    """Executor for remote agents with streaming support."""
    
    def __init__(self, backend_url: str, agent_name: str, console: Optional[Console] = None):
        """
        Initialize remote agent executor.
        
        Args:
            backend_url: Backend server URL
            agent_name: Name of the agent
            console: Rich console for output
            
        Example:
            >>> executor = RemoteAgentExecutor("http://localhost:8000", "MyAgent")
        """
        self.backend_url = backend_url
        self.agent_name = agent_name
        self.session_id = str(uuid.uuid4())
        self.user_id = "cli-user"  # Could be configurable
        self.console = console or Console()
    
    async def chat(self, message: str) -> str:
        """
        Send chat message and handle streaming response.
        
        Args:
            message: User message to send
            
        Returns:
            Complete response content as string
            
        Example:
            >>> executor = RemoteAgentExecutor("http://localhost:8000", "MyAgent")
            >>> response = await executor.chat("Hello")
        """
        async with httpx.AsyncClient(timeout=300.0) as client:
            url = f"{self.backend_url}/chat/completions"
            
            payload = {
                "model": self.agent_name,
                "messages": [
                    {"role": "user", "content": message}
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
                            self.console.print(Markdown(content))
                            return content
                        else:
                            error_msg = f"Error: Unexpected response format: {data}"
                            self.console.print(f"[red]{error_msg}[/red]")
                            return error_msg
                    
                    # Handle streaming response (SSE format)
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
                                    
                                    # Extract content from OpenAI format
                                    if "choices" in data and len(data["choices"]) > 0:
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
                                        
                                        # Handle error
                                        if "error" in data:
                                            error_msg = data["error"].get("detail", str(data["error"]))
                                            self.console.print(f"\n[red]Error: {error_msg}[/red]")
                                            return f"Error: {error_msg}"
                                
                                except json.JSONDecodeError:
                                    # Skip invalid JSON lines
                                    continue
                    
                    # Print newline after streaming completes
                    self.console.print()
                    return full_content
                    
            except httpx.HTTPStatusError as e:
                error_msg = f"Server Error: {e.response.status_code} - {e.response.text}"
                self.console.print(f"[red]{error_msg}[/red]")
                return error_msg
            except Exception as e:
                error_msg = f"Connection Error: {str(e)}"
                self.console.print(f"[red]{error_msg}[/red]")
                return error_msg

__all__ = ["RemoteAgentExecutor"]

