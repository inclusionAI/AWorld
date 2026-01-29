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

from aworld.logs.util import logger

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
    
    def select_multiple(self, options: List, title: str = "Select (Multiple)", prompt: str = "Enter option numbers (comma-separated, e.g., 1,3,5)") -> List[int]:
        """
        Display a multi-select list with checkboxes, return selected option indices.
        Supports keyboard navigation with arrow keys and Enter to toggle selection.
        Modern interface design with support for option descriptions.
        
        Args:
            options: List of options, supports the following formats:
                - List[str]: Simple string list, e.g., ["Option 1", "Option 2"]
                - List[dict]: Dictionary list, each dict contains 'label' and 'description' keys
                  e.g., [{"label": "Option 1", "description": "Description of option 1"}, ...]
                - List[tuple]: Tuple list, each tuple contains (label, description)
                  e.g., [("Option 1", "Description of option 1"), ...]
            title: Title
            prompt: Prompt text (used when prompt_toolkit is not available)
            
        Returns:
            List of selected option indices (0-based)
        """
        if not options:
            self.console.print("[red]No options available.[/red]")
            return []
        
        # Check if in a real terminal and prompt_toolkit is available
        is_terminal = sys.stdin.isatty()
        
        # If prompt_toolkit is available and in terminal, use interactive interface
        if PROMPT_TOOLKIT_AVAILABLE and is_terminal:
            return self._select_multiple_interactive(options, title)
        else:
            # Fallback to text input method
            return self._select_multiple_text_input(options, title, prompt)
    
    def _render_multi_select_options(self, parsed_options: List[tuple], selected_indices: Set[int], 
                                     current_index: int, fragments: List[tuple]) -> None:
        """
        Render the options section of the multi-select list (shared code).
        
        Args:
            parsed_options: Parsed option list, each element is a (label, description) tuple
            selected_indices: Set of selected indices
            current_index: Currently highlighted index
            fragments: List of fragments to append to
        """
        for idx, (label, description) in enumerate(parsed_options):
            # Check if selected
            is_selected = idx in selected_indices
            # Check if current highlighted item
            is_current = idx == current_index
            
            # Build format for each line
            # Number
            number = f"{idx + 1}.  "
            
            # Checkbox and arrow
            if is_current:
                prefix = "> "
                checkbox = "[✓]" if is_selected else "[ ]"
            else:
                prefix = "  "
                checkbox = "[✓]" if is_selected else "[ ]"
            
            # Set styles
            if is_current and is_selected:
                item_style = "class:current-selected"
                label_style = "class:current-selected-label"
                desc_style = "class:normal-desc"  # Description line always uses normal style
            elif is_current:
                item_style = "class:current"
                label_style = "class:current-label"
                desc_style = "class:normal-desc"  # Description line always uses normal style
            elif is_selected:
                item_style = "class:selected"
                label_style = "class:selected-label"
                desc_style = "class:selected-desc"
            else:
                item_style = "class:normal"
                label_style = "class:normal-label"
                desc_style = "class:normal-desc"
            
            # Build option row
            fragments.append((item_style, prefix))
            fragments.append(("class:number", number))
            fragments.append(("class:checkbox", checkbox))
            fragments.append((label_style, f" {label}"))
            
            # Add description if available
            if description:
                fragments.append(("", "\n"))
                fragments.append((item_style, "     "))  # Indent
                fragments.append((desc_style, f"    {description}"))
            
            fragments.append(("", "\n"))
    
    def _select_multiple_interactive(self, options: List, title: str) -> List[int]:
        """
        Interactive multi-select box implemented using prompt_toolkit.
        Supports up/down arrow navigation, Enter to toggle selection.
        Modern interface design, similar to the style in the image.
        """
        # Parse options: supports string or dict format
        def parse_option(opt):
            """Parse option, supports string or dict format"""
            if isinstance(opt, dict):
                return opt.get('label', ''), opt.get('description', '')
            elif isinstance(opt, (list, tuple)) and len(opt) >= 2:
                return opt[0], opt[1]
            else:
                return str(opt), ""
        
        parsed_options = [parse_option(opt) for opt in options]
        
        # Use list to store state for modification in closure
        state = {
            'selected_indices': set(),
            'current_index': 0
        }
        
        def get_formatted_text():
            """Generate formatted text content"""
            fragments = []
            
            # Title - more modern style
            fragments.append(("class:title", f"● {title}\n"))
            fragments.append(("", "\n"))
            
            # Option list - use shared render function
            self._render_multi_select_options(
                parsed_options, 
                state['selected_indices'], 
                state['current_index'], 
                fragments
            )
            
            fragments.append(("", "\n"))
            
            # Bottom hint - clearer format
            selected_count = len(state['selected_indices'])
            if selected_count > 0:
                fragments.append(("class:footer", f"Selected {selected_count} item(s) · "))
            fragments.append(("class:footer", "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"))
            
            # Ensure FormattedText object is returned
            try:
                if to_formatted_text:
                    return to_formatted_text(fragments)
                else:
                    return FormattedText(fragments)
            except Exception:
                # If FormattedText construction fails, try returning string directly
                text_lines = []
                for style, text in fragments:
                    text_lines.append(text)
                return "".join(text_lines)
        
        # Create keyboard bindings
        kb = KeyBindings()
        
        def move_up(event):
            if state['current_index'] > 0:
                state['current_index'] -= 1
                # Trigger UI update
                event.app.invalidate()
        
        def move_down(event):
            if state['current_index'] < len(options) - 1:
                state['current_index'] += 1
                # Trigger UI update
                event.app.invalidate()
        
        def toggle_selection(event):
            """Toggle selection state"""
            if state['current_index'] in state['selected_indices']:
                state['selected_indices'].remove(state['current_index'])
            else:
                state['selected_indices'].add(state['current_index'])
            # Trigger UI update
            event.app.invalidate()
        
        def confirm_selection(event):
            event.app.exit()
        
        def cancel_selection(event):
            state['selected_indices'].clear()
            event.app.exit()
        
        # Bind keys
        kb.add("up")(move_up)
        kb.add("k")(move_up)  # vim style
        kb.add("down")(move_down)
        kb.add("j")(move_down)  # vim style
        kb.add("left")(move_up)  # Left arrow also supported
        kb.add("right")(move_down)  # Right arrow also supported
        kb.add(" ")(toggle_selection)  # Space to toggle selection
        kb.add("enter")(toggle_selection)  # Enter to toggle selection
        kb.add("c-m")(toggle_selection)  # Ctrl+M is also Enter
        kb.add("tab")(confirm_selection)  # Tab to complete selection
        kb.add("c-c")(cancel_selection)  # Ctrl+C to cancel
        kb.add("escape")(cancel_selection)  # ESC to cancel
        
        # Create control - use callable object for dynamic updates
        # Note: text parameter should be a callable that returns FormattedText or string
        # Wrap function to ensure correct return type
        def get_text():
            result = get_formatted_text()
            # Ensure FormattedText object or string is returned, not list
            if isinstance(result, FormattedText):
                return result
            elif isinstance(result, str):
                return result
            elif isinstance(result, list):
                # If list is returned, convert to FormattedText
                return FormattedText(result)
            else:
                # Other cases, try converting to string
                return str(result)
        
        control = FormattedTextControl(
            text=get_text,
            focusable=True
        )
        
        # Define styles - use list format, modern color scheme
        # Similar to purple highlight and clear visual hierarchy in the image
        # prompt_toolkit Style accepts list of (class_name, style_string) tuples
        # Note: Style constructor expects class names without "class:" prefix
        style_list = [
            ("title", "bold #ffffff"),  # White bold title
            ("number", "#888888"),  # Gray number
            ("checkbox", "#9d4edd"),  # Purple checkbox (similar to purple theme in image)
            ("prefix", "#9d4edd"),  # Purple arrow
            # Current selected item - purple background highlight (similar to image)
            ("current", "bg:#9d4edd #ffffff"),  # Purple background, white text
            ("current-label", "bg:#9d4edd bold #ffffff"),  # Bold label
            ("current-desc", "bg:#9d4edd #e0e0e0"),  # Light gray description
            # Current selected and checked
            ("current-selected", "bg:#7b2cbf #ffffff"),  # Dark purple background
            ("current-selected-label", "bg:#7b2cbf bold #ffffff"),
            ("current-selected-desc", "bg:#7b2cbf #e0e0e0"),
            # Checked but not current item
            ("selected", "#9d4edd"),  # Purple text
            ("selected-label", "#9d4edd"),
            ("selected-desc", "#888888"),
            # Normal item
            ("normal", "#ffffff"),  # White text
            ("normal-label", "#ffffff"),
            ("normal-desc", "#888888"),  # Gray description
            # Bottom hint
            ("footer", "#888888"),  # Gray hint text
        ]
        
        # Create Style object - use list format
        # prompt_toolkit will automatically match class names in Style with "class:xxx" format in FormattedText
        if Style:
            try:
                style = Style(style_list)
            except Exception:
                # If Style construction fails, try using from_dict
                style_dict = dict(style_list)
                try:
                    style = Style.from_dict(style_dict)
                except Exception:
                    style = None
        else:
            style = None
        
        # Create layout
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # Create application
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            refresh_interval=0.1  # Periodic refresh to update display
        )
        
        try:
            app.run()
        except KeyboardInterrupt:
            return []
        
        # Return selected index list (sorted)
        return sorted(list(state['selected_indices']))
    
    def _select_multiple_text_input(self, options: List[str], title: str, prompt: str) -> List[int]:
        """
        Fallback text input method (when prompt_toolkit is not available or not in terminal).
        """
        # Create table to display options
        table = Table(title=title, box=box.ROUNDED, width=80)
        table.add_column("No.", style="cyan", justify="right", width=8)
        table.add_column("Option", style="magenta")
        
        for idx, option in enumerate(options, 1):
            table.add_row(str(idx), option)
        
        self.console.print(table)
        self.console.print("[dim]Enter 'exit' or 'cancel' to cancel selection.[/dim]")
        
        # Check if in a real terminal
        is_terminal = sys.stdin.isatty()
        
        while True:
            if is_terminal:
                choice = Prompt.ask(f"[cyan]{prompt}[/cyan]", default="", console=self.console)
            else:
                self.console.print(f"{prompt}: ", end="")
                choice = input().strip()
            
            # Check cancel command
            if choice.lower() in ("exit", "quit", "q", "cancel"):
                self.console.print("[yellow]Selection cancelled.[/yellow]")
                return []
            
            if not choice:
                self.console.print("[red]Please enter option number(s).[/red]")
                continue
            
            try:
                # Parse input numbers (supports comma-separated)
                selected_indices = []
                for part in choice.split(','):
                    part = part.strip()
                    if not part:
                        continue
                    idx = int(part) - 1  # Convert to 0-based index
                    if 0 <= idx < len(options):
                        selected_indices.append(idx)
                    else:
                        self.console.print(f"[red]Invalid option number: {part}. Please try again.[/red]")
                        selected_indices = None
                        break
                
                if selected_indices is not None:
                    if selected_indices:
                        # Display selected options
                        selected_options = [options[i] for i in selected_indices]
                        self.console.print(f"[green]Selected {len(selected_indices)} item(s):[/green]")
                        for idx in selected_indices:
                            self.console.print(f"  [green]✓[/green] {options[idx]}")
                        return selected_indices
                    else:
                        self.console.print("[red]Please select at least one option.[/red]")
            except ValueError:
                self.console.print("[red]Please enter valid number(s) (comma-separated).[/red]")
    
    def text_input(self, prompt: str = "Please enter", default: str = "", placeholder: Optional[str] = None) -> Optional[str]:
        """
        Get user text input.
        
        Args:
            prompt: Prompt text
            default: Default value
            placeholder: Placeholder text (for display hint)
            
        Returns:
            User input text, returns None if cancelled
        """
        # Check if in a real terminal and prompt_toolkit is available
        is_terminal = sys.stdin.isatty()
        
        if not is_terminal or not PROMPT_TOOLKIT_AVAILABLE:
            # Non-terminal environment or prompt_toolkit not available, use simple input wrapped in blue Panel
            try:
                # Build prompt content
                panel_content = prompt
                if placeholder:
                    panel_content = f"{prompt}\n[dim]{placeholder}[/dim]"
                
                # Display blue-bordered Panel
                input_panel = Panel(
                    panel_content,
                    title="[bold cyan]📝 Text Input[/bold cyan]",
                    title_align="left",
                    border_style="cyan",
                    padding=(1, 2)
                )
                self.console.print(input_panel)
                self.console.print()
                
                # Get user input
                user_input = input().strip() or default
                return user_input.strip() if user_input else None
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Input cancelled.[/yellow]")
                return None
        
        # Use interactive input box interface, first display blue Panel hint
        # Build prompt content
        panel_content = prompt
        if placeholder:
            panel_content = f"{prompt}\n[dim]{placeholder}[/dim]"
        
        # Display blue-bordered Panel
        input_panel = Panel(
            panel_content,
            title="[bold cyan]📝 Text Input[/bold cyan]",
            title_align="left",
            border_style="cyan",
            padding=(1, 2)
        )
        self.console.print(input_panel)
        self.console.print()
        
        # Call interactive input
        return self._text_input_interactive(prompt, default, placeholder)
    
    def _text_input_interactive(self, prompt: str, default: str, placeholder: Optional[str]) -> Optional[str]:
        """Get text input using interactive input box"""
        # State management
        state = {
            'value': default,
            'editing': True,
            'result': None
        }
        
        # Calculate text display width (Chinese characters take 2 widths)
        def get_display_width(text):
            """Calculate text display width in terminal"""
            width = 0
            for char in text:
                if ord(char) > 127:
                    width += 2
                else:
                    width += 1
            return width
        
        # Generate formatted text
        def get_formatted_text():
            fragments = []
            
            # Display prompt
            fragments.append(("class:input-title", f"{prompt}\n"))
            fragments.append(("", "\n"))
            
            # Search box style - rounded border, light purple
            box_width = 60
            
            # Top border - rounded
            fragments.append(("class:search-box", "╭"))
            fragments.append(("class:search-box", "─" * (box_width - 2)))
            fragments.append(("class:search-box", "╮\n"))
            
            # Middle row - contains icon, input content and cursor
            fragments.append(("class:search-box", "│"))
            
            # Magnifying glass icon
            icon_text = ""
            icon_display_width = 4  # 1 space + 2(emoji) + 1 space
            fragments.append(("class:search-icon", icon_text))
            
            # Input content or placeholder
            display_text = state['value'] if state['value'] else (placeholder or '')
            text_display_width = get_display_width(display_text)
            
            if state['value']:
                fragments.append(("class:input-text", display_text))
            else:
                fragments.append(("class:input-placeholder", display_text))
            
            # Cursor (if editing)
            cursor_width = 0
            if state['editing']:
                cursor_text = "▊"
                cursor_width = 1
                fragments.append(("class:input-cursor", cursor_text))
            
            # Fill remaining space
            used_width = 1 + icon_display_width + text_display_width + cursor_width
            remaining = box_width - used_width - 1  # Subtract right border
            if remaining > 0:
                fragments.append(("class:search-box", " " * remaining))
            
            fragments.append(("class:search-box", "│\n"))
            
            # Bottom border - rounded
            fragments.append(("class:search-box", "╰"))
            fragments.append(("class:search-box", "─" * (box_width - 2)))
            fragments.append(("class:search-box", "╯\n"))
            
            fragments.append(("", "\n"))
            fragments.append(("class:footer", "Press Enter to confirm after entering text · Esc to cancel"))
            
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
        
        # Create keyboard bindings
        kb = KeyBindings()
        
        # Handle character input
        @kb.add('<any>')
        def handle_any_key(event):
            """Handle any key input"""
            if not state['editing']:
                return
            
            try:
                key = event.key_sequence[0].key if event.key_sequence else None
                if key:
                    # Skip special keys
                    if key in ('up', 'down', 'left', 'right', 'escape', 'c-c', 'tab', 'enter', 'backspace'):
                        return
                    
                    if len(key) == 1 and key.isprintable():
                        state['value'] = state['value'] + key
                        event.app.invalidate()
            except Exception:
                pass
        
        # Handle backspace
        @kb.add('backspace')
        def handle_backspace(event):
            """Handle backspace key"""
            if state['editing'] and state['value']:
                state['value'] = state['value'][:-1]
                event.app.invalidate()
        
        # Handle Enter confirmation
        @kb.add('enter')
        def handle_enter(event):
            """Handle Enter confirmation"""
            state['editing'] = False
            state['result'] = state['value'].strip()
            event.app.exit()
        
        # Handle Esc cancellation
        @kb.add('escape')
        def handle_escape(event):
            """Handle Esc cancellation"""
            state['editing'] = False
            state['result'] = None
            event.app.exit()
        
        # Create control
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
        
        # Define styles
        style_list = [
            ("input-title", "bold #ffffff"),
            ("search-box", "#9d4edd"),  # Light purple border
            ("search-icon", "#9d4edd"),  # Magnifying glass icon
            ("input-text", "#ffffff"),  # Input text color
            ("input-placeholder", "#888888"),  # Placeholder color
            ("input-cursor", "#9d4edd"),  # Cursor color
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
        
        # Create layout
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # Create application
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
    
    def submit(self, message: str = "Please confirm", default: bool = True) -> bool:
        """
        Get user confirmation/submission.
        
        Args:
            message: Confirmation message
            default: Default choice (True for confirm, False for cancel)
            
        Returns:
            True means confirm/submit, False means cancel
        """
        # Check if in a real terminal
        is_terminal = sys.stdin.isatty()
        
        try:
            if is_terminal:
                confirmed = Confirm.ask(f"[cyan]{message}[/cyan]", default=default, console=self.console)
            else:
                # Non-terminal environment, use simple input
                self.console.print(f"{message} (y/n) [{'Y/n' if default else 'y/N'}]: ", end="")
                response = input().strip().lower()
                if not response:
                    confirmed = default
                else:
                    confirmed = response in ('y', 'yes', 'true', '1')
            
            return confirmed
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Operation cancelled.[/yellow]")
            return False
    
    def composite_menu(self, tabs: List[Dict[str, Any]], title: str = "Composite Menu") -> Dict[str, Any]:
        """
        Generate composite menu, supports multiple tabs, each tab can be multi-select, text input, or submit.
        
        Args:
            tabs: Tab configuration list, each tab is a dictionary containing the following fields:
                - type: Tab type, optional values:
                    - 'multi_select': Multi-select
                    - 'text_input': Text input
                    - 'submit': Submit/confirm
                - name: Tab name (for identification and display)
                - title: Tab title (displayed to user)
                - For 'multi_select' type, also need:
                    - options: Option list (same format as select_multiple)
                    - prompt: Prompt text (optional)
                - For 'text_input' type, also need:
                    - prompt: Prompt text
                    - default: Default value (optional)
                    - placeholder: Placeholder (optional)
                - For 'submit' type, also need:
                    - message: Confirmation message
                    - default: Default choice (optional, default True)
            title: Overall title
            
        Returns:
            Dictionary containing answers for each tab:
                - For 'multi_select': Returns selected index list
                - For 'text_input': Returns input text
                - For 'submit': Returns boolean (True/False)
            key is tab's name, value is corresponding answer
            Returns None if user cancels
        """
        if not tabs:
            self.console.print("[red]No tabs configured.[/red]")
            return {}
        
        # Check if in a real terminal and prompt_toolkit is available
        is_terminal = sys.stdin.isatty()
        
        # If prompt_toolkit is available and in terminal, use interactive interface
        if PROMPT_TOOLKIT_AVAILABLE and is_terminal:
            logger.info("[red]composite menu interactive tab.[/red]")
            return self._composite_menu_interactive(tabs, title)
        else:
            # Fallback to sequential execution mode
            self.console.print("[red]Sequential execution mode tab.[/red]")
            return self._composite_menu_sequential(tabs, title)
    
    def _composite_menu_interactive(self, tabs: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
        """
        Interactive composite menu implemented using prompt_toolkit.
        All tabs (including text_input, multi_select, submit) are handled in the interactive interface.
        """
        results = {}
        current_tab_index = 0
        
        # State management
        state = {
            'current_tab_index': 0,
            'results': {},
            'tab_states': {}  # Store state for each tab (e.g., selected items for multi-select)
        }
        
        # All tabs are handled in interactive interface
        state['current_tab_index'] = 0  # Start from first tab
        state['results'] = results
        state['all_tabs'] = tabs  # Store all tabs
        
        # Initialize first tab state
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
            """Generate formatted text content"""
            fragments = []
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return FormattedText([("", "")])
            
            # Current tab index (index in all tabs)
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
            
            # Top navigation bar - display all tabs
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
            
            # If tab is completed and is text_input, show review mode
            if is_completed and tab_type == 'text_input':
                # Display question and answer
                question = current_tab.get('prompt', tab_title)
                answer = state['results'].get(tab_name, '')
                
                fragments.append(("class:normal", "• "))
                fragments.append(("class:normal-label", f"{question}\n"))
                fragments.append(("class:nav-completed", "  → "))
                fragments.append(("class:normal-desc", f"{answer}\n\n"))
                
                fragments.append(("class:footer", "←→ Switch Tab · Esc to cancel"))
                # Return directly, don't execute subsequent tab type display logic
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
                # Main title
                fragments.append(("class:title", f"{tab_title}\n\n"))
            
            # Display different content based on tab type
            if tab_type == 'multi_select':
                options = current_tab.get('options', [])
                # Parse options
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
                
                # Use shared render function
                self._render_multi_select_options(
                    parsed_options, 
                    selected_indices, 
                    current_index, 
                    fragments
                )
                
                fragments.append(("", "\n"))
                selected_count = len(selected_indices)
                if selected_count > 0:
                    fragments.append(("class:footer", f"Selected {selected_count} item(s) · "))
                fragments.append(("class:footer", "Enter to select · ↑↓ Navigate · ←→ Switch Tab · Esc to cancel"))
                
            elif tab_type == 'text_input':
                prompt = current_tab.get('prompt', 'Please enter')
                default = current_tab.get('default', ' ')
                placeholder = current_tab.get('placeholder', 'Search...')
                
                tab_name = current_tab.get('name')
                current_value = state['tab_states'].get(f'{tab_name}_value', default)
                is_editing = state['tab_states'].get(f'{tab_name}_editing', False)
                
                # Search box style - rounded border, light purple (similar to style in image)
                box_width = 60
                
                # Calculate text display width (Chinese characters take 2 widths)
                def get_display_width(text):
                    """Calculate text display width in terminal"""
                    width = 0
                    for char in text:
                        # Check if Chinese character or full-width character
                        if ord(char) > 127:
                            width += 2
                        else:
                            width += 1
                    return width
                
                # Top border - rounded
                fragments.append(("class:search-box", "╭"))
                fragments.append(("class:search-box", "─" * (box_width - 2)))
                fragments.append(("class:search-box", "╮\n"))
                
                # Middle row - contains icon, input content and cursor
                fragments.append(("class:search-box", "│"))
                
                # Magnifying glass icon (takes 3 character widths: space+emoji+space, emoji may take 2 display widths)
                icon_text = ""
                icon_display_width = 4  # 1 space + 2(emoji) + 1 space
                fragments.append(("class:search-icon", icon_text))
                
                # Input content or placeholder
                display_text = current_value if current_value else placeholder
                text_display_width = get_display_width(display_text)
                
                if current_value:
                    fragments.append(("class:input-text", display_text))
                else:
                    fragments.append(("class:input-placeholder", display_text))
                
                # Cursor (if editing)
                cursor_text = ""
                cursor_width = 0
                if is_editing:
                    cursor_text = "▊"
                    cursor_width = 1
                    fragments.append(("class:input-cursor", cursor_text))
                
                # Fill remaining space
                # Used width: left border(1) + icon(4) + text width + cursor width
                used_width = 1 + icon_display_width + text_display_width + cursor_width
                remaining = box_width - used_width - 1  # Subtract right border
                if remaining > 0:
                    fragments.append(("class:search-box", " " * remaining))
                
                fragments.append(("class:search-box", "│\n"))
                
                # Bottom border - rounded
                fragments.append(("class:search-box", "╰"))
                fragments.append(("class:search-box", "─" * (box_width - 2)))
                fragments.append(("class:search-box", "╯\n"))
                
                fragments.append(("", "\n"))
                fragments.append(("class:footer", "Enter to confirm · ←→ Switch Tab · Esc to cancel"))
                
            elif tab_type == 'submit':
                message = current_tab.get('message', 'Ready to submit your answers?')
                default = current_tab.get('default', True)
                
                fragments.append(("class:submit-message", f"{message}\n\n"))
                
                # Display all answered questions and answers
                all_tabs = state.get('all_tabs', [])
                for tab in all_tabs:
                    tab_name = tab.get('name')
                    if tab_name in state['results']:
                        question = tab.get('prompt', tab.get('title', tab_name))
                        answer = state['results'][tab_name]
                        
                        # Format answer
                        if isinstance(answer, list):
                            # Multi-select result
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
                
                # Display options
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
                fragments.append(("class:footer", "Enter to select · ↑↓ Navigate · ←→ Switch Tab · Esc to cancel"))
            
            # Ensure FormattedText object is returned
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
        
        # Create keyboard bindings
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
            """Handle Enter key: toggle selection for multi-select, confirm for other cases"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            
            # If multi-select type, Enter toggles selection
            if tab_type == 'multi_select':
                toggle_selection(event)
            else:
                # Other types, Enter confirms
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
                        # All tabs completed, exit
                        event.app.exit()
                else:
                    # At least one must be selected
                    pass
            elif tab_type == 'text_input':
                # End editing mode
                state['tab_states'][f'{tab_name}_editing'] = False
                current_value = state['tab_states'].get(f'{tab_name}_value', '')
                
                if current_value.strip() or current_tab.get('allow_empty', False):
                    state['results'][tab_name] = current_value.strip()
                    # Move to next tab
                    if current_tab_idx < len(all_tabs) - 1:
                        state['current_tab_index'] = current_tab_idx + 1
                        # Reset next tab state
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
                        # All tabs completed, exit
                        event.app.exit()
                else:
                    # If empty and empty values not allowed, re-enter editing mode
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
            """Move to previous tab (in all tabs)"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                current_tab_idx = len(all_tabs) - 1
            
            current_tab = all_tabs[current_tab_idx]
            tab_type = current_tab.get('type')
            tab_name = current_tab.get('name')
            
            # Save current tab state (if interactive tab)
            if tab_type == 'multi_select':
                selected_indices = state['tab_states'].get(tab_name, set())
                if selected_indices:
                    state['results'][tab_name] = sorted(list(selected_indices))
            elif tab_type == 'text_input':
                # Save text input value
                current_value = state['tab_states'].get(f'{tab_name}_value', '')
                if current_value:
                    state['results'][tab_name] = current_value
                # Exit editing mode
                state['tab_states'][f'{tab_name}_editing'] = False
            
            # Switch to previous tab
            if current_tab_idx > 0:
                state['current_tab_index'] = current_tab_idx - 1
                # Initialize previous tab state (if interactive tab)
                prev_tab = all_tabs[state['current_tab_index']]
                prev_tab_name = prev_tab.get('name')
                prev_tab_type = prev_tab.get('type')
                
                if prev_tab_type == 'multi_select':
                    if f'{prev_tab_name}_current' not in state['tab_states']:
                        state['tab_states'][f'{prev_tab_name}_current'] = 0
                    if prev_tab_name not in state['tab_states']:
                        # If previous result exists, restore selected state
                        if prev_tab_name in state['results']:
                            state['tab_states'][prev_tab_name] = set(state['results'][prev_tab_name])
                        else:
                            state['tab_states'][prev_tab_name] = set()
                elif prev_tab_type == 'text_input':
                    # Restore text input value (if previously entered)
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
            """Move to next tab (in all tabs)"""
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
            """Cancel operation"""
            event.app.exit(result=None)
        
        # Bind keys
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
            """Handle backspace key (for text input)"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_name = current_tab.get('name')
            is_completed = tab_name in state['results']
            
            # If completed, editing not allowed
            if is_completed:
                return
            
            if current_tab.get('type') == 'text_input':
                if state['tab_states'].get(f'{tab_name}_editing', False):
                    current_value = state['tab_states'].get(f'{tab_name}_value', '')
                    if current_value:
                        state['tab_states'][f'{tab_name}_value'] = current_value[:-1]
                        event.app.invalidate()
        
        kb.add("backspace")(handle_backspace)
        
        # Bind character input for text input (use <any> but need to check tab type)
        @kb.add('<any>')
        def handle_any_key(event):
            """Handle any key input (for text input)"""
            all_tabs = state.get('all_tabs', [])
            if not all_tabs:
                return
            
            current_tab_idx = state['current_tab_index']
            if current_tab_idx >= len(all_tabs):
                return
            
            current_tab = all_tabs[current_tab_idx]
            tab_name = current_tab.get('name')
            is_completed = tab_name in state['results']
            
            # If completed, editing not allowed
            if is_completed:
                return
            
            if current_tab.get('type') == 'text_input':
                if not state['tab_states'].get(f'{tab_name}_editing', False):
                    # If not started editing yet, start editing first
                    state['tab_states'][f'{tab_name}_editing'] = True
                
                # Handle character input
                try:
                    key = event.key_sequence[0].key if event.key_sequence else None
                    if key:
                        # Skip special keys
                        if key in ('up', 'down', 'left', 'right', 'escape', 'c-c', 'tab', 'enter', 'backspace'):
                            return
                        
                        if len(key) == 1 and key.isprintable():
                            current_value = state['tab_states'].get(f'{tab_name}_value', '')
                            state['tab_states'][f'{tab_name}_value'] = current_value + key
                            event.app.invalidate()
                except Exception:
                    pass
        
        # Create control
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
        
        # Define styles
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
            ("search-box", "#9d4edd"),  # Light purple border
            ("search-icon", "#9d4edd"),  # Magnifying glass icon
            ("input-text", "#ffffff"),  # Input text color
            ("input-placeholder", "#888888"),  # Placeholder color
            ("input-cursor", "#9d4edd"),  # Cursor color
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
        
        # Create layout
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # Create application
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
            
            # All tabs processed in interactive interface, return results directly
            return state['results'] if state['results'] else None
        except KeyboardInterrupt:
            return None
    
    def _composite_menu_sequential(self, tabs: List[Dict[str, Any]], title: str) -> Dict[str, Any]:
        """
        Sequential execution mode (non-interactive, for non-terminal environments).
        """
        results = {}
        
        self.console.print(f"[bold]{title}[/bold]\n")
        
        for idx, tab in enumerate(tabs):
            tab_type = tab.get('type')
            tab_name = tab.get('name', f'tab_{idx}')
            tab_title = tab.get('title', tab_name)
            
            self.console.print(f"\n[cyan]Step {idx + 1}/{len(tabs)}: {tab_title}[/cyan]")
            
            if tab_type == 'multi_select':
                options = tab.get('options', [])
                prompt = tab.get('prompt', 'Select (Multiple)')
                selected_indices = self.select_multiple(options, tab_title, prompt)
                results[tab_name] = selected_indices
                
            elif tab_type == 'text_input':
                prompt = tab.get('prompt', 'Please enter')
                default = tab.get('default', '')
                placeholder = tab.get('placeholder')
                user_input = self.text_input(prompt, default, placeholder)
                if user_input is None:
                    return None  # User cancelled
                results[tab_name] = user_input
                
            elif tab_type == 'submit':
                message = tab.get('message', 'Please confirm')
                default = tab.get('default', True)
                confirmed = self.submit(message, default)
                results[tab_name] = confirmed
                if not confirmed:
                    return None  # User cancelled
        
        return results

    def single_select(self, options: List, title: str = "Please select", warning: Optional[str] = None, question: Optional[str] = None, nav_items: Optional[List[Dict[str, Any]]] = None) -> Optional[int]:
        """
        Display single-select list with selection support, return selected option index.
        Supports keyboard navigation with up/down arrows and Enter to select.
        Modern interface design with support for warning messages and navigation bar.
        
        Args:
            options: Option list, supports the following formats:
                - List[str]: Simple string list, e.g., ["Option 1", "Option 2"]
                - List[dict]: Dictionary list, each dict contains 'label' and 'description' keys
                  e.g., [{"label": "Option 1", "description": "Description of option 1"}, ...]
                - List[tuple]: Tuple list, each tuple contains (label, description)
                  e.g., [("Option 1", "Description of option 1"), ...]
            title: Title
            warning: Optional warning message (will display yellow warning icon)
            question: Optional question text (displayed below warning)
            nav_items: Optional navigation bar item list, each item contains:
                - 'label': Label text
                - 'checked': Whether selected (displays checkbox)
                - 'type': Type ('checkbox' or 'button')
                - 'highlight': Whether highlighted (for Submit button, etc.)
            
        Returns:
            Selected option index (0-based), returns None if cancelled
        """
        if not options:
            self.console.print("[red]No options available.[/red]")
            return None
        
        # Check if in a real terminal and prompt_toolkit is available
        is_terminal = sys.stdin.isatty()
        
        # If prompt_toolkit is available and in terminal, use interactive interface
        if PROMPT_TOOLKIT_AVAILABLE and is_terminal:
            return self._single_select_interactive(options, title, warning, question, nav_items)
        else:
            # Fallback to simple text input method
            return self._single_select_text_input(options, title)
    
    def _single_select_interactive(self, options: List, title: str, warning: Optional[str], question: Optional[str], nav_items: Optional[List[Dict[str, Any]]]) -> Optional[int]:
        """
        Interactive single-select list implemented using prompt_toolkit.
        Supports up/down arrow navigation, Enter to select.
        Modern interface design, similar to the style in the image.
        """
        # Parse options: supports string or dict format
        def parse_option(opt):
            """Parse option, supports string or dict format"""
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
        
        # Create control - 使用可调用对象来动态更新
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
        
        # Create layout
        window = Window(content=control, wrap_lines=False)
        layout = Layout(window)
        
        # Create application
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
                choice = Prompt.ask(f"[cyan]Please select option number[/cyan]", default="", console=self.console)
            else:
                self.console.print("Please select option number: ", end="")
                choice = input().strip()
            
            # Check cancel command
            if choice.lower() in ("exit", "quit", "q", "cancel"):
                self.console.print("[yellow]Selection cancelled.[/yellow]")
                return None
            
            if not choice:
                self.console.print("[red]Please enter option number.[/red]")
                continue
            
            try:
                idx = int(choice) - 1  # Convert to 0-based index
                if 0 <= idx < len(options):
                    self.console.print(f"[green]Selected: {options[idx]}[/green]")
                    return idx
                else:
                    self.console.print(f"[red]Invalid option number: {choice}. Please try again.[/red]")
            except ValueError:
                self.console.print("[red]Please enter a valid number.[/red]")
        