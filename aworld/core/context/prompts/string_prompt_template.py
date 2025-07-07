# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""String-based prompt template implementation."""

from typing import Any, Dict, List, Optional, TYPE_CHECKING
from aworld.core.context.prompts import logger

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
        
        try:
            # Automatically add common dynamic variables
            if auto_add_dynamic_vars:
                # Merge time and system variables
                auto_partial_vars = {**ALL_DYNAMIC_VARIABLES}
                
                if partial_variables:
                    # User-provided variables take priority and are not overridden by auto variables
                    for key, value in auto_partial_vars.items():
                        if key not in partial_variables:
                            partial_variables[key] = value
                else:
                    partial_variables = auto_partial_vars
            
            if input_variables is None:
                input_variables = get_template_variables(template, template_format)
            
            if partial_variables:
                input_variables = [var for var in input_variables if var not in partial_variables]
        except Exception as e:
            # If any error during initialization, use defaults
            logger.warning(f"Error during StringPromptTemplate initialization: {e}, using defaults")
            if input_variables is None:
                input_variables = []
            if partial_variables is None:
                partial_variables = {}
        
        super().__init__(
            input_variables=input_variables,
            template_format=template_format,
            partial_variables=partial_variables,
            **kwargs
        )
        
    def format(self, context: 'Context' = None, **kwargs: Any) -> str:
        try:
            variables = self._merge_partial_and_user_variables(context=context, **kwargs)
            self._validate_input_variables(variables)
            return format_template(self.template, self.template_format, **variables)
        except Exception as e:
            # If any error during formatting, return original template
            logger.warning(f"Error formatting StringPromptTemplate: {e}, returning original template")
            return self.template
    
    def format_prompt(self, context: 'Context' = None, **kwargs: Any) -> PromptValue:
        try:
            formatted_text = self.format(context=context, **kwargs)
            return StringPromptValue(formatted_text)
        except Exception as e:
            # If any error during formatting, return original template wrapped in StringPromptValue
            logger.warning(f"Error formatting StringPromptTemplate to PromptValue: {e}, returning original template")
            return StringPromptValue(self.template)
    
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
    
    def __add__(self, other: Any) -> 'StringPromptTemplate':
        """Combine two string prompt templates."""
        try:
            if isinstance(other, StringPromptTemplate):
                if self.template_format != TemplateFormat.F_STRING:
                    # If format doesn't match, just concatenate templates as strings
                    combined_template = self.template + other.template
                    logger.warning(f"Combining StringPromptTemplates with different formats, concatenating templates as strings. Original: {self.template_format}, Other: {other.template_format}")
                    return StringPromptTemplate.from_template(combined_template)
                if other.template_format != TemplateFormat.F_STRING:
                    # If format doesn't match, just concatenate templates as strings
                    combined_template = self.template + other.template
                    logger.warning(f"Combining StringPromptTemplates with different formats, concatenating templates as strings. Original: {self.template_format}, Other: {other.template_format}")
                    return StringPromptTemplate.from_template(combined_template)
                
                combined_template = self.template + other.template
                combined_input_vars = list(set(self.input_variables + other.input_variables))
                
                combined_partial_vars = self.partial_variables.copy()
                for key, value in other.partial_variables.items():
                    if key in combined_partial_vars and combined_partial_vars[key] != value:
                        # If conflicting partial variables, just use the first one
                        logger.warning(f"Combining StringPromptTemplates with conflicting partial variables. Key: {key}, Original: {combined_partial_vars[key]}, Other: {value}")
                        continue
                    combined_partial_vars[key] = value
                
                logger.info(f"Successfully combined StringPromptTemplates. New template: {combined_template}, New input_variables: {combined_input_vars}, New partial_variables: {combined_partial_vars}")
                return StringPromptTemplate(
                    template=combined_template,
                    input_variables=combined_input_vars,
                    template_format=TemplateFormat.F_STRING,
                    partial_variables=combined_partial_vars
                )
            
            elif isinstance(other, str):
                other_prompt = StringPromptTemplate.from_template(other)
                logger.warning(f"Combining StringPromptTemplate with string, treating string as a template. Original: {self.template_format}, Other: {other_prompt.template_format}")
                return self + other_prompt
            
            else:
                # If cannot combine, just concatenate as strings
                combined_template = self.template + str(other)
                logger.warning(f"Combining StringPromptTemplate with non-StringPromptTemplate, concatenating as strings. Original: {self.template_format}, Other: {type(other)}")
                return StringPromptTemplate.from_template(combined_template)
        except Exception as e:
            # If any error during combination, just concatenate templates as strings
            logger.warning(f"Error during StringPromptTemplate combination: {e}, falling back to string concatenation")
            try:
                if isinstance(other, StringPromptTemplate):
                    combined_template = self.template + other.template
                else:
                    combined_template = self.template + str(other)
                return StringPromptTemplate.from_template(combined_template)
            except Exception as e2:
                # If even string concatenation fails, return self
                logger.error(f"Even string concatenation failed during StringPromptTemplate combination: {e2}, returning self")
                return self
    
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

# For backward compatibility, keep PromptTemplate alias
PromptTemplate = StringPromptTemplate 