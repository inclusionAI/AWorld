import json
import logging
import time
from datetime import datetime
from typing import Any, Dict

# from amnicontext import ApplicationContext
from ..logger import amni_digest_logger, amni_prompt_logger
from aworld.core.agent.base import BaseAgent
from aworld.core.context.prompts.dynamic_variables import ALL_PREDEFINED_DYNAMIC_VARIABLES

from .modelutils import ModelUtils, num_tokens_from_string

# 日志显示配置常量
BORDER_WIDTH = 100  # 边框内容区域宽度
BORDER_PADDING = 4  # 左侧填充宽度
TOTAL_WIDTH = BORDER_WIDTH + BORDER_PADDING + 4  # 总宽度（包含边框字符）

def _generate_separator(style: str = "─") -> str:
    """
    Generate a separator line with the configured border width.
    
    Args:
        style (str): The character to use for the separator line
        
    Returns:
        str: A formatted separator line
    """
    return f"├{style * BORDER_WIDTH}┤"

def _generate_top_border() -> str:
    """Generate the top border of the log box."""
    return f"╭{('─' * BORDER_WIDTH)}╮"

def _generate_bottom_border() -> str:
    """Generate the bottom border of the log box."""
    return f"╰{('─' * BORDER_WIDTH)}╯"

def _format_llm_config(llm_config: Any) -> str:
    """
    Safely format LLM configuration for logging, filtering out sensitive information.
    
    Args:
        llm_config: The LLM configuration object or dictionary
        
    Returns:
        str: A safe string representation of the config without sensitive data
    """
    if not llm_config:
        return "None"
    
    try:
        show_keys =['llm_model_name', 'llm_temperature','max_model_len', 'max_retries']

        if isinstance(llm_config, dict):
            safe_config = {}
            for key, value in llm_config.items():
                if any(key == show_key for show_key in show_keys):
                    safe_config[key] = value
            return str(safe_config)

        
    except Exception:
        # If any error occurs, return a safe fallback
        return "Config (filtered)"

def _format_tools(tools: list) -> str:
    """
    Format tools information for logging, categorizing them by type.
    
    Args:
        tools (list): List of tool objects with function information
        
    Returns:
        str: Formatted string showing tool names and their types
    """
    if not tools:
        return "No tools"
    
    formatted_tools = []
    
    for tool in tools:
        if isinstance(tool, dict) and 'function' in tool:
            function_info = tool['function']
            if isinstance(function_info, dict) and 'name' in function_info:
                tool_name = function_info['name']
                
                # Determine tool type based on name
                if tool_name.startswith('mcp'):
                    tool_type = "mcp"
                elif '_agent' in tool_name:
                    tool_type = "agent_as_tool"
                else:
                    tool_type = "tool"
                
                formatted_tools.append(f"{tool_name}({tool_type})")
    
    if not formatted_tools:
        return "No valid tools"
    
    # Return first tool, additional tools will be logged separately
    result = formatted_tools[0]
    
    # Truncate if the result is too long for the border width
    max_length = BORDER_WIDTH - 13  # Account for "│ 🔨 Tools: " prefix
    if len(result) > max_length:
        result = result[:max_length-3] + "..."
    
    return result

class PromptLogger:
    """Logger class for handling prompt-related logging operations"""

    @staticmethod
    def log_agent_call_llm_messages(agent: BaseAgent, context: "ApplicationContext", messages: list[dict]) -> None:
        """
        Log OpenAI messages to the prompt log file
        
        Args:
            agent (BaseAgent): The agent making the LLM call
            context (ApplicationContext): The current application context
            messages (list[dict]): List of message dictionaries with 'role' and 'content' keys
                                  Format: [{'role': 'user', 'content': 'Hello'}, ...]
        """
        # 记录函数开始时间
        start_time = time.time()

        logger = logging.getLogger("amnicontext_prompt")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 使用更美观的分隔符和格式
        amni_prompt_logger.info(_generate_top_border())
        amni_prompt_logger.info(f"│{'🚀 AGENT EXECUTION START':^{BORDER_WIDTH}}│")
        amni_prompt_logger.info(_generate_separator())
        amni_prompt_logger.info(f"│ 🤖 Context ID: {str(id(context))+'|'+context.task_id+'|'+agent.id()+'|'+str(ts):<{BORDER_WIDTH-12}}  │")
        amni_prompt_logger.info(f"│ 🤖 Agent ID: {agent.id():<{BORDER_WIDTH-12}} │")
        amni_prompt_logger.info(f"│ 📋 Task ID:  {context.task_id:<{BORDER_WIDTH-12}} │")
        amni_prompt_logger.info(f"│ 📝 Task Input: {context.task_input:<{BORDER_WIDTH-13}} │")
        amni_prompt_logger.info(f"│ 👨🏻 User ID:  {context.user_id:<{BORDER_WIDTH-12}} │")
        amni_prompt_logger.info(f"│ 💬 Session ID:  {context.session_id:<{BORDER_WIDTH-14}} │")
        amni_prompt_logger.info(f"│ 🔢 Messages Count (See details in log file(amnicontext_prompt.log) ): {len(messages):<{BORDER_WIDTH-12}} │")
        
        try:
            # Log context length information
            PromptLogger._log_context_length(messages, agent.conf.llm_config['llm_model_name'], context, agent)
        except Exception as e:
            amni_prompt_logger.warning(f"❌ Error logging context length: {str(e)}")

        try:
            # Log facts information
            PromptLogger._log_agent_facts(agent, context)
        except Exception as e:
            amni_prompt_logger.warning(f"❌ Error logging agent facts: {str(e)}")
        
        try:
            amni_prompt_logger.info(f"│ ⚙️ LLM Config: {_format_llm_config(agent.conf.llm_config):<{BORDER_WIDTH-13}} │")
        except Exception as e:
            amni_prompt_logger.warning(f"❌ Error logging LLM config: {str(e)}")
        
        try:
            # Log tools information
            PromptLogger._log_agent_tools(agent)
        except Exception as e:
            amni_prompt_logger.warning(f"❌ Error logging agent tools: {str(e)}")
        
        try:
            amni_prompt_logger.info(_generate_separator())
        except Exception as e:
            amni_prompt_logger.warning(f"❌ Error generating separator: {str(e)}")
        
        try:
            # Log context tree information
            PromptLogger._log_context_tree(context)
        except Exception as e:
            amni_prompt_logger.warning(f"❌ Error logging context tree: {str(e)}")
        
        try:
            # Log OpenAI messages
            PromptLogger._log_messages(messages)
        except Exception as e:
            amni_prompt_logger.warning(f"❌ Error logging messages: {str(e)}")
        
        # 计算并记录函数执行耗时
        end_time = time.time()
        execution_time = end_time - start_time
        logger.info(_generate_separator())
        logger.info(f"│ ⏱️  Execution Time: {execution_time:.3f}s{(' ' * (BORDER_WIDTH - 20))} │")
        logger.info(_generate_bottom_border())


    @staticmethod
    def _log_context_length(messages: list[dict], model_name: str, context: "ApplicationContext", agent: BaseAgent) -> None:
        """
        Log detailed context usage analysis similar to ClaudeCode, showing token breakdown by category.
        
        Args:
            messages (list[dict]): List of message dictionaries to calculate context length from
            model_name (str): The name of the LLM model for context window calculation
            context (ApplicationContext): The current application context
        """
        try:
            # Calculate total context length and breakdown using ModelUtils
            context_window_limit = ModelUtils.get_context_window(model_name)
            token_breakdown = ModelUtils.calculate_token_breakdown(messages, model_name)
            
            total_context_length = token_breakdown['total']
            total_context_length_k = total_context_length / 1024  # Convert to K
            total_percentage = (total_context_length / context_window_limit) * 100

            # Get individual token counts
            system_tokens = token_breakdown['system']
            user_tokens = token_breakdown['user']
            assistant_tokens = token_breakdown['assistant']
            tool_tokens = token_breakdown['tool']
            other_tokens = token_breakdown['other']
            
            # Calculate percentages for each category
            system_percentage = (system_tokens / context_window_limit) * 100
            user_percentage = (user_tokens / context_window_limit) * 100
            assistant_percentage = (assistant_tokens / context_window_limit) * 100
            tool_percentage = (tool_tokens / context_window_limit) * 100
            other_percentage = (other_tokens / context_window_limit) * 100

            # Calculate free space for text data
            free_space = context_window_limit - total_context_length
            free_space_k = free_space / 1024
            free_percentage = (free_space / context_window_limit) * 100

            # Generate context usage grid
            grid = PromptLogger._generate_context_usage_grid(
                system_tokens, user_tokens, assistant_tokens, tool_tokens, other_tokens,
                context_window_limit
            )
            
            # Prepare text data for the right side of the grid
            text_data = [
                "",  # 空行
                f"📊 CONTEXT USAGE {agent.id()}",
                f"{model_name} -> {total_context_length_k:.1f}k/{context_window_limit/1024}k tokens ({total_percentage:.1f}%)",
                f"",
                f"🟦 System: {system_tokens/1024:.1f}k tokens ({system_percentage:.1f}%)",
                f"🟧 User: {user_tokens/1024:.1f}k tokens ({user_percentage:.1f}%)" if user_tokens > 1024 else f"🟧 User: {user_tokens} tokens ({user_percentage:.1f}%)",
                f"🟨 Assistant: {assistant_tokens/1024:.1f}k tokens ({assistant_percentage:.1f}%)",
                f"🟪 Tool: {tool_tokens/1024:.1f}k tokens ({tool_percentage:.1f}%)",
                f"⬜ Free Space: {free_space_k:.1f}k ({free_percentage:.1f}%)",
                "",  # Empty line for spacing
            ]

            # Use render_grid_with_text to display the context usage visualization
            grid_display = PromptLogger.render_grid_with_text(10, 10, grid, text_data)
            
            # Log the grid display
            for line in grid_display.split('\n'):
                if line.strip():  # Skip empty lines
                    amni_prompt_logger.info(f"│ {line}")
            
            amni_prompt_logger.info(_generate_separator())
            if messages and len(messages) > 0 and messages[-1].get('role') != 'assistant':
                amni_digest_logger.info(f"context_length|{agent.id()}|{context.task_id}|{context.user_id}|{context.session_id}|{model_name}|{total_context_length}|{json.dumps(token_breakdown)}")
        except Exception as e:
            # If any error occurs in context length logging, log warning and continue
            try:
                amni_prompt_logger.warning(f"⚠️ Error in context length logging: {str(e)}")
            except:
                # If even warning fails, silently continue
                pass

    @staticmethod
    def render_grid_with_text(x: int, y: int, grid_data: list[str], text_data: list[str]) -> str:
        """
        Render a grid with text on the right side.

        Args:
            x (int): Number of columns in the grid
            y (int): Number of rows in the grid
            grid_data (list[str]): List of grid elements (length should be x*y)
            text_data (list[str]): List of text lines to display on the right

        Returns:
            str: Formatted string with grid and text
        """
        if len(grid_data) != x * y:
            raise ValueError(f"Grid data length {len(grid_data)} doesn't match grid size {x}*{y}={x * y}")

        # Calculate grid width (including borders and spacing)
        grid_width = x * 2 + 3  # Each cell is 2 chars wide + borders

        # Calculate text area width
        text_width = 50  # Fixed width for text area

        # Total line width
        total_width = grid_width + 4 + text_width  # 4 chars spacing between grid and text

        # Create top border
        result = []

        # Create separator
        result.append("├" + "─" * (total_width - 2) + "┤")

        # Render grid and text side by side
        for row in range(y):
            row_start = row * x
            row_end = row_start + x

            # Get grid row content
            grid_row = grid_data[row_start:row_end]
            grid_content = "│ " + " ".join(grid_row) + " │"

            # Get text row content (if available)
            text_content = ""
            if row < len(text_data):
                text_content = text_data[row]
            else:
                text_content = " " * text_width

            # Pad text to fixed width
            text_content = text_content.ljust(text_width)

            # Combine grid and text
            line = f"│ {grid_content}    {text_content}"
            result.append(line)

        # Create bottom border
        result.append("╰" + "─" * (total_width - 2) + "╯")

        return "\n".join(result)

    @staticmethod
    def _log_agent_facts(agent: BaseAgent, context: "ApplicationContext") -> None:
        """
        Log agent facts information, each fact on a separate line.
        
        Args:
            agent (BaseAgent): The agent whose facts to log
            context (ApplicationContext): The current application context
        """
        facts = context.get_facts(agent.id()) + context.get_facts()
        if facts:
            amni_prompt_logger.info(f"│ 🧠 Context Facts: │")
            # Log all facts on separate lines
            for fact in facts:
                if hasattr(fact, 'content'):
                    fact_content = str(fact.content)
                    # Truncate if too long
                    # if len(fact_content) > BORDER_WIDTH - 7:
                    #     fact_content = fact_content[:BORDER_WIDTH-10] + "..."
                    amni_prompt_logger.info(f"│     │ {fact_content:<{BORDER_WIDTH-7}} │")
                else:
                    # If fact doesn't have content attribute, show the fact object
                    fact_str = str(fact)
                    if len(fact_str) > BORDER_WIDTH - 7:
                        fact_str = fact_str[:BORDER_WIDTH-10] + "..."
                    amni_prompt_logger.info(f"│     │ {fact_str:<{BORDER_WIDTH-7}} │")
        else:
            amni_prompt_logger.info(f"│ 🧠 Context Facts: {'No facts':<{BORDER_WIDTH-13}} │")

    @staticmethod
    def _log_agent_tools(agent: BaseAgent) -> None:
        """
        Log agent tools information, each tool on a separate line.
        
        Args:
            agent (BaseAgent): The agent whose tools to log
        """
        tools = agent.tools if hasattr(agent, 'tools') else []
        if tools:
            amni_prompt_logger.info(f"│ 🔨 Tools: │")
            # Log all tools on separate lines
            for tool in tools:
                if isinstance(tool, dict) and 'function' in tool:
                    function_info = tool['function']
                    if isinstance(function_info, dict) and 'name' in function_info:
                        tool_name = function_info['name']
                        # Determine tool type based on name
                        if tool_name.startswith('mcp'):
                            tool_type = "mcp"
                        elif '_agent' in tool_name:
                            tool_type = "agent_as_tool"
                        else:
                            tool_type = "tool"
                        
                        tool_display = f"{tool_name}({tool_type})"
                        # Truncate if too long
                        if len(tool_display) > BORDER_WIDTH - 7:
                            tool_display = tool_display[:BORDER_WIDTH-10] + "..."
                        amni_prompt_logger.info(f"{tool_display:<{BORDER_WIDTH-7}} │")
        else:
            amni_prompt_logger.info(f"│ 🔨 Tools: {'No tools':<{BORDER_WIDTH-13}} │")

    @staticmethod
    def _log_context_tree(context: "ApplicationContext") -> None:
        """
        Log context tree information with intelligent hierarchical structure formatting.
        
        Args:
            context (ApplicationContext): The current application context
        """
        amni_prompt_logger.info(f"│{'🌳 CONTEXT TREE':^{BORDER_WIDTH}}│")
        amni_prompt_logger.info(_generate_separator())

        # Format context tree output with intelligent hierarchical structure processing
        tree_lines = context.tree.split('\n')
        for line in tree_lines:
            if line.strip():  # Skip empty lines
                # Analyze line content to identify hierarchical structure
                original_line = line
                
                # Detect level identifiers and calculate indentation
                indent_level = 0
                if line.startswith('├─ '):
                    # Child node
                    line_content = line[3:]  # Remove "├─ " prefix
                    level_emoji = "├─"
                    indent_level = 1
                elif line.startswith('└─ '):
                    # Last child node
                    line_content = line[3:]  # Remove "└─ " prefix
                    level_emoji = "└─"
                    indent_level = 1
                elif line.startswith('📍 '):
                    # Current node, special identifier - lowest level, extra indentation
                    line_content = line[2:]  # Remove "📍 " prefix
                    level_emoji = "📍"
                    indent_level = 2  # Increase indentation level
                elif line.startswith('  '):
                    # Line with existing indentation, calculate indentation level
                    spaces_count = len(line) - len(line.lstrip())
                    indent_level = spaces_count // 2
                    # If this line contains "sub_" or similar identifiers, it's the lowest level, extra indentation
                    if 'sub_' in line or 'current' in line.lower():
                        indent_level += 1
                    line_content = line.lstrip()
                    level_emoji = ""
                elif line.startswith('Context Tree'):
                    # Tree title line, no indentation
                    line_content = line
                    level_emoji = ""
                    indent_level = 0
                else:
                    # Root node or other cases
                    line_content = line
                    level_emoji = ""
                    indent_level = 0
                
                # Generate indentation string
                indent_str = "  " * indent_level
                
                # Format display content
                if level_emoji:
                    # Line with level identifier
                    formatted_line = f"{indent_str}{level_emoji} {line_content}"
                else:
                    # Regular line
                    formatted_line = f"{indent_str}{line_content}"
                
                # Special handling: if line contains 🎯 emoji, ensure correct indentation
                if '🎯' in line_content and 'current' in line_content.lower():
                    # This is the current subtask, ensure correct indentation
                    if indent_level < 2:
                        indent_level = 2
                        indent_str = "  " * indent_level
                        formatted_line = f"{indent_str}├─ 🎯 {line_content.split('🎯', 1)[1].strip()}"
                
                # Ensure line length doesn't exceed border width, maintain border alignment
                # if len(formatted_line) > BORDER_WIDTH:
                #     formatted_line = formatted_line[:BORDER_WIDTH-3] + "..."
                #
                # Calculate right padding to maintain border alignment
                padding = BORDER_WIDTH - len(formatted_line)
                if padding > 0:
                    amni_prompt_logger.info(f"│ {formatted_line:<{BORDER_WIDTH}} │")
                else:
                    amni_prompt_logger.info(f"│ {formatted_line} │")

    @staticmethod
    def _log_messages(messages: list[dict]) -> None:
        """
        Log OpenAI messages with intelligent formatting and multi-modal content support.
        
        Args:
            messages (list[dict]): List of message dictionaries with 'role' and 'content' keys
        """
        amni_prompt_logger.info(_generate_separator())
        amni_prompt_logger.info(f"│{'📝 OPENAI MESSAGES':^{BORDER_WIDTH}}│")
        amni_prompt_logger.info(_generate_separator())

        # Format OpenAI Messages for clear display
        if messages:
            for i, message in enumerate(messages, 1):
                role = message.get('role', 'unknown')
                content = message.get('content', '')
                tool_calls = message.get('tool_calls')

                # Add different emojis and color identifiers for different roles
                role_emoji = {
                    'system': '🔧',
                    'user': '👤', 
                    'assistant': '🤖',
                    'function': '⚙️',
                    'tool': '🛠️'
                }.get(role, '❓')
                
                # Handle tool message tool_call_id
                if role == 'tool' and 'tool_call_id' in message:
                    tool_call_id = message.get('tool_call_id', 'unknown')
                    amni_prompt_logger.debug(f"🔗 Tool Call ID: {tool_call_id:<{BORDER_WIDTH-18}}")
                
                # Calculate message length
                message_length = 0
                if content:
                    if isinstance(content, list):
                        # Multi-modal content, calculate length of all text content
                        for item in content:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                message_length += num_tokens_from_string(str(item.get('text', '')))
                    else:
                        # Regular text content
                        message_length = num_tokens_from_string(str(content))
                if tool_calls:
                    message_length += num_tokens_from_string(str(tool_calls))


                # Format message content with length information
                amni_prompt_logger.info(f"│ 📨 Message #{i:<2} {role_emoji} {role.upper():<10} 📏 Length: {message_length:<6}")
                
                # Process message content, support multiple formats
                if content:
                    if isinstance(content, list):
                        # Process multi-modal content list (e.g., containing text and images)
                        try:
                            amni_prompt_logger.debug(f"📋 Multi-modal content ({len(content)} items):")
                            
                            for item_idx, item in enumerate(content, 1):
                                item_type = item.get('type', 'unknown')
                                
                                if item_type == 'text':
                                    # Process text content
                                    text_content = item.get('text', '')
                                    if text_content:
                                        amni_prompt_logger.debug(f"  📝 Text #{item_idx}:")
                                        # Split text content by newlines
                                        text_lines = text_content.split('\n')
                                        for line in text_lines:
                                            if line.strip():
                                                # Ensure each line is the configured width, maintain border alignment
                                                padded_line = line.ljust(BORDER_WIDTH - 8)
                                                # Use info level for user input text to make it visible in regular logs
                                                if role == 'user':
                                                    amni_prompt_logger.info(f"    {padded_line}")
                                                else:
                                                    amni_prompt_logger.debug(f"    {padded_line}")
                                            else:
                                                # Also display empty lines to maintain consistent format
                                                empty_line = " " * (BORDER_WIDTH - 8)
                                                if role == 'user':
                                                    amni_prompt_logger.info(f"    {empty_line}")
                                                else:
                                                    amni_prompt_logger.debug(f"    {empty_line}")
                                    else:
                                        empty_text = "<empty text>".ljust(BORDER_WIDTH - 8)
                                        if role == 'user':
                                            amni_prompt_logger.info(f"    {empty_text}")
                                        else:
                                            amni_prompt_logger.debug(f"    {empty_text}")
                                        
                                elif item_type == 'image_url':
                                    # Process image URL
                                    image_url = item.get('image_url', {}).get('url', '')
                                    if image_url.startswith('data:image'):
                                        image_info = "[Base64 image data]".ljust(BORDER_WIDTH - 8)
                                        amni_prompt_logger.debug(f"  🖼️  Image #{item_idx}: {image_info}")
                                    else:
                                        # Truncate long URLs
                                        if len(image_url) > BORDER_WIDTH - 20:
                                            image_url = image_url[:BORDER_WIDTH - 23] + "..."
                                        padded_url = image_url.ljust(BORDER_WIDTH - 8)
                                        amni_prompt_logger.debug(f"  🖼️  Image #{item_idx}: {padded_url}")
                                        
                                elif item_type == 'tool_use':
                                    # Process tool usage
                                    tool_name = item.get('tool_use', {}).get('name', 'unknown')
                                    padded_tool = tool_name.ljust(BORDER_WIDTH - 8)
                                    amni_prompt_logger.debug(f"  🛠️  Tool #{item_idx}: {padded_tool}")
                                    
                                else:
                                    # Process other types of content
                                    other_content = str(item)[:BORDER_WIDTH - 20]
                                    if len(str(item)) > BORDER_WIDTH - 20:
                                        other_content += "..."
                                    padded_other = other_content.ljust(BORDER_WIDTH - 8)
                                    amni_prompt_logger.debug(f"  ❓ {item_type.title()} #{item_idx}: {padded_other}")
                                
                                # Add separator lines between content items (except the last one)
                                if item_idx < len(content):
                                    amni_prompt_logger.debug("  " + "─" * (BORDER_WIDTH - 4) + "")
                                    
                        except Exception as e:
                            # If parsing fails, fall back to string display
                            amni_prompt_logger.debug(f"⚠️  Error parsing content list: {str(e)[:BORDER_WIDTH-30]}...")
                            fallback_content = str(content)[:BORDER_WIDTH - 8]
                            if len(str(content)) > BORDER_WIDTH - 8:
                                fallback_content += "..."
                            padded_fallback = fallback_content.ljust(BORDER_WIDTH - 8)
                            amni_prompt_logger.debug(f"📋 Fallback: {padded_fallback}")
                            
                    else:
                        # Process regular text content
                        content_str = str(content)
                        # Split content by newlines, display each line separately
                        content_lines = content_str.split('\n')
                        for line in content_lines:
                            if line.strip():  # Skip empty lines
                                # Ensure each line is the configured width, maintain border alignment
                                padded_line = line.ljust(BORDER_WIDTH)
                                # Use info level for user input text to make it visible in regular logs
                                if role in ['user'] or (role == 'system' and len(messages) <= 2) or i == len(messages):
                                    amni_prompt_logger.info(f"{padded_line}")
                                else:
                                    amni_prompt_logger.debug(f"{padded_line}")
                            else:
                                # Also display empty lines to maintain consistent format
                                empty_line = " " * BORDER_WIDTH
                                if role == 'user':
                                    amni_prompt_logger.info(f"{empty_line}")
                                else:
                                    amni_prompt_logger.debug(f"{empty_line}")
                else:
                    # Empty content, display placeholder
                    empty_placeholder = "<empty content>".ljust(BORDER_WIDTH)
                    amni_prompt_logger.debug(f"{empty_placeholder}")
                
                # Process tool calls (tool_calls)
                if 'tool_calls' in message and message['tool_calls']:
                    amni_prompt_logger.info(f"🛠️  Tool Calls: {len(message['tool_calls'])} found")
                    
                    for j, tool_call in enumerate(message['tool_calls'], 1):
                        if isinstance(tool_call, dict):
                            # Process dictionary format tool calls
                            function_name = tool_call.get('function', {}).get('name', 'unknown')
                            tool_id = tool_call.get('id', 'unknown')
                            args = tool_call.get('function', {}).get('arguments', '{}')
                            
                            amni_prompt_logger.info(f"  🔧 Tool #{j}: {function_name}")
                            amni_prompt_logger.info(f"  🆔 ID: {tool_id}")
                            
                            # # Format argument display, limit length and maintain border alignment
                            # if isinstance(args, str):
                            #     # 如果是字符串，确保Unicode转义序列被正确解码
                            #     try:
                            #         # 处理可能的Unicode转义序列
                            #         args_str = args.encode('utf-8').decode('unicode_escape')
                            #     except (UnicodeDecodeError, UnicodeError):
                            #         # 如果已经是正确编码的字符串，直接使用
                            #         args_str = str(args)
                            # else:
                            #     # 对于非字符串类型，先转换为字符串再处理Unicode
                            #     args_str = str(args)
                            #     try:
                            #         # 尝试解码可能存在的Unicode转义序列
                            #         args_str = args_str.encode('utf-8').decode('unicode_escape')
                            #     except (UnicodeDecodeError, UnicodeError):
                            #         # 如果已经是正确编码的字符串，保持不变
                            #         pass
                            args_str = str(args)
                            padded_args = args_str.ljust(BORDER_WIDTH - 8)
                            amni_prompt_logger.info(f"  📋 Args: {padded_args}")
                            
                        elif hasattr(tool_call, 'function') and hasattr(tool_call, 'id'):
                            # Process object format tool calls (e.g., ToolCall class)
                            function_name = getattr(tool_call.function, 'name', 'unknown')
                            tool_id = getattr(tool_call, 'id', 'unknown')
                            args = getattr(tool_call.function, 'arguments', '{}')
                            
                            amni_prompt_logger.info(f"  🔧 Tool #{j}: {function_name}")
                            amni_prompt_logger.info(f"  🆔 ID: {tool_id}")
                            
                            # # Format argument display, limit length and maintain border alignment
                            # if isinstance(args, str):
                            #     # 如果是字符串，确保Unicode转义序列被正确解码
                            #     try:
                            #         # 处理可能的Unicode转义序列
                            #         args_str = args.encode('utf-8').decode('unicode_escape')
                            #     except (UnicodeDecodeError, UnicodeError):
                            #         # 如果已经是正确编码的字符串，直接使用
                            #         args_str = str(args)
                            # else:
                            #     # 对于非字符串类型，先转换为字符串再处理Unicode
                            #     args_str = str(args)
                            #     try:
                            #         # 尝试解码可能存在的Unicode转义序列
                            #         args_str = args_str.encode('utf-8').decode('unicode_escape')
                            #     except (UnicodeDecodeError, UnicodeError):
                            #         # 如果已经是正确编码的字符串，保持不变
                            #         pass
                            args_str = str(args)
                            padded_args = args_str.ljust(BORDER_WIDTH - 8)
                            amni_prompt_logger.info(f"  📋 Args: {padded_args}")
                        
                        # Add separator lines between tool calls (except the last one)
                        if j < len(message['tool_calls']):
                            amni_prompt_logger.debug("  " + "─" * (BORDER_WIDTH - 4) + "")
                
                # Add message separator lines (except the last message)
                if i < len(messages):
                    amni_prompt_logger.debug("" + "─" * BORDER_WIDTH + "")
        else:
            amni_prompt_logger.debug("No messages" + " " * (BORDER_WIDTH - 12) + "")

    @staticmethod
    def log_formatted_parameters(variables: Dict[str, Any] = None) -> None:
        if not variables:
            return

        def truncate_value(value, max_length=20):
            """截断超过指定长度的字符串值"""
            if isinstance(value, str) and len(value) > max_length:
                return value[:max_length] + "..."
            return value

        # 格式化variables字典，分离预定义变量和用户变量
        formatted_vars = {}
        predefined_vars = []

        for key, value in variables.items():
            # 检查是否是预定义变量
            if key in ALL_PREDEFINED_DYNAMIC_VARIABLES:
                predefined_vars.append(f"{key}:{value}")
            else:
                formatted_vars[key] = value

        # 构建结构化日志格式
        log_lines = [
            "╭────────────────────────────────────────────────────────────────────────────────────────────────────╮",
            "│                                      🎯 PROMPT TEMPLATE PARAMETERS                                 │",
            "├────────────────────────────────────────────────────────────────────────────────────────────────────┤"
        ]

        # 添加预定义变量信息（压缩在一行）
        if predefined_vars:
            predefined_line = "│ 🔧 Predefined Variables: " + " | ".join(predefined_vars)
            log_lines.append(predefined_line)

        # 添加用户变量信息
        if formatted_vars:
            log_lines.append("│ 📋 User Variables:")
            for key, value in formatted_vars.items():
                # 格式化key-value对，确保对齐
                value_str = str(value)
                key_str = f" 🏷️{key}({num_tokens_from_string(value_str)} tokens) -> :"
                # 如果value太长，需要换行处理
                log_lines.append(f"│ {key_str} {truncate_value(value_str)}")
        else:
            log_lines.append("│ 📋 User Variables: (empty)")

        log_lines.append("├────────────────────────────────────────────────────────────────────────────────────────────────────┤")
        log_lines.append("╰────────────────────────────────────────────────────────────────────────────────────────────────────╯")

        # 输出每一行日志
        for line in log_lines:
            amni_prompt_logger.info(line)

    @staticmethod
    def _generate_context_usage_grid(system_tokens: int, user_tokens: int, 
                                    assistant_tokens: int, tool_tokens: int, 
                                    other_tokens: int, context_window_limit: int = None) -> list[str]:
        """
        Generate a simple 10x10 grid visualization for context usage.
        
        Args:
            system_tokens (int): Number of system message tokens
            user_tokens (int): Number of user message tokens
            assistant_tokens (int): Number of assistant message tokens
            tool_tokens (int): Number of tool message tokens
            other_tokens (int): Number of other message tokens
            context_window_limit (int): The context window limit in tokens. If None, defaults to 64k.
            
        Returns:
            list[str]: List of 100 grid elements representing the context usage visualization
        """
        total_cells = 100
        if context_window_limit is None:
            context_window_limit = 64 * 1024  # Default to 64k tokens
        
        # Color blocks for different categories (full and partial)
        colors = {
            'system': ('🟦', '🔷'),    # Blue square/blue diamond
            'user': ('🟧', '🔶'),      # Orange square/orange diamond
            'assistant': ('🟨', '🔸'), # Yellow square/yellow diamond
            'tool': ('🟪', '🔮'),      # Purple square/purple diamond
            'other': ('🟥', '🔺')      # Red square/red triangle
        }
        
        # Calculate cells for each category based on percentage
        categories = [
            ('system', system_tokens),
            ('user', user_tokens), 
            ('assistant', assistant_tokens),
            ('tool', tool_tokens),
            ('other', other_tokens)
        ]
        
        # Filter categories with tokens and calculate cells
        active_categories = []
        for name, count in categories:
            if count > 0:
                percentage = (count / context_window_limit) * 100
                full_cells = int(percentage)  # Integer part
                partial = percentage - full_cells  # Decimal part
                
                # Show categories with any meaningful representation
                # Always show partial cells if they exist, even for small percentages
                if full_cells > 0 or partial > 0:
                    active_categories.append((name, full_cells, partial))
        
        # Create grid
        grid = []
        current_pos = 0
        
        # Fill active categories
        for name, full_cells, partial in active_categories:
            # Fill full cells
            for i in range(full_cells):
                if current_pos < total_cells:
                    grid.append(colors[name][0])  # Full square
                    current_pos += 1
            
            # Add partial cell if exists
            if partial > 0 and current_pos < total_cells:
                grid.append(colors[name][1])  # Partial symbol
                current_pos += 1
        
        # Fill remaining with empty squares
        while current_pos < total_cells:
            grid.append('⬜')
            current_pos += 1
        
        return grid


"""
上下文窗口定义总结
================

根据 Andrej Karpathy 的观点，大语言模型（LLMs）就像一种新型的操作系统：
- LLM 就像 CPU，而上下文窗口就像 RAM，作为模型的工作内存
- 就像 RAM 一样，LLM 上下文窗口的容量有限，需要处理各种上下文来源
- 正如操作系统管理 CPU 的 RAM 使用，我们可以将"上下文工程"视为类似的作用

Karpathy 对上下文工程的定义：
"上下文工程是...用恰好合适的信息填充上下文窗口以进行下一步的精细艺术和科学。"

这个定义强调了在有限的上下文窗口中优化信息选择和管理的重要性，这是构建高效 AI 系统的关键要素。
"""