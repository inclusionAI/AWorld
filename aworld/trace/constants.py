from enum import Enum

ATTRIBUTES_NAMESPACE = 'aworld'
"""Namespace within OTEL attributes used by aworld."""

ATTRIBUTES_MESSAGE_KEY = f'{ATTRIBUTES_NAMESPACE}.msg'
"""The formatted message for a log."""

ATTRIBUTES_MESSAGE_TEMPLATE_KEY = f'{ATTRIBUTES_NAMESPACE}.msg_template'
"""The template for a log message."""

ATTRIBUTES_MESSAGE_RUN_TYPE_KEY = f'{ATTRIBUTES_NAMESPACE}.run_type'
"""The template for a log message."""

MESSAGE_FORMATTED_VALUE_LENGTH_LIMIT = 128
"""Maximum number of characters for formatted values in a trace message."""


class RunType(Enum):
    '''Span run type supported in the framework
    '''
    AGNET = "AGENT"
    TOOL = "TOOL"
    MCP = "MCP"
    LLM = "LLM"
    OTHER = "OTHER"
