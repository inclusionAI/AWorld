"""
User Input Handler for aworld_cli
Provides various user input methods: multi-select, text input, and submit/confirm
"""

import sys
from typing import List, Optional, Set, Dict, Any, Union

from rich import box
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import FormattedText, to_formatted_text
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False
    to_formatted_text = None
    Style = None

from ._globals import console


class UserInputHandler:
    """
    Handler for various user input types in CLI.
    
    Provides methods for:
    - Multi-select: Select multiple options from a list
    - Text input: Get text input from user
    - Submit: Get confirmation/submit from user
    """
    
    def __init__(self, console_instance=None):
        """
        Initialize the UserInputHandler.
        
        Args:
            console_instance: Optional console instance. If None, uses global console.
        """
        self.console = console_instance or console
    
    def select_multiple(self, options: List, title: str = "请选择（可多选）", prompt: str = "输入选项编号（用逗号分隔，如：1,3,5）") -> List[int]:
        """
        展示多选框列表并支持勾选，返回选中的选项索引列表。
        支持键盘上下箭头导航和回车键勾选/取消勾选。
        现代化的界面设计，支持选项描述。
        
        Args:
            options: 选项列表，支持以下格式：
                - List[str]: 简单字符串列表，如 ["选项1", "选项2"]
                - List[dict]: 字典列表，每个字典包含 'label' 和 'description' 键
                  如 [{"label": "选项1", "description": "这是选项1的描述"}, ...]
                - List[tuple]: 元组列表，每个元组包含 (label, description)
                  如 [("选项1", "这是选项1的描述"), ...]
            title: 标题
            prompt: 提示文本（当 prompt_toolkit 不可用时使用）
            
        Returns:
            选中的选项索引列表（从0开始）
        """
        if not options:
            self.console.print("[red]没有可选项。[/red]")
            return []
        
        # 检查是否在真实终端且 prompt_toolkit 可用
        is_terminal = sys.stdin.isatty()
        
        # 如果 prompt_toolkit 可用且在终端中，使用交互式界面
        if PROMPT_TOOLKIT_AVAILABLE and is_terminal:
            return self._select_multiple_interactive(options, title)
        else:
            # 回退到原来的文本输入方式
            return self._select_multiple_text_input(options, title, prompt)
    
    def _render_multi_select_options(self, parsed_options: List[tuple], selected_indices: Set[int], 
                                     current_index: int, fragments: List[tuple]) -> None:
        """
        渲染多选列表的选项部分（共享代码）。
        
        Args:
            parsed_options: 解析后的选项列表，每个元素为 (label, description) 元组
            selected_indices: 选中的索引集合
            current_index: 当前高亮的索引
            fragments: 要追加到的 fragments 列表
        """
        for idx, (label, description) in enumerate(parsed_options):
            # 判断是否选中
            is_selected = idx in selected_indices
            # 判断是否是当前高亮项
            is_current = idx == current_index
            
            # 构建每行的格式
            # 序号
            number = f"{idx + 1}.  "
            
            # 复选框和箭头
            if is_current:
                prefix = "> "
                checkbox = "[✓]" if is_selected else "[ ]"
            else:
                prefix = "  "
                checkbox = "[✓]" if is_selected else "[ ]"
            
            # 设置样式
            if is_current and is_selected:
                item_style = "class:current-selected"
                label_style = "class:current-selected-label"
                desc_style = "class:normal-desc"  # 描述行始终使用普通样式
            elif is_current:
                item_style = "class:current"
                label_style = "class:current-label"
                desc_style = "class:normal-desc"  # 描述行始终使用普通样式
            elif is_selected:
                item_style = "class:selected"
                label_style = "class:selected-label"
                desc_style = "class:selected-desc"
            else:
                item_style = "class:normal"
                label_style = "class:normal-label"
                desc_style = "class:normal-desc"
            
            # 构建选项行
            fragments.append((item_style, prefix))
            fragments.append(("class:number", number))
            fragments.append(("class:checkbox", checkbox))
            fragments.append((label_style, f" {label}"))
            
            # 如果有描述，添加描述
            if description:
                fragments.append(("", "\n"))
                fragments.append((item_style, "     "))  # 缩进
                fragments.append((desc_style, f"    {description}"))
            
            fragments.append(("", "\n"))
    
    def _select_multiple_interactive(self, options: List, title: str) -> List[int]:
        """
        使用 prompt_toolkit 实现的交互式多选框。
        支持上下箭头导航，回车键勾选/取消勾选。
        现代化的界面设计，类似图片中的样式。
        """
        # 解析选项：支持字符串或字典格式
        def parse_option(opt):
            """解析选项，支持字符串或字典格式"""
            if isinstance(opt, dict):
                return opt.get('label', ''), opt.get('description', '')
            elif isinstance(opt, (list, tuple)) and len(opt) >= 2:
                return opt[0], opt[1]
            else:
                return str(opt), ""
        
        parsed_options = [parse_option(opt) for opt in options]
        
        # 使用列表来存储状态，以便在闭包中修改
        state = {
            'selected_indices': set(),
            'current_index': 0
        }
        
        def get_formatted_text():
            """生成格式化的文本内容"""
            fragments = []
            
            # 标题 - 更现代化的样式
            fragments.append(("class:title", f"● {title}\n"))
            fragments.append(("", "\n"))
            
            # 选项列表 - 使用共享的渲染函数
            self._render_multi_select_options(
                parsed_options, 
                state['selected_indices'], 
                state['current_index'], 
                fragments
            )
            
            fragments.append(("", "\n"))
            
            # 底部提示 - 更清晰的格式
            selected_count = len(state['selected_indices'])
            if selected_count > 0:
                fragments.append(("class:footer", f"已选择 {selected_count} 项 · "))
            fragments.append(("class:footer", "Enter 选择 · Tab/方向键 导航 · Esc 取消"))
            
            # 确保返回 FormattedText 对象
            try:
                if to_formatted_text:
                    return to_formatted_text(fragments)
                else:
                    return FormattedText(fragments)
            except Exception:
                # 如果 FormattedText 构造失败，尝试直接返回字符串
                text_lines = []
                for style, text in fragments:
                    text_lines.append(text)
                return "".join(text_lines)
        
        # 创建键盘绑定
        kb = KeyBindings()
        
        def move_up(event):
            if state['current_index'] > 0:
                state['current_index'] -= 1
                # 触发界面更新
                event.app.invalidate()
        
        def move_down(event):
            if state['current_index'] < len(options) - 1:
                state['current_index'] += 1
                # 触发界面更新
                event.app.invalidate()
        
        def toggle_selection(event):
            """切换选择状态"""
            if state['current_index'] in state['selected_indices']:
                state['selected_indices'].remove(state['current_index'])
            else:
                state['selected_indices'].add(state['current_index'])
            # 触发界面更新
            event.app.invalidate()
        
        def confirm_selection(event):
            event.app.exit()
        
        def cancel_selection(event):
            state['selected_indices'].clear()
            event.app.exit()
        
        # 绑定按键
        kb.add("up")(move_up)
        kb.add("k")(move_up)  # vim 风格
        kb.add("down")(move_down)
        kb.add("j")(move_down)  # vim 风格
        kb.add("left")(move_up)  # 左箭头也支持
        kb.add("right")(move_down)  # 右箭头也支持
        kb.add(" ")(toggle_selection)  # 空格键切换选择
        kb.add("enter")(toggle_selection)  # 回车键切换选择
        kb.add("c-m")(toggle_selection)  # Ctrl+M 也是回车键
        kb.add("tab")(confirm_selection)  # Tab 键完成选择
        kb.add("c-c")(cancel_selection)  # Ctrl+C 取消
        kb.add("escape")(cancel_selection)  # ESC 取消
        
        # 创建控件 - 使用可调用对象来动态更新
        # 注意：text 参数应该是一个返回 FormattedText 或字符串的可调用对象
        # 包装函数以确保返回正确的类型
        def get_text():
            result = get_formatted_text()
            # 确保返回的是 FormattedText 对象或字符串，而不是列表
            if isinstance(result, FormattedText):
                return result
            elif isinstance(result, str):
                return result
            elif isinstance(result, list):
                # 如果返回的是列表，转换为 FormattedText
                return FormattedText(result)
            else:
                # 其他情况，尝试转换为字符串
                return str(result)
        
        control = FormattedTextControl(
            text=get_text,
            focusable=True
        )
        
        # 定义样式 - 使用列表格式，现代化的配色方案
        # 类似图片中的紫色高亮和清晰的视觉层次
        # prompt_toolkit 的 Style 接受 (class_name, style_string) 元组列表
        # 注意：Style 构造函数期望的类名不带 "class:" 前缀
        style_list = [
            ("title", "bold #ffffff"),  # 白色粗体标题
            ("number", "#888888"),  # 灰色序号
            ("checkbox", "#9d4edd"),  # 紫色复选框（类似图片中的紫色主题）
            ("prefix", "#9d4edd"),  # 紫色箭头
            # 当前选中项 - 紫色背景高亮（类似图片）
            ("current", "bg:#9d4edd #ffffff"),  # 紫色背景，白色文字
            ("current-label", "bg:#9d4edd bold #ffffff"),  # 粗体标签
            ("current-desc", "bg:#9d4edd #e0e0e0"),  # 浅灰色描述
            # 当前选中且已勾选
            ("current-selected", "bg:#7b2cbf #ffffff"),  # 深紫色背景
            ("current-selected-label", "bg:#7b2cbf bold #ffffff"),
            ("current-selected-desc", "bg:#7b2cbf #e0e0e0"),
            # 已勾选但非当前项
            ("selected", "#9d4edd"),  # 紫色文字
            ("selected-label", "#9d4edd"),
            ("selected-desc", "#888888"),
            # 普通项
            ("normal", "#ffffff"),  # 白色文字
            ("normal-label", "#ffffff"),
            ("normal-desc", "#888888"),  # 灰色描述
            # 底部提示
            ("footer", "#888888"),  # 灰色提示文字
        ]
        
        # 创建 Style 对象 - 使用列表格式
        # prompt_toolkit 会自动将 Style 中的类名与 FormattedText 中的 "class:xxx" 格式匹配
        if Style:
            try:
                style = Style(style_list)
            except Exception:
                # 如果 Style 构造失败，尝试使用 from_dict
                style_dict = dict(style_list)
                try:
                    style = Style.from_dict(style_dict)
                except Exception:
                    style = None
        else:
            style = None
        
        # 创建布局
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # 创建应用
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            refresh_interval=0.1  # 定期刷新以更新显示
        )
        
        try:
            app.run()
        except KeyboardInterrupt:
            return []
        
        # 返回选中的索引列表（排序）
        return sorted(list(state['selected_indices']))
    
    def _select_multiple_text_input(self, options: List[str], title: str, prompt: str) -> List[int]:
        """
        回退的文本输入方式（当 prompt_toolkit 不可用或不在终端时）。
        """
        # 创建表格展示选项
        table = Table(title=title, box=box.ROUNDED, width=80)
        table.add_column("编号", style="cyan", justify="right", width=8)
        table.add_column("选项", style="magenta")
        
        for idx, option in enumerate(options, 1):
            table.add_row(str(idx), option)
        
        self.console.print(table)
        self.console.print("[dim]输入 'exit' 或 'cancel' 取消选择。[/dim]")
        
        # 检查是否在真实终端
        is_terminal = sys.stdin.isatty()
        
        while True:
            if is_terminal:
                choice = Prompt.ask(f"[cyan]{prompt}[/cyan]", default="", console=self.console)
            else:
                self.console.print(f"{prompt}: ", end="")
                choice = input().strip()
            
            # 检查取消命令
            if choice.lower() in ("exit", "quit", "q", "cancel"):
                self.console.print("[yellow]选择已取消。[/yellow]")
                return []
            
            if not choice:
                self.console.print("[red]请输入选项编号。[/red]")
                continue
            
            try:
                # 解析输入的编号（支持逗号分隔）
                selected_indices = []
                for part in choice.split(','):
                    part = part.strip()
                    if not part:
                        continue
                    idx = int(part) - 1  # 转换为0-based索引
                    if 0 <= idx < len(options):
                        selected_indices.append(idx)
                    else:
                        self.console.print(f"[red]无效的选项编号: {part}。请重新输入。[/red]")
                        selected_indices = None
                        break
                
                if selected_indices is not None:
                    if selected_indices:
                        # 显示选中的选项
                        selected_options = [options[i] for i in selected_indices]
                        self.console.print(f"[green]已选择 {len(selected_indices)} 项：[/green]")
                        for idx in selected_indices:
                            self.console.print(f"  [green]✓[/green] {options[idx]}")
                        return selected_indices
                    else:
                        self.console.print("[red]请至少选择一个选项。[/red]")
            except ValueError:
                self.console.print("[red]请输入有效的数字编号（用逗号分隔）。[/red]")
    
    def text_input(self, prompt: str = "请输入", default: str = "", placeholder: Optional[str] = None) -> Optional[str]:
        """
        获取用户文本输入。
        
        Args:
            prompt: 提示文本
            default: 默认值
            placeholder: 占位符文本（用于显示提示）
            
        Returns:
            用户输入的文本，如果取消则返回 None
        """
        # 检查是否在真实终端且 prompt_toolkit 可用
        is_terminal = sys.stdin.isatty()
        
        if not is_terminal or not PROMPT_TOOLKIT_AVAILABLE:
            # 非终端环境或 prompt_toolkit 不可用，使用简单输入，并用蓝色 Panel 包裹
            try:
                # 构建提示内容
                panel_content = prompt
                if placeholder:
                    panel_content = f"{prompt}\n[dim]{placeholder}[/dim]"
                
                # 显示蓝色边框的 Panel
                input_panel = Panel(
                    panel_content,
                    title="[bold cyan]📝 Text Input[/bold cyan]",
                    title_align="left",
                    border_style="cyan",
                    padding=(1, 2)
                )
                self.console.print(input_panel)
                self.console.print()
                
                # 获取用户输入
                user_input = input().strip() or default
                return user_input.strip() if user_input else None
            except KeyboardInterrupt:
                self.console.print("\n[yellow]输入已取消。[/yellow]")
                return None
        
        # 使用交互式输入框界面，先显示蓝色 Panel 提示
        # 构建提示内容
        panel_content = prompt
        if placeholder:
            panel_content = f"{prompt}\n[dim]{placeholder}[/dim]"
        
        # 显示蓝色边框的 Panel
        input_panel = Panel(
            panel_content,
            title="[bold cyan]📝 Text Input[/bold cyan]",
            title_align="left",
            border_style="cyan",
            padding=(1, 2)
        )
        self.console.print(input_panel)
        self.console.print()
        
        # 调用交互式输入
        return self._text_input_interactive(prompt, default, placeholder)
    
    def _text_input_interactive(self, prompt: str, default: str, placeholder: Optional[str]) -> Optional[str]:
        """使用交互式输入框获取文本输入"""
        # 状态管理
        state = {
            'value': default,
            'editing': True,
            'result': None
        }
        
        # 计算文本显示宽度（中文字符占2个宽度）
        def get_display_width(text):
            """计算文本在终端中的显示宽度"""
            width = 0
            for char in text:
                if ord(char) > 127:
                    width += 2
                else:
                    width += 1
            return width
        
        # 生成格式化文本
        def get_formatted_text():
            fragments = []
            
            # 显示提示
            fragments.append(("class:input-title", f"{prompt}\n"))
            fragments.append(("", "\n"))
            
            # 搜索框样式 - 圆角边框，浅紫色
            box_width = 60
            
            # 上边框 - 圆角
            fragments.append(("class:search-box", "╭"))
            fragments.append(("class:search-box", "─" * (box_width - 2)))
            fragments.append(("class:search-box", "╮\n"))
            
            # 中间行 - 包含图标、输入内容和光标
            fragments.append(("class:search-box", "│"))
            
            # 放大镜图标
            icon_text = ""
            icon_display_width = 4  # 1空格 + 2(emoji) + 1空格
            fragments.append(("class:search-icon", icon_text))
            
            # 输入内容或占位符
            display_text = state['value'] if state['value'] else (placeholder or '')
            text_display_width = get_display_width(display_text)
            
            if state['value']:
                fragments.append(("class:input-text", display_text))
            else:
                fragments.append(("class:input-placeholder", display_text))
            
            # 光标（如果正在编辑）
            cursor_width = 0
            if state['editing']:
                cursor_text = "▊"
                cursor_width = 1
                fragments.append(("class:input-cursor", cursor_text))
            
            # 填充剩余空间
            used_width = 1 + icon_display_width + text_display_width + cursor_width
            remaining = box_width - used_width - 1  # 减去右边框
            if remaining > 0:
                fragments.append(("class:search-box", " " * remaining))
            
            fragments.append(("class:search-box", "│\n"))
            
            # 下边框 - 圆角
            fragments.append(("class:search-box", "╰"))
            fragments.append(("class:search-box", "─" * (box_width - 2)))
            fragments.append(("class:search-box", "╯\n"))
            
            fragments.append(("", "\n"))
            fragments.append(("class:footer", "输入文本后按 Enter 确认 · Esc 取消"))
            
            try:
                if to_formatted_text:
                    return to_formatted_text(fragments)
                else:
                    return FormattedText(fragments)
            except Exception:
                text_lines = []
                for style, text in fragments:
                    text_lines.append(text)
                return "".join(text_lines)
        
        # 创建键盘绑定
        kb = KeyBindings()
        
        # 处理字符输入
        @kb.add('<any>')
        def handle_any_key(event):
            """处理任意键输入"""
            if not state['editing']:
                return
            
            try:
                key = event.key_sequence[0].key if event.key_sequence else None
                if key:
                    # 跳过特殊键
                    if key in ('up', 'down', 'left', 'right', 'escape', 'c-c', 'tab', 'enter', 'backspace'):
                        return
                    
                    if len(key) == 1 and key.isprintable():
                        state['value'] = state['value'] + key
                        event.app.invalidate()
            except Exception:
                pass
        
        # 处理退格
        @kb.add('backspace')
        def handle_backspace(event):
            """处理退格键"""
            if state['editing'] and state['value']:
                state['value'] = state['value'][:-1]
                event.app.invalidate()
        
        # 处理回车确认
        @kb.add('enter')
        def handle_enter(event):
            """处理回车确认"""
            state['editing'] = False
            state['result'] = state['value'].strip()
            event.app.exit()
        
        # 处理 Esc 取消
        @kb.add('escape')
        def handle_escape(event):
            """处理 Esc 取消"""
            state['editing'] = False
            state['result'] = None
            event.app.exit()
        
        # 创建控件
        def get_text():
            result = get_formatted_text()
            if isinstance(result, FormattedText):
                return result
            elif isinstance(result, str):
                return result
            elif isinstance(result, list):
                return FormattedText(result)
            else:
                return str(result)
        
        control = FormattedTextControl(
            text=get_text,
            focusable=True
        )
        
        # 定义样式
        style_list = [
            ("input-title", "bold #ffffff"),
            ("search-box", "#9d4edd"),  # 浅紫色边框
            ("search-icon", "#9d4edd"),  # 放大镜图标
            ("input-text", "#ffffff"),  # 输入文本颜色
            ("input-placeholder", "#888888"),  # 占位符颜色
            ("input-cursor", "#9d4edd"),  # 光标颜色
            ("footer", "#888888"),
        ]
        
        if Style:
            try:
                style = Style(style_list)
            except Exception:
                style_dict = dict(style_list)
                try:
                    style = Style.from_dict(style_dict)
                except Exception:
                    style = None
        else:
            style = None
        
        # 创建布局
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # 创建应用
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            refresh_interval=0.1
        )
        
        try:
            app.run()
            return state['result']
        except KeyboardInterrupt:
            return None
        except Exception:
            return None
    
    def submit(self, message: str = "请确认", default: bool = True) -> bool:
        """
        获取用户确认/提交。
        
        Args:
            message: 确认消息
            default: 默认选择（True 为确认，False 为取消）
            
        Returns:
            True 表示确认/提交，False 表示取消
        """
        # 检查是否在真实终端
        is_terminal = sys.stdin.isatty()
        
        try:
            if is_terminal:
                confirmed = Confirm.ask(f"[cyan]{message}[/cyan]", default=default, console=self.console)
            else:
                # 非终端环境，使用简单的输入
                self.console.print(f"{message} (y/n) [{'Y/n' if default else 'y/N'}]: ", end="")
                response = input().strip().lower()
                if not response:
                    confirmed = default
                else:
                    confirmed = response in ('y', 'yes', 'true', '1')
            
            return confirmed
        except KeyboardInterrupt:
            self.console.print("\n[yellow]操作已取消。[/yellow]")
            return False
    
    def composite_menu(self, tabs: List[Dict[str, Any]], title: str = "复合菜单") -> Dict[str, Any]:
        """
        生成复合菜单，支持多个 tab，每个 tab 可以是多选、文本输入或提交。
        
        Args:
            tabs: Tab 配置列表，每个 tab 是一个字典，包含以下字段：
                - type: Tab 类型，可选值：
                    - 'multi_select': 多选
                    - 'text_input': 文本输入
                    - 'submit': 提交/确认
                - name: Tab 名称（用于标识和显示）
                - title: Tab 标题（显示给用户）
                - 对于 'multi_select' 类型，还需要：
                    - options: 选项列表（格式同 select_multiple）
                    - prompt: 提示文本（可选）
                - 对于 'text_input' 类型，还需要：
                    - prompt: 提示文本
                    - default: 默认值（可选）
                    - placeholder: 占位符（可选）
                - 对于 'submit' 类型，还需要：
                    - message: 确认消息
                    - default: 默认选择（可选，默认 True）
            title: 整体标题
            
        Returns:
            字典，包含每个 tab 的答案：
                - 对于 'multi_select': 返回选中的索引列表
                - 对于 'text_input': 返回输入的文本
                - 对于 'submit': 返回布尔值（True/False）
            key 为 tab 的 name，value 为对应的答案
            如果用户取消，返回 None
        """
        if not tabs:
            self.console.print("[red]没有配置任何 tab。[/red]")
            return {}
        
        # 检查是否在真实终端且 prompt_toolkit 可用
        is_terminal = sys.stdin.isatty()
        
        # 如果 prompt_toolkit 可用且在终端中，使用交互式界面
        if PROMPT_TOOLKIT_AVAILABLE and is_terminal:
            self.console.print("[red]交互式 tab。[/red]")
            return self._composite_menu_interactive(tabs, title)
        else:
            # 回退到顺序执行方式
            self.console.print("[red]顺序执行式 tab。[/red]")
            return self._composite_menu_sequential(tabs, title)
    
    def _composite_menu_interactive(self, tabs: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
        """
        使用 prompt_toolkit 实现的交互式复合菜单。
        所有 tabs（包括 text_input、multi_select、submit）都在交互式界面中处理。
        """
        results = {}
        current_tab_index = 0
        
        # 状态管理
        state = {
            'current_tab_index': 0,
            'results': {},
            'tab_states': {}  # 存储每个 tab 的状态（如多选的选中项）
        }
        
        # 所有 tabs 都在交互式界面中处理
        state['current_tab_index'] = 0  # 从第一个 tab 开始
        state['results'] = results
        state['all_tabs'] = tabs  # 存储所有 tabs
        
        # 初始化第一个 tab 的状态
        if tabs:
            first_tab = tabs[0]
            first_tab_name = first_tab.get('name')
            first_tab_type = first_tab.get('type')
            if first_tab_type == 'multi_select':
                state['tab_states'][f'{first_tab_name}_current'] = 0
                state['tab_states'][first_tab_name] = set()
            elif first_tab_type == 'text_input':
                default = first_tab.get('default', '')
                state['tab_states'][f'{first_tab_name}_value'] = default
                state['tab_states'][f'{first_tab_name}_editing'] = False
            elif first_tab_type == 'submit':
                state['tab_states']['submit_current'] = 0
        
        def get_formatted_text():
            """生成格式化的文本内容"""
            fragments = []
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return FormattedText([("", "")])
            
            # 当前tab索引（在所有tabs中的索引）
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                current_tab_idx = len(all_tabs) - 1
            if current_tab_idx < 0:
                current_tab_idx = 0
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            tab_name = current_tab.get('name', f'tab_{current_tab_idx}')
            tab_title = current_tab.get('title', tab_name)
            is_completed = tab_name in state['results']
            
            # 顶部导航栏 - 显示所有 tabs
            fragments.append(("class:nav", "← "))
            for idx, tab in enumerate(all_tabs):
                tab_display_name = tab.get('name', f'Tab {idx+1}')
                is_current = (idx == current_tab_idx)
                tab_is_completed = tab.get('name') in state['results']
                
                if is_current:
                    fragments.append(("class:nav-current", f"✓ {tab_display_name}"))
                elif tab_is_completed:
                    fragments.append(("class:nav-completed", f"□ {tab_display_name}"))
                else:
                    fragments.append(("class:nav-pending", f"□ {tab_display_name}"))
                
                if idx < len(all_tabs) - 1:
                    fragments.append(("class:nav", " "))
            
            fragments.append(("class:nav", " →\n"))
            fragments.append(("class:separator", "─" * 80 + "\n\n"))
            
            # 如果tab已完成且是text_input，显示review模式
            if is_completed and tab_type == 'text_input':
                # 显示问题和答案
                question = current_tab.get('prompt', tab_title)
                answer = state['results'].get(tab_name, '')
                
                fragments.append(("class:normal", "• "))
                fragments.append(("class:normal-label", f"{question}\n"))
                fragments.append(("class:nav-completed", "  → "))
                fragments.append(("class:normal-desc", f"{answer}\n\n"))
                
                fragments.append(("class:footer", "←→ 切换Tab · Esc 取消"))
                # 直接返回，不执行后续的tab类型显示逻辑
                try:
                    if to_formatted_text:
                        return to_formatted_text(fragments)
                    else:
                        return FormattedText(fragments)
                except Exception:
                    text_lines = []
                    for style, text in fragments:
                        text_lines.append(text)
                    return "".join(text_lines)
            else:
                # 主标题
                fragments.append(("class:title", f"{tab_title}\n\n"))
            
            # 根据 tab 类型显示不同内容
            if tab_type == 'multi_select':
                options = current_tab.get('options', [])
                # 解析选项
                def parse_option(opt):
                    if isinstance(opt, dict):
                        return opt.get('label', ''), opt.get('description', '')
                    elif isinstance(opt, (list, tuple)) and len(opt) >= 2:
                        return opt[0], opt[1]
                    else:
                        return str(opt), ""
                
                parsed_options = [parse_option(opt) for opt in options]
                selected_indices = state['tab_states'].get(tab_name, set())
                current_index = state['tab_states'].get(f'{tab_name}_current', 0)
                
                # 使用共享的渲染函数
                self._render_multi_select_options(
                    parsed_options, 
                    selected_indices, 
                    current_index, 
                    fragments
                )
                
                fragments.append(("", "\n"))
                selected_count = len(selected_indices)
                if selected_count > 0:
                    fragments.append(("class:footer", f"已选择 {selected_count} 项 · "))
                fragments.append(("class:footer", "Enter 选择 · ↑↓ 导航 · ←→ 切换Tab · Esc 取消"))
                
            elif tab_type == 'text_input':
                prompt = current_tab.get('prompt', '请输入')
                default = current_tab.get('default', ' ')
                placeholder = current_tab.get('placeholder', 'Search...')
                
                tab_name = current_tab.get('name')
                current_value = state['tab_states'].get(f'{tab_name}_value', default)
                is_editing = state['tab_states'].get(f'{tab_name}_editing', False)
                
                # 搜索框样式 - 圆角边框，浅紫色（类似图片中的样式）
                box_width = 60
                
                # 计算文本显示宽度（中文字符占2个宽度）
                def get_display_width(text):
                    """计算文本在终端中的显示宽度"""
                    width = 0
                    for char in text:
                        # 判断是否为中文字符或全角字符
                        if ord(char) > 127:
                            width += 2
                        else:
                            width += 1
                    return width
                
                # 上边框 - 圆角
                fragments.append(("class:search-box", "╭"))
                fragments.append(("class:search-box", "─" * (box_width - 2)))
                fragments.append(("class:search-box", "╮\n"))
                
                # 中间行 - 包含图标、输入内容和光标
                fragments.append(("class:search-box", "│"))
                
                # 放大镜图标（占3个字符宽度：空格+emoji+空格，emoji可能占2个显示宽度）
                icon_text = ""
                icon_display_width = 4  # 1空格 + 2(emoji) + 1空格
                fragments.append(("class:search-icon", icon_text))
                
                # 输入内容或占位符
                display_text = current_value if current_value else placeholder
                text_display_width = get_display_width(display_text)
                
                if current_value:
                    fragments.append(("class:input-text", display_text))
                else:
                    fragments.append(("class:input-placeholder", display_text))
                
                # 光标（如果正在编辑）
                cursor_text = ""
                cursor_width = 0
                if is_editing:
                    cursor_text = "▊"
                    cursor_width = 1
                    fragments.append(("class:input-cursor", cursor_text))
                
                # 填充剩余空间
                # 已用宽度：左边框(1) + 图标(4) + 文本宽度 + 光标宽度
                used_width = 1 + icon_display_width + text_display_width + cursor_width
                remaining = box_width - used_width - 1  # 减去右边框
                if remaining > 0:
                    fragments.append(("class:search-box", " " * remaining))
                
                fragments.append(("class:search-box", "│\n"))
                
                # 下边框 - 圆角
                fragments.append(("class:search-box", "╰"))
                fragments.append(("class:search-box", "─" * (box_width - 2)))
                fragments.append(("class:search-box", "╯\n"))
                
                fragments.append(("", "\n"))
                fragments.append(("class:footer", "Enter 确认 · ←→ 切换Tab · Esc 取消"))
                
            elif tab_type == 'submit':
                message = current_tab.get('message', 'Ready to submit your answers?')
                default = current_tab.get('default', True)
                
                fragments.append(("class:submit-message", f"{message}\n\n"))
                
                # 显示所有已回答的问题和答案
                all_tabs = state.get('all_tabs', [])
                for tab in all_tabs:
                    tab_name = tab.get('name')
                    if tab_name in state['results']:
                        question = tab.get('prompt', tab.get('title', tab_name))
                        answer = state['results'][tab_name]
                        
                        # 格式化答案
                        if isinstance(answer, list):
                            # 多选结果
                            options = tab.get('options', [])
                            def parse_option(opt):
                                if isinstance(opt, dict):
                                    return opt.get('label', '')
                                elif isinstance(opt, (list, tuple)) and len(opt) >= 1:
                                    return opt[0]
                                else:
                                    return str(opt)
                            answer_labels = [parse_option(options[i]) for i in answer if i < len(options)]
                            answer_str = ', '.join(answer_labels) if answer_labels else str(answer)
                        else:
                            answer_str = str(answer)
                        
                        fragments.append(("class:normal", "• "))
                        fragments.append(("class:normal-label", f"{question}\n"))
                        fragments.append(("class:nav-completed", "  → "))
                        fragments.append(("class:normal-desc", f"{answer_str}\n\n"))
                
                fragments.append(("", "\n"))
                
                # 显示选项
                submit_options = [
                    ("Submit answers", True),
                    ("Cancel", False)
                ]
                
                current_index = state['tab_states'].get('submit_current', 0 if default else 1)
                
                for idx, (label, value) in enumerate(submit_options):
                    is_current = idx == current_index
                    prefix = "> " if is_current else "  "
                    
                    if is_current:
                        item_style = "class:current"
                        label_style = "class:current-label"
                    else:
                        item_style = "class:normal"
                        label_style = "class:normal-label"
                    
                    fragments.append((item_style, prefix))
                    fragments.append((label_style, f"{label}\n"))
                
                fragments.append(("", "\n"))
                fragments.append(("class:footer", "Enter 选择 · ↑↓ 导航 · ←→ 切换Tab · Esc 取消"))
            
            # 确保返回 FormattedText 对象
            try:
                if to_formatted_text:
                    return to_formatted_text(fragments)
                else:
                    return FormattedText(fragments)
            except Exception:
                text_lines = []
                for style, text in fragments:
                    text_lines.append(text)
                return "".join(text_lines)
        
        # 创建键盘绑定
        kb = KeyBindings()
        
        def move_up(event):
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            tab_name = current_tab.get('name')
            
            if tab_type == 'multi_select':
                options = current_tab.get('options', [])
                current_index = state['tab_states'].get(f'{tab_name}_current', 0)
                if current_index > 0:
                    state['tab_states'][f'{tab_name}_current'] = current_index - 1
                    event.app.invalidate()
            elif tab_type == 'submit':
                current_index = state['tab_states'].get('submit_current', 0)
                if current_index > 0:
                    state['tab_states']['submit_current'] = current_index - 1
                    event.app.invalidate()
        
        def move_down(event):
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            tab_name = current_tab.get('name')
            
            if tab_type == 'multi_select':
                options = current_tab.get('options', [])
                current_index = state['tab_states'].get(f'{tab_name}_current', 0)
                if current_index < len(options) - 1:
                    state['tab_states'][f'{tab_name}_current'] = current_index + 1
                    event.app.invalidate()
            elif tab_type == 'submit':
                current_index = state['tab_states'].get('submit_current', 0)
                if current_index < 1:
                    state['tab_states']['submit_current'] = current_index + 1
                    event.app.invalidate()
        
        def toggle_selection(event):
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            
            if tab_type == 'multi_select':
                tab_name = current_tab.get('name')
                options = current_tab.get('options', [])
                selected_indices = state['tab_states'].get(tab_name, set())
                current_index = state['tab_states'].get(f'{tab_name}_current', 0)
                
                if current_index in selected_indices:
                    selected_indices.remove(current_index)
                else:
                    selected_indices.add(current_index)
                state['tab_states'][tab_name] = selected_indices
                event.app.invalidate()
        
        def handle_enter(event):
            """处理回车键：多选时切换选择，其他情况确认"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            
            # 如果是多选类型，回车键切换选择
            if tab_type == 'multi_select':
                toggle_selection(event)
            else:
                # 其他类型，回车键确认
                confirm_selection(event)
        
        def confirm_selection(event):
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                event.app.exit()
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                event.app.exit()
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            tab_name = current_tab.get('name')
            is_completed = tab_name in state['results']
            
            # 如果已完成且是text_input，不做任何操作（review模式）
            if is_completed and tab_type == 'text_input':
                return
            
            if tab_type == 'multi_select':
                selected_indices = state['tab_states'].get(tab_name, set())
                if selected_indices:
                    state['results'][tab_name] = sorted(list(selected_indices))
                    # 移动到下一个 tab
                    if current_tab_idx < len(all_tabs) - 1:
                        state['current_tab_index'] = current_tab_idx + 1
                        # 重置下一个 tab 的状态
                        next_tab = all_tabs[state['current_tab_index']]
                        next_tab_name = next_tab.get('name')
                        if next_tab.get('type') == 'multi_select':
                            if f'{next_tab_name}_current' not in state['tab_states']:
                                state['tab_states'][f'{next_tab_name}_current'] = 0
                            if next_tab_name not in state['tab_states']:
                                if next_tab_name in state['results']:
                                    state['tab_states'][next_tab_name] = set(state['results'][next_tab_name])
                                else:
                                    state['tab_states'][next_tab_name] = set()
                        elif next_tab.get('type') == 'submit':
                            state['tab_states']['submit_current'] = 0
                        event.app.invalidate()
                    else:
                        # 所有 tab 完成，退出
                        event.app.exit()
                else:
                    # 至少需要选择一个
                    pass
            elif tab_type == 'text_input':
                # 结束编辑模式
                state['tab_states'][f'{tab_name}_editing'] = False
                current_value = state['tab_states'].get(f'{tab_name}_value', '')
                
                if current_value.strip() or current_tab.get('allow_empty', False):
                    state['results'][tab_name] = current_value.strip()
                    # 移动到下一个 tab
                    if current_tab_idx < len(all_tabs) - 1:
                        state['current_tab_index'] = current_tab_idx + 1
                        # 重置下一个 tab 的状态
                        next_tab = all_tabs[state['current_tab_index']]
                        next_tab_name = next_tab.get('name')
                        if next_tab.get('type') == 'multi_select':
                            if f'{next_tab_name}_current' not in state['tab_states']:
                                state['tab_states'][f'{next_tab_name}_current'] = 0
                            if next_tab_name not in state['tab_states']:
                                if next_tab_name in state['results']:
                                    state['tab_states'][next_tab_name] = set(state['results'][next_tab_name])
                                else:
                                    state['tab_states'][next_tab_name] = set()
                        elif next_tab.get('type') == 'submit':
                            state['tab_states']['submit_current'] = 0
                        event.app.invalidate()
                    else:
                        # 所有 tab 完成，退出
                        event.app.exit()
                else:
                    # 如果为空且不允许空值，重新进入编辑模式
                    state['tab_states'][f'{tab_name}_editing'] = True
                    event.app.invalidate()
            elif tab_type == 'submit':
                current_index = state['tab_states'].get('submit_current', 0)
                if current_index == 0:  # Submit
                    state['results'][tab_name] = True
                    event.app.exit()
                else:  # Cancel
                    state['results'][tab_name] = False
                    event.app.exit()
        
        def move_left(event):
            """移动到上一个 tab（所有tabs中）"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                current_tab_idx = len(all_tabs) - 1
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            tab_name = current_tab.get('name')
            
            # 保存当前 tab 的状态（如果是交互式tab）
            if tab_type == 'multi_select':
                selected_indices = state['tab_states'].get(tab_name, set())
                if selected_indices:
                    state['results'][tab_name] = sorted(list(selected_indices))
            elif tab_type == 'text_input':
                # 保存文本输入的值
                current_value = state['tab_states'].get(f'{tab_name}_value', '')
                if current_value:
                    state['results'][tab_name] = current_value
                # 退出编辑模式
                state['tab_states'][f'{tab_name}_editing'] = False
            
            # 切换到上一个 tab
            if current_tab_idx > 0:
                state['current_tab_index'] = current_tab_idx - 1
                # 初始化前一个 tab 的状态（如果是交互式tab）
                prev_tab = all_tabs[state['current_tab_index']]
                prev_tab_name = prev_tab.get('name')
                prev_tab_type = prev_tab.get('type')
                
                if prev_tab_type == 'multi_select':
                    if f'{prev_tab_name}_current' not in state['tab_states']:
                        state['tab_states'][f'{prev_tab_name}_current'] = 0
                    if prev_tab_name not in state['tab_states']:
                        # 如果之前有结果，恢复选中状态
                        if prev_tab_name in state['results']:
                            state['tab_states'][prev_tab_name] = set(state['results'][prev_tab_name])
                        else:
                            state['tab_states'][prev_tab_name] = set()
                elif prev_tab_type == 'text_input':
                    # 恢复文本输入的值（如果之前有输入）
                    if prev_tab_name in state['results']:
                        state['tab_states'][f'{prev_tab_name}_value'] = state['results'][prev_tab_name]
                    else:
                        default = prev_tab.get('default', '')
                        state['tab_states'][f'{prev_tab_name}_value'] = default
                    state['tab_states'][f'{prev_tab_name}_editing'] = False
                elif prev_tab_type == 'submit':
                    state['tab_states']['submit_current'] = 0
                
                event.app.invalidate()
        
        def move_right(event):
            """移动到下一个 tab（所有tabs中）"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                current_tab_idx = len(all_tabs) - 1
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            tab_name = current_tab.get('name')
            
            # 保存当前 tab 的状态（如果是交互式tab）
            if tab_type == 'multi_select':
                selected_indices = state['tab_states'].get(tab_name, set())
                if selected_indices:
                    state['results'][tab_name] = sorted(list(selected_indices))
            elif tab_type == 'text_input':
                # 保存文本输入的值
                current_value = state['tab_states'].get(f'{tab_name}_value', '')
                if current_value:
                    state['results'][tab_name] = current_value
                # 退出编辑模式
                state['tab_states'][f'{tab_name}_editing'] = False
            
            # 切换到下一个 tab
            if current_tab_idx < len(all_tabs) - 1:
                state['current_tab_index'] = current_tab_idx + 1
                # 初始化下一个 tab 的状态（如果是交互式tab）
                next_tab = all_tabs[state['current_tab_index']]
                next_tab_name = next_tab.get('name')
                next_tab_type = next_tab.get('type')
                
                if next_tab_type == 'multi_select':
                    if f'{next_tab_name}_current' not in state['tab_states']:
                        state['tab_states'][f'{next_tab_name}_current'] = 0
                    if next_tab_name not in state['tab_states']:
                        # 如果之前有结果，恢复选中状态
                        if next_tab_name in state['results']:
                            state['tab_states'][next_tab_name] = set(state['results'][next_tab_name])
                        else:
                            state['tab_states'][next_tab_name] = set()
                elif next_tab_type == 'text_input':
                    # 恢复文本输入的值（如果之前有输入）
                    if next_tab_name in state['results']:
                        state['tab_states'][f'{next_tab_name}_value'] = state['results'][next_tab_name]
                    else:
                        default = next_tab.get('default', '')
                        state['tab_states'][f'{next_tab_name}_value'] = default
                    state['tab_states'][f'{next_tab_name}_editing'] = False
                elif next_tab_type == 'submit':
                    state['tab_states']['submit_current'] = 0
                
                event.app.invalidate()
        
        
        def cancel_selection(event):
            """取消操作"""
            event.app.exit(result=None)
        
        # 绑定按键
        kb.add("up")(move_up)
        kb.add("k")(move_up)
        kb.add("down")(move_down)
        kb.add("j")(move_down)
        kb.add("left")(move_left)
        kb.add("right")(move_right)
        kb.add(" ")(toggle_selection)
        kb.add("enter")(handle_enter)
        kb.add("c-m")(handle_enter)  # Ctrl+M 也是回车键
        kb.add("tab")(confirm_selection)
        kb.add("c-c")(cancel_selection)
        kb.add("escape")(cancel_selection)
        def handle_backspace(event):
            """处理退格键（用于文本输入）"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_name = current_tab.get('name')
            is_completed = tab_name in state['results']
            
            # 如果已完成，不允许编辑
            if is_completed:
                return
            
            if current_tab.get('type') == 'text_input':
                if state['tab_states'].get(f'{tab_name}_editing', False):
                    current_value = state['tab_states'].get(f'{tab_name}_value', '')
                    if current_value:
                        state['tab_states'][f'{tab_name}_value'] = current_value[:-1]
                        event.app.invalidate()
        
        kb.add("backspace")(handle_backspace)
        
        # 为文本输入绑定字符输入（使用 <any> 但需要检查 tab 类型）
        @kb.add('<any>')
        def handle_any_key(event):
            """处理任意键输入（用于文本输入）"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_name = current_tab.get('name')
            is_completed = tab_name in state['results']
            
            # 如果已完成，不允许编辑
            if is_completed:
                return
            
            if current_tab.get('type') == 'text_input':
                if not state['tab_states'].get(f'{tab_name}_editing', False):
                    # 如果还没开始编辑，先开始编辑
                    state['tab_states'][f'{tab_name}_editing'] = True
                
                # 处理字符输入
                try:
                    key = event.key_sequence[0].key if event.key_sequence else None
                    if key:
                        # 跳过特殊键
                        if key in ('up', 'down', 'left', 'right', 'escape', 'c-c', 'tab', 'enter', 'backspace'):
                            return
                        
                        if len(key) == 1 and key.isprintable():
                            current_value = state['tab_states'].get(f'{tab_name}_value', '')
                            state['tab_states'][f'{tab_name}_value'] = current_value + key
                            event.app.invalidate()
                except Exception:
                    pass
        
        # 创建控件
        def get_text():
            result = get_formatted_text()
            if isinstance(result, FormattedText):
                return result
            elif isinstance(result, str):
                return result
            elif isinstance(result, list):
                return FormattedText(result)
            else:
                return str(result)
        
        control = FormattedTextControl(
            text=get_text,
            focusable=True
        )
        
        # 定义样式
        style_list = [
            ("title", "bold #ffffff"),
            ("nav", "#888888"),
            ("nav-current", "bold #9d4edd"),
            ("nav-completed", "#888888"),
            ("nav-pending", "#888888"),
            ("separator", "#444444"),
            ("number", "#888888"),
            ("checkbox", "#9d4edd"),
            ("prefix", "#9d4edd"),
            ("current", "bg:#9d4edd #ffffff"),
            ("current-label", "bg:#9d4edd bold #ffffff"),
            ("current-desc", "bg:#9d4edd #e0e0e0"),
            ("current-selected", "bg:#7b2cbf #ffffff"),
            ("current-selected-label", "bg:#7b2cbf bold #ffffff"),
            ("current-selected-desc", "bg:#7b2cbf #e0e0e0"),
            ("selected", "#9d4edd"),
            ("selected-label", "#9d4edd"),
            ("selected-desc", "#888888"),
            ("normal", "#ffffff"),
            ("normal-label", "#ffffff"),
            ("normal-desc", "#888888"),
            ("footer", "#888888"),
            ("instruction", "#888888"),
            ("input-prompt", "#ffffff"),
            ("input-value", "#9d4edd"),
            ("input-title", "bold #ffffff"),
            ("search-box", "#9d4edd"),  # 浅紫色边框
            ("search-icon", "#9d4edd"),  # 放大镜图标
            ("input-text", "#ffffff"),  # 输入文本颜色
            ("input-placeholder", "#888888"),  # 占位符颜色
            ("input-cursor", "#9d4edd"),  # 光标颜色
            ("submit-message", "#ffffff"),
        ]
        
        if Style:
            try:
                style = Style(style_list)
            except Exception:
                style_dict = dict(style_list)
                try:
                    style = Style.from_dict(style_dict)
                except Exception:
                    style = None
        else:
            style = None
        
        # 创建布局
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # 创建应用
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            refresh_interval=0.1
        )
        
        try:
            app.run()
            
            # 所有 tabs 都在交互式界面中处理完成，直接返回结果
            return state['results'] if state['results'] else None
        except KeyboardInterrupt:
            return None
    
    def _composite_menu_sequential(self, tabs: List[Dict[str, Any]], title: str) -> Dict[str, Any]:
        """
        顺序执行方式（非交互式，用于非终端环境）。
        """
        results = {}
        
        self.console.print(f"[bold]{title}[/bold]\n")
        
        for idx, tab in enumerate(tabs):
            tab_type = tab.get('type')
            tab_name = tab.get('name', f'tab_{idx}')
            tab_title = tab.get('title', tab_name)
            
            self.console.print(f"\n[cyan]步骤 {idx + 1}/{len(tabs)}: {tab_title}[/cyan]")
            
            if tab_type == 'multi_select':
                options = tab.get('options', [])
                prompt = tab.get('prompt', '请选择（可多选）')
                selected_indices = self.select_multiple(options, tab_title, prompt)
                results[tab_name] = selected_indices
                
            elif tab_type == 'text_input':
                prompt = tab.get('prompt', '请输入')
                default = tab.get('default', '')
                placeholder = tab.get('placeholder')
                user_input = self.text_input(prompt, default, placeholder)
                if user_input is None:
                    return None  # 用户取消
                results[tab_name] = user_input
                
            elif tab_type == 'submit':
                message = tab.get('message', '请确认')
                default = tab.get('default', True)
                confirmed = self.submit(message, default)
                results[tab_name] = confirmed
                if not confirmed:
                    return None  # 用户取消
        
        return results

    def single_select(self, options: List, title: str = "请选择", warning: Optional[str] = None, question: Optional[str] = None, nav_items: Optional[List[Dict[str, Any]]] = None) -> Optional[int]:
        """
        展示单选列表并支持选择，返回选中的选项索引。
        支持键盘上下箭头导航和回车键选择。
        现代化的界面设计，支持警告信息和导航栏。
        
        Args:
            options: 选项列表，支持以下格式：
                - List[str]: 简单字符串列表，如 ["选项1", "选项2"]
                - List[dict]: 字典列表，每个字典包含 'label' 和 'description' 键
                  如 [{"label": "选项1", "description": "这是选项1的描述"}, ...]
                - List[tuple]: 元组列表，每个元组包含 (label, description)
                  如 [("选项1", "这是选项1的描述"), ...]
            title: 标题
            warning: 可选的警告信息（会显示黄色警告图标）
            question: 可选的问题文本（显示在警告下方）
            nav_items: 可选的导航栏项目列表，每个项目包含：
                - 'label': 标签文本
                - 'checked': 是否选中（显示复选框）
                - 'type': 类型（'checkbox' 或 'button'）
                - 'highlight': 是否高亮（用于Submit按钮等）
            
        Returns:
            选中的选项索引（从0开始），如果取消则返回 None
        """
        if not options:
            self.console.print("[red]没有可选项。[/red]")
            return None
        
        # 检查是否在真实终端且 prompt_toolkit 可用
        is_terminal = sys.stdin.isatty()
        
        # 如果 prompt_toolkit 可用且在终端中，使用交互式界面
        if PROMPT_TOOLKIT_AVAILABLE and is_terminal:
            return self._single_select_interactive(options, title, warning, question, nav_items)
        else:
            # 回退到简单的文本输入方式
            return self._single_select_text_input(options, title)
    
    def _single_select_interactive(self, options: List, title: str, warning: Optional[str], question: Optional[str], nav_items: Optional[List[Dict[str, Any]]]) -> Optional[int]:
        """
        使用 prompt_toolkit 实现的交互式单选列表。
        支持上下箭头导航，回车键选择。
        现代化的界面设计，类似图片中的样式。
        """
        # 解析选项：支持字符串或字典格式
        def parse_option(opt):
            """解析选项，支持字符串或字典格式"""
            if isinstance(opt, dict):
                return opt.get('label', ''), opt.get('description', '')
            elif isinstance(opt, (list, tuple)) and len(opt) >= 2:
                return opt[0], opt[1]
            else:
                return str(opt), ""
        
        parsed_options = [parse_option(opt) for opt in options]
        
        # 使用列表来存储状态，以便在闭包中修改
        state = {
            'selected_index': None,
            'current_index': 0
        }
        
        def get_formatted_text():
            """生成格式化的文本内容"""
            fragments = []
            
            # 顶部导航栏
            if nav_items:
                fragments.append(("class:nav", "← "))
                for idx, nav_item in enumerate(nav_items):
                    nav_label = nav_item.get('label', '')
                    nav_type = nav_item.get('type', 'checkbox')
                    nav_checked = nav_item.get('checked', False)
                    nav_highlight = nav_item.get('highlight', False)
                    
                    if nav_type == 'button' and nav_highlight:
                        # Submit按钮样式（紫色背景，白色对勾）
                        fragments.append(("class:nav-button-highlight", f"✓ {nav_label}"))
                    elif nav_type == 'checkbox':
                        checkbox = "[✓]" if nav_checked else "[ ]"
                        if nav_highlight:
                            fragments.append(("class:nav-checkbox-highlight", f"{checkbox} {nav_label}"))
                        else:
                            fragments.append(("class:nav-checkbox", f"{checkbox} {nav_label}"))
                    else:
                        fragments.append(("class:nav", nav_label))
                    
                    if idx < len(nav_items) - 1:
                        fragments.append(("class:nav", " "))
                fragments.append(("class:nav", " →\n"))
                fragments.append(("", "\n"))
            
            # 标题
            fragments.append(("class:title", f"{title}\n"))
            fragments.append(("", "\n"))
            
            # 警告信息
            if warning:
                fragments.append(("class:warning-icon", "⚠ "))
                fragments.append(("class:warning-text", f"{warning}\n"))
                fragments.append(("", "\n"))
            
            # 问题文本
            if question:
                fragments.append(("class:question", f"{question}\n"))
                fragments.append(("", "\n"))
            
            # 选项列表
            for idx, (label, description) in enumerate(parsed_options):
                # 判断是否是当前高亮项
                is_current = idx == state['current_index']
                
                # 构建每行的格式
                # 序号
                number = f"{idx + 1}. "
                
                # 箭头
                if is_current:
                    prefix = "> "
                else:
                    prefix = "  "
                
                # 设置样式
                if is_current:
                    item_style = "class:current"
                    label_style = "class:current-label"
                    desc_style = "class:current-desc"
                else:
                    item_style = "class:normal"
                    label_style = "class:normal-label"
                    desc_style = "class:normal-desc"
                
                # 构建选项行
                fragments.append((item_style, prefix))
                fragments.append(("class:number", number))
                fragments.append((label_style, f"{label}"))
                
                # 如果有描述，添加描述
                if description:
                    fragments.append(("", "\n"))
                    fragments.append((item_style, "     "))  # 缩进
                    fragments.append((desc_style, f"    {description}"))
                
                fragments.append(("", "\n"))
            
            fragments.append(("", "\n"))
            
            # 底部提示
            fragments.append(("class:footer", "Enter 选择 · 方向键 导航 · Esc 取消"))
            
            # 确保返回 FormattedText 对象
            try:
                if to_formatted_text:
                    return to_formatted_text(fragments)
                else:
                    return FormattedText(fragments)
            except Exception:
                # 如果 FormattedText 构造失败，尝试直接返回字符串
                text_lines = []
                for style, text in fragments:
                    text_lines.append(text)
                return "".join(text_lines)
        
        # 创建键盘绑定
        kb = KeyBindings()
        
        def move_up(event):
            if state['current_index'] > 0:
                state['current_index'] -= 1
                # 触发界面更新
                event.app.invalidate()
        
        def move_down(event):
            if state['current_index'] < len(options) - 1:
                state['current_index'] += 1
                # 触发界面更新
                event.app.invalidate()
        
        def confirm_selection(event):
            state['selected_index'] = state['current_index']
            event.app.exit()
        
        def cancel_selection(event):
            state['selected_index'] = None
            event.app.exit()
        
        # 绑定按键
        kb.add("up")(move_up)
        kb.add("k")(move_up)  # vim 风格
        kb.add("down")(move_down)
        kb.add("j")(move_down)  # vim 风格
        kb.add("left")(move_up)  # 左箭头也支持
        kb.add("right")(move_down)  # 右箭头也支持
        kb.add("enter")(confirm_selection)  # 回车键确认
        kb.add("c-c")(cancel_selection)  # Ctrl+C 取消
        kb.add("escape")(cancel_selection)  # ESC 取消
        
        # 创建控件 - 使用可调用对象来动态更新
        def get_text():
            result = get_formatted_text()
            # 确保返回的是 FormattedText 对象或字符串
            if isinstance(result, FormattedText):
                return result
            elif isinstance(result, str):
                return result
            elif isinstance(result, list):
                # 如果返回的是列表，转换为 FormattedText
                return FormattedText(result)
            else:
                # 其他情况，尝试转换为字符串
                return str(result)
        
        control = FormattedTextControl(
            text=get_text,
            focusable=True
        )
        
        # 定义样式 - 使用列表格式，现代化的配色方案
        style_list = [
            ("title", "bold #ffffff"),  # 白色粗体标题
            ("number", "#888888"),  # 灰色序号
            ("prefix", "#9d4edd"),  # 紫色箭头
            # 导航栏样式
            ("nav", "#888888"),  # 灰色导航文字
            ("nav-checkbox", "#888888"),  # 灰色复选框
            ("nav-checkbox-highlight", "#9d4edd"),  # 紫色高亮复选框
            ("nav-button-highlight", "bg:#9d4edd #ffffff bold"),  # 紫色背景按钮
            # 当前选中项 - 紫色高亮（类似图片）
            ("current", "bg:#9d4edd #ffffff"),  # 紫色背景，白色文字
            ("current-label", "bg:#9d4edd bold #ffffff"),  # 粗体标签
            ("current-desc", "bg:#9d4edd #e0e0e0"),  # 浅灰色描述
            # 普通项
            ("normal", "#ffffff"),  # 白色文字
            ("normal-label", "#ffffff"),
            ("normal-desc", "#888888"),  # 灰色描述
            # 警告样式
            ("warning-icon", "#ffd60a"),  # 黄色警告图标
            ("warning-text", "#ffd60a"),  # 黄色警告文字
            # 问题样式
            ("question", "#e0e0e0"),  # 浅灰色问题文字
            # 底部提示
            ("footer", "#888888"),  # 灰色提示文字
        ]
        
        # 创建 Style 对象
        if Style:
            try:
                style = Style(style_list)
            except Exception:
                # 如果 Style 构造失败，尝试使用 from_dict
                style_dict = dict(style_list)
                try:
                    style = Style.from_dict(style_dict)
                except Exception:
                    style = None
        else:
            style = None
        
        # 创建布局
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # 创建应用
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            refresh_interval=0.1  # 定期刷新以更新显示
        )
        
        try:
            app.run()
        except KeyboardInterrupt:
            return None
        
        # 返回选中的索引
        return state['selected_index']
    
    def _single_select_text_input(self, options: List[str], title: str) -> Optional[int]:
        """
        回退的文本输入方式（当 prompt_toolkit 不可用或不在终端时）。
        """
        # 创建表格展示选项
        table = Table(title=title, box=box.ROUNDED, width=80)
        table.add_column("编号", style="cyan", justify="right", width=8)
        table.add_column("选项", style="magenta")
        
        for idx, option in enumerate(options, 1):
            table.add_row(str(idx), option)
        
        self.console.print(table)
        self.console.print("[dim]输入 'exit' 或 'cancel' 取消选择。[/dim]")
        
        # 检查是否在真实终端
        is_terminal = sys.stdin.isatty()
        
        while True:
            if is_terminal:
                choice = Prompt.ask(f"[cyan]请选择选项编号[/cyan]", default="", console=self.console)
            else:
                self.console.print("请选择选项编号: ", end="")
                choice = input().strip()
            
            # 检查取消命令
            if choice.lower() in ("exit", "quit", "q", "cancel"):
                self.console.print("[yellow]选择已取消。[/yellow]")
                return None
            
            if not choice:
                self.console.print("[red]请输入选项编号。[/red]")
                continue
            
            try:
                idx = int(choice) - 1  # 转换为0-based索引
                if 0 <= idx < len(options):
                    self.console.print(f"[green]已选择: {options[idx]}[/green]")
                    return idx
                else:
                    self.console.print(f"[red]无效的选项编号: {choice}。请重新输入。[/red]")
            except ValueError:
                self.console.print("[red]请输入有效的数字编号。[/red]")
        