from typing import List, Dict, Tuple
import json
from typing import Callable, Any  # Added for content processor typing
from abc import ABC, abstractmethod
import dataclasses

from .constants import ToolPlacement

# Convert ToolFormatter into an abstract base class
class ToolFormatter(ABC):
    """
    Strategy that converts an in-memory list[dict] describing tools
    into the textual representation expected by the target model.
    """
    @abstractmethod
    def format(self, tools: List[Dict]) -> str:
        """Format a list of tool dictionaries into a string representation."""
        raise NotImplementedError

    @abstractmethod
    def jinja(self) -> str:
        """Return a Jinja template that can be used to format the tools."""
        raise NotImplementedError


class ToolContentProcessor(ABC):
    """
    Strategy that processes the content of a tool before it is serialized.
    """
    @abstractmethod
    def __call__(self, tool: Dict) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def jinja(self) -> str:
        """Return a Jinja template that can be used to process the content of a tool."""
        raise NotImplementedError


class ToolMainContentProcessor(ToolContentProcessor):
    """
    Strategy that processes the main content of a tool before it is serialized.
    """
    def __call__(self, tool: Dict) -> Dict:
        assert isinstance(tool, dict), "Tool must be a dictionary"
        if "function" in tool:
            content = tool["function"]
            assert "name" in content, "Tool function must have a name"
            assert "parameters" in content, "Tool function must have parameters"
            return content
        elif "name" in tool and "parameters" in tool:
            return tool
        else:
            raise ValueError(f"Tool must have a function or name and parameters: {tool}")

    # The main-content extraction cannot be replicated in pure Jinja, so we
    # fall back to the identity behaviour at template-generation time.  This
    # means the processor is *ignored* in frozen chat-templates; users who
    # require it must rely on the Python render path.

    def jinja(self) -> str:
        #  We deliberately document the limitation by returning a simple pass-
        #  through expression.
        return "{{ tool }}"

# Make JsonFormatter inherit the ToolFormatter base class
class JsonFormatter(ToolFormatter):
    """General JSON formatter with configurable indent, separators, and joiner."""

    def __init__(
        self,
        *,
        indent: int | None = None,
        separators: Tuple[str, str] | None = None,
        joiner: str = "\n",
        format_as_list: bool = False,
        content_processor: ToolContentProcessor = None,
    ):
        """Create a new JsonFormatter.

        Args:
            indent: Indentation level passed to ``json.dumps``. ``None`` means no pretty-print.
            separators: Custom separators passed to ``json.dumps``; useful for minification.
            joiner: String used to join per-tool JSON strings when ``format_as_list`` is *False*.
            format_as_list: If *True*, the entire ``tools`` list is serialised in a single
                ``json.dumps`` call, ignoring ``joiner``. This is handy when the target
                model expects a single JSON array instead of multiple individual objects.
            content_processor: Optional callable applied to each individual tool dictionary
                before serialisation. Defaults to the identity function.
        """
        self.indent = indent
        self.separators = separators
        self.joiner = joiner
        self.format_as_list = format_as_list

    def format(self, tools: List[Dict]) -> str:  # noqa: D401
        """Return a single string obtained by dumping every tool to JSON then joining them.

        Args:
            tools: A list of tool dictionaries to be stringified.

        Returns:
            A string representation of the tools, formatted according to the
            given ``indent``/``separators`` and concatenated with ``joiner``.
        """
        # Apply the per-tool content processor first

        if self.format_as_list:
            # Serialize the whole list in one go – joiner is irrelevant in this mode.
            return json.dumps(tools, indent=self.indent, separators=self.separators)

        # Default behaviour: dump each tool individually then concatenate.
        return self.joiner.join(
            json.dumps(t, indent=self.indent, separators=self.separators) for t in tools
        )

    # ------------------------------------------------------------------
    # Jinja support
    # ------------------------------------------------------------------

    def _escape_joiner(self, joiner: str) -> str:  # local helper
        """Return *joiner* escaped so it is safe inside a single‐quoted Jinja
        string literal (the HF chat-template parser understands the Python
        backslash escapes)."""

        return joiner.replace("\\", "\\\\").replace("'", "\\'")

    def jinja(self) -> str:  # noqa: D401
        """Return a **Jinja-mini** snippet that serialises the *tools* variable
        with the same settings as :py:meth:`format`.

        The template assumes that a ``tools`` list is present in the Jinja
        context.  Because the Hugging-Face chat-template dialect only supports
        a limited subset of Jinja, we restrict ourselves to `map`, `tojson`,
        `join`, and optional indent on a *single* tojson call when
        ``format_as_list`` is *True*.
        
        When ``format_as_list`` is *False* and ``indent`` is specified, we use
        a Jinja loop to apply indentation to each individual tool.
        """

        # Serialise whole list -> one tojson call (supports indent argument)
        if self.format_as_list:
            if self.indent is None:
                return "{{ tools | tojson }}"
            else:
                return f"{{{{ tools | tojson(indent={self.indent}) }}}}"

        # Individual objects: use loop if indent is needed, otherwise use map
        if self.indent is not None:
            # Use loop to apply indentation to each individual tool
            # For joiners containing newlines, we need to avoid whitespace control to preserve them
            # For other joiners, we can use whitespace control for cleaner output
            
            if '\n' in self.joiner:
                # Joiner contains newlines - use Jinja's string replacement to convert \n to actual newlines
                # We'll create a Jinja variable with the proper newlines
                joiner_var = '{% set joiner = "' + self.joiner.replace('\n', '\\n') + '" | replace("\\\\n", "\n") %}'
                return joiner_var + f"{{% for tool in tools %}}{{{{ tool | tojson(indent={self.indent}) }}}}{{% if not loop.last %}}{{{{ joiner }}}}{{% endif %}}{{% endfor %}}"
            else:
                # Joiner doesn't contain newlines - safe to use whitespace control and escaping
                joiner_escaped = self._escape_joiner(self.joiner)
                return f"{{%- for tool in tools -%}}{{{{ tool | tojson(indent={self.indent}) }}}}{{%- if not loop.last -%}}{joiner_escaped}{{%- endif -%}}{{%- endfor -%}}"
        else:
            # No indentation needed, use the simpler map approach
            joiner_escaped = self._escape_joiner(self.joiner)
            return (
                "{{ tools | map('tojson') | join('" + joiner_escaped + "') }}"
            )

class JsonMinifiedFormatter(JsonFormatter):
    """Single-line JSON objects without extra whitespace (legacy alias)."""

    def __init__(self, joiner: str = "\n", *, content_processor: Callable[[Dict], Any] | None = None):
        super().__init__(indent=None, separators=(",", ":"), joiner=joiner, content_processor=content_processor)


class JsonIndentedFormatter(JsonFormatter):
    """
    Pretty printed JSON with configurable indent (default 4).
    Frequently required by models like Mistral-v0.3.
    (legacy alias)
    """

    def __init__(self, indent: int = 4, *, joiner: str = "\n\n", format_as_list: bool = False):
        super().__init__(indent=indent, separators=None, joiner=joiner, format_as_list=format_as_list)


class JsonCompactFormatter(JsonFormatter):
    """Single-line JSON objects without extra whitespace."""
    def __init__(self, *, format_as_list: bool = True, content_processor: Callable[[Dict], Any] | None = None):
        super().__init__(indent=None, separators=None, format_as_list=format_as_list, content_processor=content_processor)

class JsonQwenFormatter(JsonFormatter):
    """
    JSON formatter for Qwen models.
    """
    def __init__(self):
        super().__init__(indent=None, separators=None, format_as_list=False, content_processor=None)

    # No special behaviour – inherits .jinja from JsonFormatter


# ---------------------------------------------------------------------------
# Content processors – only implement jinja where feasible
# ---------------------------------------------------------------------------


try:
    import yaml as _yaml  # optional dependency

    class YamlFormatter(ToolFormatter):  # type: ignore
        def format(self, tools: List[Dict]) -> str:  # noqa: D401
            return _yaml.safe_dump(tools, sort_keys=False)
except ModuleNotFoundError:  # pragma: no cover
    YamlFormatter = None  # type: ignore


@dataclasses.dataclass
class ToolPolicy:
    """
    Encapsulates every configuration decision about how *tools*
    appear in the prompt for a given template.
    """
    placement: "ToolPlacement" = ToolPlacement.SYSTEM
    content_processor: Callable[[Dict], Any] = None
    formatter: ToolFormatter = dataclasses.field(default_factory=lambda: JsonQwenFormatter())

    def format_tools(self, tools: List[Dict]) -> str:
        """
        Convert `tools` into ready-to-inject text according to the chosen formatter.
        """
        if self.content_processor is not None:
            processed_tools = [self.content_processor(t) for t in tools]
        else:
            processed_tools = tools
        return self.formatter.format(processed_tools)
