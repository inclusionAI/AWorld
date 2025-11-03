from enum import Enum, auto


class ToolPlacement(Enum):
    """
    Where to inject the tool catalogue in the rendered prompt.
    """
    SYSTEM = auto()        # inside the system message
    FIRST_USER = auto()    # as an extra first-user turn
    LAST_USER = auto()     # appended to the last user turn
    SEPARATE = auto()      # its own dedicated turn / role


class Role(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    ASSISTANT_PREFIX = "assistant_prefix"
