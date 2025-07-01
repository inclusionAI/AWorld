# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Chat-based prompt template implementation."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union, Tuple

from aworld.core.context.prompts.base_prompt_template import BasePromptTemplate, PromptValue, ChatPromptValue
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate
from aworld.core.context.prompts.formatters import TemplateFormat, get_template_variables

MessageLike = Union[Dict[str, Any], Tuple[str, str], 'BaseMessagePromptTemplate']
MessageRole = str


class BaseMessagePromptTemplate(ABC):
    """Base class for message prompt templates."""
    
    def __init__(self, template: str, 
                 template_format: TemplateFormat = TemplateFormat.F_STRING,
                 additional_kwargs: Optional[Dict[str, Any]] = None):
        self.prompt = StringPromptTemplate.from_template(template, template_format)
        self.additional_kwargs = additional_kwargs or {}
    
    @property
    @abstractmethod
    def role(self) -> str:
        """Return the message role."""
        pass
    
    @property
    def input_variables(self) -> List[str]:
        """Return the input variables for this message template."""
        return self.prompt.input_variables
    
    def format_message(self, **kwargs: Any) -> Dict[str, Any]:
        """Format this template into a message dictionary."""
        content = self.prompt.format(**kwargs)
        message = {
            "role": self.role,
            "content": content,
            **self.additional_kwargs
        }
        return message
    
    @classmethod
    def from_template(cls, template: str, 
                     template_format: TemplateFormat = TemplateFormat.F_STRING,
                     **kwargs: Any) -> 'BaseMessagePromptTemplate':
        """Create a message template from a template string."""
        return cls(template=template, template_format=template_format, **kwargs)
    
    def __str__(self) -> str:
        return f"{self.__class__.__name__}(role={self.role}, template={self.prompt.template!r})"
    
    def __repr__(self) -> str:
        return self.__str__()


class HumanMessagePromptTemplate(BaseMessagePromptTemplate):
    """Human message prompt template."""
    
    @property
    def role(self) -> str:
        return "user"


class AIMessagePromptTemplate(BaseMessagePromptTemplate):
    """AI message prompt template."""
    
    @property
    def role(self) -> str:
        return "assistant"


class SystemMessagePromptTemplate(BaseMessagePromptTemplate):
    """System message prompt template."""
    
    @property
    def role(self) -> str:
        return "system"


class MessagesPlaceholder:
    """A placeholder for a list of messages in a chat template."""
    
    def __init__(self, variable_name: str, optional: bool = False):
        self.variable_name = variable_name
        self.optional = optional
    
    @property
    def input_variables(self) -> List[str]:
        """Return the input variables for this placeholder."""
        return [self.variable_name] if not self.optional else []
    
    def format_messages(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Format the placeholder into a list of messages."""
        if self.variable_name not in kwargs:
            if self.optional:
                return []
            else:
                raise KeyError(f"Missing required variable: {self.variable_name}")
        
        messages = kwargs[self.variable_name]
        
        formatted_messages = []
        for message in messages:
            if isinstance(message, dict):
                formatted_messages.append(message)
            elif isinstance(message, (tuple, list)) and len(message) == 2:
                role, content = message
                formatted_messages.append({"role": role, "content": content})
            else:
                raise ValueError(f"Invalid message format: {message}")
        
        return formatted_messages
    
    def __str__(self) -> str:
        return f"MessagesPlaceholder(variable_name={self.variable_name}, optional={self.optional})"
    
    def __repr__(self) -> str:
        return self.__str__()


class ChatPromptTemplate(BasePromptTemplate):
    """Chat prompt template for structured conversations."""
    
    def __init__(self, 
                 messages: List[MessageLike],
                 template_format: TemplateFormat = TemplateFormat.F_STRING,
                 **kwargs: Any):
        self.messages = []
        self.template_format = template_format
        
        for message in messages:
            self.messages.append(self._convert_message(message, template_format))
        
        all_input_vars = []
        for message_template in self.messages:
            if hasattr(message_template, 'input_variables'):
                all_input_vars.extend(message_template.input_variables)
        
        input_variables = []
        for var in all_input_vars:
            if var not in input_variables:
                input_variables.append(var)
        
        super().__init__(
            input_variables=input_variables,
            template_format=template_format,
            **kwargs
        )
    
    def _convert_message(self, message: MessageLike, 
                        template_format: TemplateFormat) -> Union[BaseMessagePromptTemplate, MessagesPlaceholder]:
        """Convert a message-like object to internal format."""
        if isinstance(message, (BaseMessagePromptTemplate, MessagesPlaceholder)):
            return message
        
        elif isinstance(message, dict):
            role = message.get("role", "user")
            content = message.get("content", "")
            return self._create_message_template(role, content, template_format)
        
        elif isinstance(message, (tuple, list)) and len(message) == 2:
            role, content = message
            
            if role == "placeholder" or role == "messages":
                if content.startswith("{") and content.endswith("}"):
                    variable_name = content[1:-1]
                    return MessagesPlaceholder(variable_name, optional=True)
                else:
                    return MessagesPlaceholder(content, optional=True)
            
            return self._create_message_template(role, content, template_format)
        
        else:
            raise ValueError(f"Invalid message format: {message}")
    
    def _create_message_template(self, role: str, content: str, 
                               template_format: TemplateFormat) -> BaseMessagePromptTemplate:
        """Create a message template for the given role and content."""
        if role in ("user", "human"):
            return HumanMessagePromptTemplate(content, template_format)
        elif role in ("assistant", "ai", "bot"):
            return AIMessagePromptTemplate(content, template_format)
        elif role == "system":
            return SystemMessagePromptTemplate(content, template_format)
        else:
            class GenericMessagePromptTemplate(BaseMessagePromptTemplate):
                def __init__(self, role: str, template: str, template_format: TemplateFormat):
                    self._role = role
                    super().__init__(template, template_format)
                
                @property
                def role(self) -> str:
                    return self._role
            
            return GenericMessagePromptTemplate(role, content, template_format)
    
    def format(self, **kwargs: Any) -> str:
        """Format the chat prompt as a single string."""
        messages = self.format_messages(**kwargs)
        lines = []
        for message in messages:
            role = message.get("role", "unknown")
            content = message.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
    
    def format_prompt(self, **kwargs: Any) -> PromptValue:
        """Format the chat prompt and return a PromptValue object."""
        messages = self.format_messages(**kwargs)
        return ChatPromptValue(messages)
    
    def format_messages(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Format all messages in the chat prompt."""
        variables = self._merge_partial_and_user_variables(**kwargs)
        
        formatted_messages = []
        for message_template in self.messages:
            if isinstance(message_template, MessagesPlaceholder):
                placeholder_messages = message_template.format_messages(**variables)
                formatted_messages.extend(placeholder_messages)
            else:
                formatted_message = message_template.format_message(**variables)
                formatted_messages.append(formatted_message)
        
        return formatted_messages
    
    def _get_additional_kwargs(self) -> Dict[str, Any]:
        """Get additional kwargs needed for creating new instances."""
        return {"messages": self.messages}
    
    @classmethod
    def from_messages(cls, 
                     messages: List[MessageLike],
                     template_format: TemplateFormat = TemplateFormat.F_STRING) -> 'ChatPromptTemplate':
        """Create a ChatPromptTemplate from a list of messages."""
        return cls(messages=messages, template_format=template_format)
    
    @classmethod
    def from_template(cls, template: str, **kwargs: Any) -> 'ChatPromptTemplate':
        """Create a ChatPromptTemplate from a single template string."""
        return cls.from_messages([("user", template)], **kwargs)
    
    def append(self, message: MessageLike) -> None:
        """Append a message to the chat template."""
        converted_message = self._convert_message(message, self.template_format)
        self.messages.append(converted_message)
        
        if hasattr(converted_message, 'input_variables'):
            for var in converted_message.input_variables:
                if var not in self.input_variables:
                    self.input_variables.append(var)
    
    def extend(self, messages: List[MessageLike]) -> None:
        """Extend the chat template with multiple messages."""
        for message in messages:
            self.append(message)
    
    def __add__(self, other: Any) -> 'ChatPromptTemplate':
        """Combine two chat prompt templates."""
        if isinstance(other, ChatPromptTemplate):
            combined_messages = self.messages + other.messages
            return ChatPromptTemplate(
                messages=combined_messages,
                template_format=self.template_format
            )
        elif isinstance(other, (BaseMessagePromptTemplate, tuple, dict)):
            new_template = ChatPromptTemplate(
                messages=self.messages.copy(),
                template_format=self.template_format
            )
            new_template.append(other)
            return new_template
        else:
            raise TypeError(f"Cannot combine ChatPromptTemplate with {type(other)}")
    
    def __len__(self) -> int:
        """Return the number of messages in the template."""
        return len(self.messages)
    
    def __getitem__(self, index: Union[int, slice]) -> Union[MessageLike, 'ChatPromptTemplate']:
        """Get a message or slice of messages."""
        if isinstance(index, slice):
            return ChatPromptTemplate(
                messages=self.messages[index],
                template_format=self.template_format
            )
        else:
            return self.messages[index]
    
    @property
    def _prompt_type(self) -> str:
        """Return the prompt type identifier."""
        return "chat"
    
    def __str__(self) -> str:
        """String representation of the chat template."""
        return f"ChatPromptTemplate(messages={len(self.messages)} messages)"
    
    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"ChatPromptTemplate(messages={self.messages!r})" 