# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Prompt Template Utilities

Utility functions for working with prompt templates.
"""

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Callable

from aworld.core.context.prompts.string_prompt_template import PromptTemplate
from aworld.core.context.prompts.chat_prompt_template import ChatPromptTemplate
from aworld.core.context.prompts.formatters import TemplateFormat


def load_prompt_from_file(file_path: Union[str, Path], 
                         encoding: str = "utf-8") -> PromptTemplate:
    """Load a prompt template from a file.
    
    Supports various file formats:
    - .txt: Plain text template (PromptTemplate)
    - .json: JSON format with template configuration
    - .yaml/.yml: YAML format with template configuration
    
    Args:
        file_path: Path to the template file
        encoding: File encoding
        
    Returns:
        A prompt template instance
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file format is unsupported or invalid
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileNotFoundError(f"Template file not found: {file_path}")
    
    suffix = file_path.suffix.lower()
    
    if suffix == '.txt':
        # Plain text file - create simple PromptTemplate
        return PromptTemplate.from_file(str(file_path), encoding=encoding)
    
    elif suffix == '.json':
        # JSON format
        with open(file_path, 'r', encoding=encoding) as f:
            config = json.load(f)
        return _create_prompt_from_config(config)
    
    elif suffix in ('.yaml', '.yml'):
        # YAML format
        with open(file_path, 'r', encoding=encoding) as f:
            config = yaml.safe_load(f)
        return _create_prompt_from_config(config)
    
    else:
        raise ValueError(f"Unsupported file format: {suffix}")


def _create_prompt_from_config(config: Dict[str, Any]) -> PromptTemplate:
    """Create a prompt template from configuration dictionary.
    
    Expected format:
    {
        "type": "prompt" | "chat",
        "template": "template string" | [list of messages for chat],
        "template_format": "f-string" | "jinja2" | "string",
        "input_variables": [list of variables],
        "partial_variables": {dict of partial variables}
    }
    
    Args:
        config: Configuration dictionary
        
    Returns:
        A prompt template instance
    """
    prompt_type = config.get("type", "prompt")
    template_format = TemplateFormat(config.get("template_format", "f-string"))
    input_variables = config.get("input_variables")
    partial_variables = config.get("partial_variables")
    
    if prompt_type == "prompt":
        template = config["template"]
        return PromptTemplate(
            template=template,
            input_variables=input_variables,
            template_format=template_format,
            partial_variables=partial_variables
        )
    
    elif prompt_type == "chat":
        messages = config["template"]
        return ChatPromptTemplate(
            messages=messages,
            template_format=template_format,
            partial_variables=partial_variables
        )
    
    else:
        raise ValueError(f"Unsupported prompt type: {prompt_type}")


def save_prompt_to_file(prompt: PromptTemplate, 
                       file_path: Union[str, Path],
                       format: str = "json",
                       encoding: str = "utf-8") -> None:
    """Save a prompt template to a file.
    
    Args:
        prompt: The prompt template to save
        file_path: Path where to save the file
        format: Output format ("json" or "yaml")
        encoding: File encoding
        
    Raises:
        ValueError: If the format is unsupported
    """
    file_path = Path(file_path)
    
    # Create configuration dictionary
    config = prompt_to_config(prompt)
    
    if format.lower() == "json":
        with open(file_path, 'w', encoding=encoding) as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    
    elif format.lower() in ("yaml", "yml"):
        with open(file_path, 'w', encoding=encoding) as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    
    else:
        raise ValueError(f"Unsupported output format: {format}")


def prompt_to_config(prompt: PromptTemplate) -> Dict[str, Any]:
    """Convert a prompt template to configuration dictionary.
    
    Args:
        prompt: The prompt template to convert
        
    Returns:
        Configuration dictionary
    """
    config = {
        "type": prompt._prompt_type,
        "template_format": prompt.template_format.value,
        "input_variables": prompt.input_variables,
        "partial_variables": prompt.partial_variables
    }
    
    if isinstance(prompt, PromptTemplate):
        config["template"] = prompt.template
    
    elif isinstance(prompt, ChatPromptTemplate):
        # Convert messages to serializable format
        messages = []
        for msg_template in prompt.messages:
            if hasattr(msg_template, 'role') and hasattr(msg_template, 'prompt'):
                messages.append((msg_template.role, msg_template.prompt.template))
            elif hasattr(msg_template, 'variable_name'):
                # MessagesPlaceholder
                messages.append(("placeholder", f"{{{msg_template.variable_name}}}"))
        config["template"] = messages
    
    return config


def create_few_shot_prompt(examples: List[Dict[str, str]], 
                          example_template: str,
                          suffix: str,
                          input_variables: List[str],
                          example_separator: str = "\n\n",
                          prefix: str = "") -> PromptTemplate:
    """Create a few-shot prompt template from examples.
    
    Args:
        examples: List of example dictionaries
        example_template: Template for formatting each example
        suffix: Template suffix (usually contains the input variables)
        input_variables: Input variables for the final prompt
        example_separator: Separator between examples
        prefix: Optional prefix before examples
        
    Returns:
        A PromptTemplate for few-shot learning
        
    Example:
        >>> examples = [
        ...     {"input": "2+2", "output": "4"},
        ...     {"input": "3+3", "output": "6"}
        ... ]
        >>> prompt = create_few_shot_prompt(
        ...     examples=examples,
        ...     example_template="Input: {input}\nOutput: {output}",
        ...     suffix="Input: {input}\nOutput:",
        ...     input_variables=["input"]
        ... )
    """
    # Format all examples
    formatted_examples = []
    for example in examples:
        formatted_example = example_template.format(**example)
        formatted_examples.append(formatted_example)
    
    # Combine everything
    full_template = ""
    if prefix:
        full_template += prefix + "\n\n"
    
    full_template += example_separator.join(formatted_examples)
    
    if formatted_examples and suffix:
        full_template += example_separator + suffix
    elif suffix:
        full_template += suffix
    
    return PromptTemplate.from_template(full_template)


def chain_prompts(*prompts: PromptTemplate) -> PromptTemplate:
    """Chain multiple prompt templates together.
    
    For PromptTemplate: concatenates the templates
    For ChatPromptTemplate: combines all messages
    
    Args:
        *prompts: Prompt templates to chain
        
    Returns:
        A combined prompt template
        
    Raises:
        ValueError: If prompts have incompatible types
    """
    if not prompts:
        raise ValueError("At least one prompt is required")
    
    if len(prompts) == 1:
        return prompts[0]
    
    # Check that all prompts are the same type
    first_type = type(prompts[0])
    if not all(isinstance(p, first_type) for p in prompts):
        raise ValueError("All prompts must be of the same type")
    
    # Chain prompts
    result = prompts[0]
    for prompt in prompts[1:]:
        result = result + prompt
    
    return result


def extract_variables_from_text(text: str, 
                               template_format: TemplateFormat = TemplateFormat.F_STRING) -> List[str]:
    """Extract template variables from text.
    
    Args:
        text: Text to analyze
        template_format: Template format to use for extraction
        
    Returns:
        List of variable names found in the text
    """
    from aworld.core.context.prompts.formatters import get_template_variables
    return get_template_variables(text, template_format)


def validate_prompt_inputs(prompt: PromptTemplate, **kwargs: Any) -> bool:
    """Validate that all required inputs are provided for a prompt.
    
    Args:
        prompt: The prompt template to validate
        **kwargs: Input variables to check
        
    Returns:
        True if all required variables are provided
        
    Raises:
        ValueError: If required variables are missing
    """
    try:
        # Merge with partial variables
        merged_vars = prompt._merge_partial_and_user_variables(**kwargs)
        prompt._validate_input_variables(merged_vars)
        return True
    except ValueError:
        raise 