"""
CLI Human Handler for aworld_cli
Simple handler for human input in CLI
"""

import asyncio
import json
import re
from typing import Optional, Tuple

from aworld.core.event.base import Constants, Message
from aworld.logs.util import logger
from aworld.runners import HandlerFactory
from aworld.runners.handler.human import DefaultHumanHandler
from rich.prompt import Prompt
from rich.panel import Panel

from .._globals import console
from ..user_input import UserInputHandler


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
    
    def _parse_human_in_loop_content(self, payload: str) -> Tuple[str, dict]:
        """
        解析 human_in_loop 格式的内容，提取 input_type 和配置数据，并路由到对应的 user_input 接口。
        
        支持的格式（按优先级排序）：
        1. JSON 格式（推荐）：{"input_type": "1", "message": "..."} 等
        2. human_in_loop 代码块格式：```human_in_loop ... ```
        3. 前缀格式（向后兼容）：1|, 2|, 3|, 4|, 5|, 6| 开头
        
        路由规则：
        - input_type "1" -> user_input.submit() (确认/批准)
        - input_type "2" -> user_input.text_input() (文本输入)
        - input_type "3" -> 文件上传（暂未实现，回退到文本输入）
        - input_type "4" -> user_input.select_multiple() (多选)
        - input_type "5" -> user_input.single_select() (单选)
        - input_type "6" -> user_input.composite_menu() (复合菜单)
        
        Args:
            payload: 原始 payload 内容
            
        Returns:
            (input_type, config_dict) 元组，config_dict 包含所有配置数据，可直接传递给对应的 user_input 接口
        """
        if not payload or not payload.strip():
            return "2", {"input_type": "2", "text": ""}
        
        payload = payload.strip()
        
        # 优先级1: 尝试解析为 JSON 格式（推荐格式）
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and "input_type" in data:
                input_type = str(data.get("input_type", "2"))
                # 确保 input_type 在 config 中
                data["input_type"] = input_type
                logger.debug(f"✅ 解析 JSON 格式成功: input_type={input_type}")
                return input_type, data
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            logger.debug(f"JSON 解析失败，尝试其他格式: {e}")
        
        # 优先级2: 检查是否是 human_in_loop 代码块格式
        if "```human_in_loop" in payload:
            try:
                match = re.search(r'```human_in_loop\s*\n(.*?)\n```', payload, re.DOTALL)
                if match:
                    json_str = match.group(1).strip()
                    data = json.loads(json_str)
                    if isinstance(data, dict) and "input_type" in data:
                        input_type = str(data.get("input_type", "2"))
                        data["input_type"] = input_type
                        logger.debug(f"✅ 解析 human_in_loop 代码块成功: input_type={input_type}")
                        return input_type, data
            except (json.JSONDecodeError, ValueError, AttributeError, KeyError) as e:
                logger.debug(f"human_in_loop 代码块解析失败: {e}")
        
        # 优先级3: 回退到前缀格式（向后兼容）
        if payload.startswith("1|"):
            input_type = "1"
            config = {"input_type": "1", "message": payload[2:].strip(), "default": True}
        elif payload.startswith("2|"):
            input_type = "2"
            config = {"input_type": "2", "text": payload[2:].strip()}
        elif payload.startswith("3|"):
            input_type = "3"
            config = {"input_type": "3", "message": payload[2:].strip()}
        elif payload.startswith("4|"):
            input_type = "4"
            content = payload[2:].strip()
            # 尝试解析为 JSON 数组
            try:
                options = json.loads(content)
                if not isinstance(options, list):
                    options = [options] if options else []
                config = {"input_type": "4", "options": options, "title": "请选择（可多选）"}
            except (json.JSONDecodeError, ValueError, AttributeError):
                # 如果不是 JSON，按行分割
                options = [line.strip() for line in content.split('\n') if line.strip()]
                config = {"input_type": "4", "options": options, "title": "请选择（可多选）"}
        elif payload.startswith("5|"):
            input_type = "5"
            content = payload[2:].strip()
            # 尝试解析为 JSON 对象
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    data["input_type"] = "5"
                    config = data
                else:
                    config = {"input_type": "5", "title": str(data), "options": []}
            except (json.JSONDecodeError, ValueError, AttributeError):
                # 如果不是 JSON，按行分割（第一行是标题，后续是选项）
                lines = [line.strip() for line in content.split('\n') if line.strip()]
                if lines:
                    config = {"input_type": "5", "title": lines[0], "options": lines[1:] if len(lines) > 1 else []}
                else:
                    config = {"input_type": "5", "title": "请选择", "options": []}
        elif payload.startswith("6|"):
            input_type = "6"
            content = payload[2:].strip()
            # 尝试解析为 JSON 对象
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    data["input_type"] = "6"
                    config = data
                else:
                    config = {"input_type": "6", "title": "复合菜单", "tabs": []}
            except (json.JSONDecodeError, ValueError, AttributeError):
                config = {"input_type": "6", "title": "复合菜单", "tabs": []}
        else:
            # 默认文本输入
            input_type = "2"
            config = {"input_type": "2", "text": payload}
        
        logger.debug(f"✅ 使用前缀格式解析: input_type={input_type}")
        return input_type, config
    
    async def handle_user_input(self, message: Message) -> Optional[str]:
        """
        Handle user input - display formatted payload and prompt for input
        
        Args:
            message: Message object containing the input request
            
        Returns:
            User's input as string, or None if input failed/cancelled
        """
        try:
            logger.info(f"✅ Handling user input: {message.payload}")
            message.context.cli = self.console
            
            # Add a blank line for visual separation
            self.console.print()
            
            payload = message.payload or ""
            
            # 解析 input_type 和 config
            input_type, config = self._parse_human_in_loop_content(payload)
            
            # 创建 UserInputHandler 实例
            user_input_handler = UserInputHandler(self.console)
            
            # 根据 input_type 处理不同的输入类型
            if input_type == "1":  # approval/confirmation
                from rich.prompt import Confirm
                message_text = config.get("message", "请确认")
                default = config.get("default", True)
                confirmed = await asyncio.to_thread(
                    Confirm.ask,
                    f"[cyan]{message_text}[/cyan]",
                    default=default,
                    console=self.console
                )
                return json.dumps({"confirmed": confirmed}, ensure_ascii=False)
            
            elif input_type == "2":  # text_input
                try:
                    prompt = config.get("text", config.get("prompt", "请输入"))
                    default = config.get("default", "")
                    placeholder = config.get("placeholder")
                    
                    user_input = await asyncio.to_thread(
                        user_input_handler.text_input,
                        prompt=prompt,
                        default=default,
                        placeholder=placeholder
                    )
                    if user_input:
                        logger.info(f"✅ Human text input received: {user_input[:100]}...")
                    return user_input
                except Exception as e:
                    logger.warning(f"调用文本输入接口失败，回退到简单输入: {e}")
                    self.console.print(f"[yellow]⚠️ 文本输入接口调用失败: {e}[/yellow]")
                    # 回退到简单输入
                    display_text = config.get("text", config.get("prompt", "请输入"))
                    if display_text:
                        formatted_payload = self._format_payload(display_text)
                        self.console.print(
                            Panel(
                                formatted_payload,
                                border_style="dim",
                                padding=(1, 2),
                                title=None
                            )
                        )
                        self.console.print()
                    user_input = await asyncio.to_thread(
                        Prompt.ask,
                        f"[cyan]{self._get_short_prompt(display_text)}[/cyan]",
                        console=self.console
                    )
                    return user_input.strip() if user_input else None
            
            elif input_type == "3":  # file_upload
                message_text = config.get("message", "请上传文件")
                self.console.print(f"[yellow]⚠️ 文件上传功能暂未实现，请手动提供文件路径。[/yellow]")
                self.console.print(f"[dim]{message_text}[/dim]")
                file_path = await asyncio.to_thread(
                    Prompt.ask,
                    "[cyan]请输入文件路径[/cyan]",
                    console=self.console
                )
                return json.dumps({"file_path": file_path.strip()}, ensure_ascii=False) if file_path.strip() else None
            
            elif input_type == "4":  # multi_select
                try:
                    options = config.get("options", [])
                    if not options:
                        self.console.print("[yellow]⚠️ 没有可选项，回退到文本输入。[/yellow]")
                    else:
                        title = config.get("title", "请选择（可多选）")
                        prompt = config.get("prompt", "输入选项编号（用逗号分隔，如：1,3,5）")
                        selected_indices = await asyncio.to_thread(
                            user_input_handler.select_multiple,
                            options=options,
                            title=title,
                            prompt=prompt
                        )
                        if selected_indices:
                            selected_options = [options[i] for i in selected_indices]
                            user_input = json.dumps(selected_options, ensure_ascii=False)
                            logger.info(f"✅ Human multi-select received: {len(selected_indices)} items")
                            return user_input
                        return None
                except Exception as e:
                    logger.warning(f"调用多选接口失败，回退到文本输入: {e}")
                    self.console.print(f"[yellow]⚠️ 多选接口调用失败: {e}[/yellow]")
            
            elif input_type == "5":  # single_select
                try:
                    options = config.get("options", [])
                    title = config.get("title", "请选择")
                    warning = config.get("warning")
                    question = config.get("question")
                    nav_items = config.get("nav_items")
                    
                    if not options:
                        self.console.print("[yellow]⚠️ 没有可选项，回退到文本输入。[/yellow]")
                    else:
                        selected_index = await asyncio.to_thread(
                            user_input_handler.single_select,
                            options=options,
                            title=title,
                            warning=warning,
                            question=question,
                            nav_items=nav_items
                        )
                        if selected_index is not None:
                            selected_option = options[selected_index] if isinstance(options[selected_index], str) else options[selected_index].get("label", "")
                            user_input = json.dumps({"selected_index": selected_index, "selected_option": selected_option}, ensure_ascii=False)
                            logger.info(f"✅ Human single-select received: index {selected_index}")
                            return user_input
                        return None
                except Exception as e:
                    logger.warning(f"调用单选接口失败，回退到文本输入: {e}")
                    self.console.print(f"[yellow]⚠️ 单选接口调用失败: {e}[/yellow]")
            
            elif input_type == "6":  # composite_menu
                try:
                    tabs = config.get("tabs", [])
                    title = config.get("title", "复合菜单")
                    
                    if not tabs:
                        self.console.print("[yellow]⚠️ 没有配置任何 tab，回退到文本输入。[/yellow]")
                    else:
                        results = await asyncio.to_thread(
                            user_input_handler.composite_menu,
                            tabs=tabs,
                            title=title
                        )
                        if results:
                            user_input = json.dumps(results, ensure_ascii=False)
                            logger.info(f"✅ Human composite menu received: {len(results)} tabs")
                            return user_input
                        return None
                except Exception as e:
                    logger.warning(f"调用复合菜单接口失败，回退到文本输入: {e}")
                    self.console.print(f"[yellow]⚠️ 复合菜单接口调用失败: {e}[/yellow]")
            
            # 处理默认情况（未匹配到任何 input_type 或回退情况）
            # Display formatted payload content if available
            display_text = config.get("message") if config.get("message") else payload
            if display_text:
                formatted_payload = self._format_payload(display_text)
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
            prompt_text = self._get_short_prompt(display_text if display_text else payload)
            
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
