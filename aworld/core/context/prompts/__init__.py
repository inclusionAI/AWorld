"""AWorld Prompt Templates Module.

This module provides a simplified implementation of prompt templates inspired by LangChain.
It supports string formatting, chat message templates, and various formatting options.

**Class hierarchy:**

.. code-block::

    BasePromptTemplate --> StringPromptTemplate (PromptTemplate)
                       --> ChatPromptTemplate

    BaseMessagePromptTemplate --> HumanMessagePromptTemplate
                              --> AIMessagePromptTemplate  
                              --> SystemMessagePromptTemplate
                              --> MessagesPlaceholder

**Main components:**

- **StringPromptTemplate**: For simple string-based prompts with variable substitution
- **ChatPromptTemplate**: For structured chat conversations with multiple message types
- **MessagePromptTemplate**: For individual chat messages (Human, AI, System)
- **MessagesPlaceholder**: For injecting lists of messages into chat templates

**Example usage:**

.. code-block:: python

    from aworld.core.context.prompts import PromptTemplate, ChatPromptTemplate
    
    # Simple string template
    prompt = PromptTemplate.from_template("Hello {name}, how are you?")
    result = prompt.format(name="Alice")
    
    # Chat template
    chat_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant."),
        ("human", "Hello, my name is {name}"),
        ("ai", "Nice to meet you, {name}!"),
        ("human", "{user_input}")
    ])
    messages = chat_prompt.format_messages(name="Alice", user_input="What's the weather?")

"""

from aworld.core.context.prompts.base_prompt_template import BasePromptTemplate
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate, PromptTemplate
from aworld.core.context.prompts.chat_prompt_template import (
    ChatPromptTemplate,
    BaseMessagePromptTemplate,
    HumanMessagePromptTemplate,
    AIMessagePromptTemplate,
    SystemMessagePromptTemplate,
    MessagesPlaceholder,
)
from aworld.core.context.prompts.formatters import (
    TemplateFormat,
    format_template,
    get_template_variables,
)

__all__ = [
    # Base classes
    "BasePromptTemplate",
    "BaseMessagePromptTemplate",
    
    # Prompt templates
    "StringPromptTemplate",
    "PromptTemplate",  # Backward compatibility alias
    "ChatPromptTemplate",
    
    # Message templates
    "HumanMessagePromptTemplate",
    "AIMessagePromptTemplate", 
    "SystemMessagePromptTemplate",
    "MessagesPlaceholder",
    
    # Formatters and utilities
    "TemplateFormat",
    "format_template",
    "get_template_variables",
] 