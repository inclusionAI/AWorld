# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""String-based prompt template implementation."""

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from aworld.core.context.prompts.base_prompt_template import BasePromptTemplate, PromptValue, StringPromptValue
from aworld.core.context.prompts.formatters import (
    TemplateFormat, 
    format_template, 
    get_template_variables
)
from aworld.core.context.prompts.dynamic_variables import ALL_DYNAMIC_VARIABLES

if TYPE_CHECKING:
    from aworld.core.context.base import Context

class StringPromptTemplate(BasePromptTemplate):
    """String-based prompt template."""
    
    def __init__(self, 
                 template: str,
                 input_variables: Optional[List[str]] = None,
                 template_format: TemplateFormat = TemplateFormat.F_STRING,
                 partial_variables: Optional[Dict[str, Any]] = None,
                 auto_add_dynamic_vars: bool = True,
                 **kwargs):
        self.template = template
        self.auto_add_dynamic_vars = auto_add_dynamic_vars
        
        # 自动添加通用动态变量
        if auto_add_dynamic_vars:
            # 合并时间和系统变量
            auto_partial_vars = {**ALL_DYNAMIC_VARIABLES}
            
            if partial_variables:
                # 用户提供的变量优先，不被自动变量覆盖
                for key, value in auto_partial_vars.items():
                    if key not in partial_variables:
                        partial_variables[key] = value
            else:
                partial_variables = auto_partial_vars
        
        if input_variables is None:
            input_variables = get_template_variables(template, template_format)
        
        if partial_variables:
            input_variables = [var for var in input_variables if var not in partial_variables]
        
        super().__init__(
            input_variables=input_variables,
            template_format=template_format,
            partial_variables=partial_variables,
            **kwargs
        )
        
    def format(self, context: 'Context' = None, **kwargs: Any) -> str:
        variables = self._merge_partial_and_user_variables(context=context, **kwargs)
        self._validate_input_variables(variables)
        return format_template(self.template, self.template_format, **variables)
    
    def format_prompt(self, context: 'Context' = None, **kwargs: Any) -> PromptValue:
        formatted_text = self.format(context=context, **kwargs)
        return StringPromptValue(formatted_text)
    
    def _get_additional_kwargs(self) -> Dict[str, Any]:
        return {
            "template": self.template,
            "auto_add_dynamic_vars": getattr(self, 'auto_add_dynamic_vars', True)
        }
    
    @classmethod
    def from_template(cls,
                     template: str,
                     template_format: TemplateFormat = TemplateFormat.F_STRING,
                     partial_variables: Optional[Dict[str, Any]] = None,
                     auto_add_dynamic_vars: bool = True,
                     **kwargs: Any) -> 'StringPromptTemplate':
        """Create a StringPromptTemplate from a template string."""
        return cls(
            template=template,
            input_variables=None,
            template_format=template_format,
            partial_variables=partial_variables,
            auto_add_dynamic_vars=auto_add_dynamic_vars,
            **kwargs
        )
    
    @classmethod 
    def from_file(cls,
                  template_file: str,
                  encoding: str = "utf-8",
                  **kwargs: Any) -> 'StringPromptTemplate':
        """Create a StringPromptTemplate from a file."""
        try:
            with open(template_file, 'r', encoding=encoding) as f:
                template = f.read()
            return cls.from_template(template, **kwargs)
        except FileNotFoundError:
            raise FileNotFoundError(f"Template file not found: {template_file}")
        except IOError as e:
            raise IOError(f"Error reading template file {template_file}: {e}")
    
    def __add__(self, other: Any) -> 'StringPromptTemplate':
        """Combine two string prompt templates."""
        if isinstance(other, StringPromptTemplate):
            if self.template_format != TemplateFormat.F_STRING:
                raise ValueError("Can only combine f-string format templates")
            if other.template_format != TemplateFormat.F_STRING:
                raise ValueError("Can only combine f-string format templates")
            
            combined_template = self.template + other.template
            combined_input_vars = list(set(self.input_variables + other.input_variables))
            
            combined_partial_vars = self.partial_variables.copy()
            for key, value in other.partial_variables.items():
                if key in combined_partial_vars and combined_partial_vars[key] != value:
                    raise ValueError(f"Conflicting partial variable '{key}' in templates")
                combined_partial_vars[key] = value
            
            return StringPromptTemplate(
                template=combined_template,
                input_variables=combined_input_vars,
                template_format=TemplateFormat.F_STRING,
                partial_variables=combined_partial_vars
            )
        
        elif isinstance(other, str):
            other_prompt = StringPromptTemplate.from_template(other)
            return self + other_prompt
        
        else:
            raise TypeError(f"Cannot combine StringPromptTemplate with {type(other)}")
    
    @property
    def _prompt_type(self) -> str:
        return "string"
    
    def __str__(self) -> str:
        return f"StringPromptTemplate(template={self.template!r})"
    
    def __repr__(self) -> str:
        return (f"StringPromptTemplate("
                f"template={self.template!r}, "
                f"input_variables={self.input_variables}, "
                f"template_format={self.template_format})")

# 为了向后兼容，保留PromptTemplate别名
PromptTemplate = StringPromptTemplate 