"""
Remote agent executor with streaming support.
"""
import uuid
import json
import httpx
from typing import Optional, Union, List
from rich.console import Console
from rich.markdown import Markdown
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

