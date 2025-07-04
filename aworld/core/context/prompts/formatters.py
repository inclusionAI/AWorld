# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Template formatters for prompt templates."""

import re
import string
from enum import Enum
from typing import Any, Dict, List, Mapping, Set

try:
    from jinja2 import Environment, meta
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False

class TemplateFormat(str, Enum):
    """Template format enumeration."""
    F_STRING = "f-string"
    JINJA2 = "jinja2"
    STRING_TEMPLATE = "string"


def format_template(template: str, template_format: TemplateFormat, **kwargs: Any) -> str:
    if template_format == TemplateFormat.F_STRING:
        return _format_f_string(template, **kwargs)
    elif template_format == TemplateFormat.JINJA2:
        return _format_jinja2(template, **kwargs)
    elif template_format == TemplateFormat.STRING_TEMPLATE:
        return _format_string_template(template, **kwargs)
    else:
        raise ValueError(f"Unsupported template format: {template_format}")


def get_template_variables(template: str, template_format: TemplateFormat) -> List[str]:
    if not template:
        return []
    if template_format == TemplateFormat.F_STRING:
        return _get_f_string_variables(template)
    elif template_format == TemplateFormat.JINJA2:
        return _get_jinja2_variables(template)
    elif template_format == TemplateFormat.STRING_TEMPLATE:
        return _get_string_template_variables(template)
    else:
        raise ValueError(f"Unsupported template format: {template_format}")


def _format_f_string(template: str, **kwargs: Any) -> str:
    """Format using Python f-string style."""
    try:
        return template.format(**kwargs)
    except KeyError as e:
        raise ValueError(f"Missing variable in template: {e}")
    except Exception as e:
        raise ValueError(f"Error formatting template: {e}")


def _format_jinja2(template: str, **kwargs: Any) -> str:
    """Format using Jinja2 template engine."""
    if not JINJA2_AVAILABLE:
        raise ImportError("Jinja2 is required for jinja2 template format")
    
    try:
        env = Environment()
        template_obj = env.from_string(template)
        return template_obj.render(**kwargs)
    except Exception as e:
        raise ValueError(f"Error formatting Jinja2 template: {e}")


def _format_string_template(template: str, **kwargs: Any) -> str:
    """Format using Python string.Template style ($variable)."""
    try:
        template_obj = string.Template(template)
        return template_obj.substitute(**kwargs)
    except KeyError as e:
        raise ValueError(f"Missing variable in template: {e}")
    except Exception as e:
        raise ValueError(f"Error formatting template: {e}")


def _get_f_string_variables(template: str) -> List[str]:
    """Extract variables from f-string style template."""
    # Find all {variable} patterns, excluding escaped {{ and }}
    pattern = r'(?<!\{)\{([^{}]+)\}(?!\})'
    matches = re.findall(pattern, template)
    
    variables = []
    for match in matches:
        # Handle format specifiers like {name:>10} -> name
        var_name = match.split(':')[0].split('!')[0].strip()
        if var_name:
            variables.append(var_name)
    
    return list(set(variables))  # Remove duplicates


def _get_jinja2_variables(template: str) -> List[str]:
    """Extract variables from Jinja2 template."""
    if not JINJA2_AVAILABLE:
        raise ImportError("Jinja2 is required for jinja2 template format")
    
    try:
        env = Environment()
        ast = env.parse(template)
        variables = meta.find_undeclared_variables(ast)
        return list(variables)
    except Exception as e:
        raise ValueError(f"Error parsing Jinja2 template: {e}")


def _get_string_template_variables(template: str) -> List[str]:
    """Extract variables from string.Template style template."""
    # Find all $identifier and ${identifier} patterns
    pattern = r'\$(?:([_a-zA-Z][_a-zA-Z0-9]*)|{([_a-zA-Z][_a-zA-Z0-9]*)})'
    matches = re.findall(pattern, template)
    
    variables = []
    for match in matches:
        # match is a tuple, get the non-empty group
        var_name = match[0] or match[1]
        if var_name:
            variables.append(var_name)
    
    return list(set(variables))  # Remove duplicates


def escape_template_string(text: str, template_format: TemplateFormat) -> str:
    """Escape special characters in text to prevent template interpretation.
    
    Args:
        text: Text to escape
        template_format: The template format being used
        
    Returns:
        Escaped text safe for use in templates
    """
    if template_format == TemplateFormat.F_STRING:
        # Escape curly braces by doubling them
        return text.replace('{', '{{').replace('}', '}}')
    elif template_format == TemplateFormat.JINJA2:
        # Escape Jinja2 special characters
        return text.replace('{', '\\{').replace('}', '\\}').replace('%', '\\%')
    elif template_format == TemplateFormat.STRING_TEMPLATE:
        # Escape dollar signs
        return text.replace('$', '$$')
    else:
        return text 