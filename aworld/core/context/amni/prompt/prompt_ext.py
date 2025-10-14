import copy
import traceback
from typing import List, Dict, Any
from typing import Optional

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.prompt.neurons import neuron_factory, Neurons
from aworld.core.context.amni.utils.context_log import PromptLogger
from aworld.core.context.prompts import StringPromptTemplate, TemplateFormat, format_template
from aworld.core.context.prompts.dynamic_variables import ALL_PREDEFINED_DYNAMIC_VARIABLES
from aworld.logs.util import logger

DEFAULT_VALUE = None

def logical_schema_getter(
        field_path: str,
        context: ApplicationContext = None,
        recursive: bool = True,
        **kwargs
) -> str:
    agent_id = kwargs.get("agent_id", None)
    return ApplicationContext.get_logical_schema_field(
        key=field_path, context=context, recursive=recursive, agent_id=agent_id)

def create_neuron_formatter(neuron_type: Neurons):
    """
    Factory function to create neuron formatter functions.
    
    This eliminates code duplication by dynamically generating formatter functions
    for different neuron types with the same logic pattern.
    
    Args:
        neuron_type: The type of neuron to format
        
    Returns:
        An async function that formats the neuron content
        
    Example:
        >>> working_dir_desc = create_neuron_formatter(Neurons.WORKING_DIR)
        >>> result = await working_dir_desc(context)
    """
    async def formatter(context: ApplicationContext) -> str:
        component = neuron_factory.get_neuron(neuron_type)
        if not component:
            return ""
        return await component.format(context=context)
    
    return formatter


CONTEXT_PREDEFINED_DYNAMIC_VARIABLES = dict(ALL_PREDEFINED_DYNAMIC_VARIABLES, **{
    # user specified context
    "ai_context": ApplicationContext.ai_context,
    # dynamically generated neuron formatters
    "working_dir": create_neuron_formatter(Neurons.WORKING_DIR),
    "facts": create_neuron_formatter(Neurons.FACT),
    "task_history": create_neuron_formatter(Neurons.TASK),
    "todo_info": create_neuron_formatter(Neurons.TODO),
    "action_info": create_neuron_formatter(Neurons.ACTION_INFO),
    "conversation_history": create_neuron_formatter(Neurons.CONVERSATION_HISTORY)
})

CONTEXT_LOGICAL_SCHEMA_GETTER = logical_schema_getter

class ContextPromptTemplate(StringPromptTemplate):

    def __init__(self,
                 template: str,
                 input_variables: Optional[List[str]] = None,
                 template_format: TemplateFormat = TemplateFormat.DOUBLE_BRACE,
                 partial_variables: Optional[Dict[str, Any]] = None,
                 auto_add_dynamic_vars: bool = True,
                 **kwargs):
        result_partial_variables = copy.deepcopy(CONTEXT_PREDEFINED_DYNAMIC_VARIABLES)
        if partial_variables:
            result_partial_variables = {
                **partial_variables
            }

        super().__init__(template= template,
                         input_variables=input_variables,
                         template_format=template_format,
                         partial_variables=result_partial_variables,
                         auto_add_dynamic_vars=auto_add_dynamic_vars,
                         **kwargs
                         )

    async def async_format(self, context: 'Context' = None, **kwargs: Any) -> str:
        try:
            variables = await self.async_merge_partial_and_user_variables(context=context, **kwargs)
            self._validate_input_variables(variables)
            # 记录格式化的参数日志
            PromptLogger.log_formatted_parameters(variables)
            return format_template(self.template, self.template_format, **variables)
        except Exception as e:
            # If any error during formatting, return original template
            logger.warning(f"Error formatting StringPromptTemplate: {e}, returning original template")
            return self.template

    async def async_merge_partial_and_user_variables(self, context: 'Context' = None, **kwargs: Any) -> Dict[str, Any]:
        merged = {}
        try:
            for key, value in self.partial_variables.items():
                if key not in self.input_variables:
                    continue
                # 1. get filed from context by predefined schema
                if key not in CONTEXT_PREDEFINED_DYNAMIC_VARIABLES.keys():
                    merged[key] = CONTEXT_LOGICAL_SCHEMA_GETTER(field_path=key, context=context, **kwargs)
                    if merged[key] != DEFAULT_VALUE:
                        continue

                # 2. user specified value getter or predefined value getter
                try:
                    if callable(value):
                        # If it's a function, try to pass context as a parameter
                        try:
                            # Check if the function accepts context parameter
                            import inspect
                            sig = inspect.signature(value)
                            
                            # Check if it's an async function
                            is_async = inspect.iscoroutinefunction(value)
                            
                            if ("context" in sig.parameters.keys()) == True:
                                # If function accepts context parameter, pass the context
                                if is_async:
                                    # For async functions, we need to await them
                                    merged[key] = await value(context=context)
                                else:
                                    # For sync functions, call directly
                                    merged[key] = value(context=context)
                                logger.debug(
                                    f"prompt template parameter={sig.parameters} {sig.parameters.keys()} {'context' in sig.parameters.keys()}\nkey={key} value={merged[key]} is_async={is_async}")
                            else:
                                # Otherwise call directly
                                if is_async:
                                    # For async functions, we need to await them
                                    merged[key] = await value()
                                else:
                                    # For sync functions, call directly
                                    merged[key] = value()
                        except Exception as e:
                            logger.error(
                                f"fallbackpre: Error calling function {key} even without parameters: {e}, using placeholder, traceback is {traceback.format_exc()}")
                            # If error occurs, fallback to no-parameter call
                            try:
                                logger.error(f"Error calling function {key}: {e}")
                                if is_async:
                                    merged[key] = await value()
                                else:
                                    merged[key] = value()
                            except Exception as e2:
                                # If still error, use default value or placeholder
                                logger.error(
                                    f"Error calling function {key} even without parameters: {e2}, using placeholder, traceback is {traceback.format_exc()}")
                                merged[key] = f"<Error calling function {key}: {e}>"
                    else:
                        merged[key] = value
                except Exception as e:
                    # If any error processing this variable, use placeholder
                    logger.warning(f"Error processing partial variable {key}: {e}, using placeholder, traceback is {traceback.format_exc()}")
                    merged[key] = f"<Error processing {key}: {e}>"

            for key, value in kwargs.items():
                if key in self.input_variables:
                    merged[key] = value
        except Exception as e:
            # If any error in the whole process, at least return kwargs
            logger.error(f"Error in _merge_partial_and_user_variables: {e}, returning only kwargs")
            merged = kwargs.copy()

        # 遍历merged，将None值改成空字符串
        for key, value in merged.items():
            if value is None or value == 'unknown':
                merged[key] = ""

        return merged


