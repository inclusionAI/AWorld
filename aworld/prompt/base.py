import logging
import re
from typing import Dict, Any, Optional, Callable, List

# Import system_prompt directly from the module
try:
    from aworld.prompt.templates.prompt import system_prompt as default_system_prompt
except ImportError:
    default_system_prompt = ""

class Prompt:
    """
    Prompt processing class, responsible for loading templates and rendering variables
    """
    
    def __init__(self, template: str = None):
        """
        Initialize Prompt class
        
        Args:
            template: Template content. If None, will load default system prompt.
        """
        if template is None:
            self.template_content = default_system_prompt
        else:
            self.template_content = template
            
        # Extract variables from the template
        self.variables = self._extract_variables(self.template_content)
    
    def _extract_variables(self, template_content: str) -> List[str]:
        """
        Extract variables from the template
        
        Args:
            template_content: Template content
            
        Returns:
            List of variables
        """
        # Use regular expression to extract {{variable}} format variables
        pattern = r'\{\{([a-zA-Z0-9_]+)\}\}'
        if not template_content:
            return []
        try:
            matches = re.findall(pattern, template_content)
            # Remove duplicates
            return list(set(matches))
        except Exception as e:
            logging.warning(f"Error extracting variables from template: {str(e)}")
            return []

    
    def _get_variable_values_from_api(self, variables: List[str]) -> Dict[str, Any]:
        """
        Get variable values from API
        
        Args:
            variables: List of variables
            
        Returns:
            Dictionary of variable values, key is variable name, value is variable value
        """
        # This is a mock implementation, should be replaced with actual API calls in real use
        result = {}
        
        for var_name in variables:
            # Mock values for some specific variables
            if var_name == "task_description":
                result[var_name] = "This is a default task description"
            elif var_name == "available_tools":
                result[var_name] = "- Search tool\n- Analysis tool\n- Visualization tool"
            elif var_name == "topic":
                result[var_name] = "The future of artificial intelligence"
            elif var_name == "opinion":
                result[var_name] = "Artificial intelligence will bring positive impacts"
            elif var_name == "oppose_opinion":
                result[var_name] = "Artificial intelligence will bring negative impacts"
            elif var_name == "search_results_content":
                result[var_name] = "- Research shows that AI can improve productivity\n- Experts believe that AI needs strict regulation"
            else:
                # For other variables, generate a placeholder
                result[var_name] = f"[Value of {var_name}]"
                
        return result
    
    def get_prompt(self, variables: Dict[str, Any] = None, variable_resolver: Optional[Callable[[List[str]], Dict[str, Any]]] = None) -> str:
        """
        Render template, replace variables
        
        Args:
            variables: Optional variable dictionary, if provided, will override API values
            variable_resolver: Optional variable resolver function, used to get variable values, higher priority than built-in API method
            
        Returns:
            Rendered content
        """
        if variables is None:
            variables = {}
        
        # Get variable values
        if variable_resolver:
            # If custom variable resolver is provided, use it
            resolved_variables = variable_resolver(self.variables)
        else:
            # Otherwise use the built-in API method
            resolved_variables = self._get_variable_values_from_api(self.variables)
        
        # Override API values with provided variables
        for var_name, var_value in variables.items():
            if var_value is not None:  # Only override when value is not None
                resolved_variables[var_name] = var_value
        
        # Render template
        rendered_content = self.template_content
        
        # Replace variables
        for var_name, var_value in resolved_variables.items():
            placeholder = f"{{{{{var_name}}}}}"
            rendered_content = rendered_content.replace(placeholder, str(var_value))
            
        return rendered_content

# Simplified API, only keep the prompt creation functionality
def create_prompt(template_content: str) -> Prompt:
    """
    Create Prompt from string
    
    Args:
        template_content: Template content
        
    Returns:
        Prompt object
    """
    return Prompt(template_content)