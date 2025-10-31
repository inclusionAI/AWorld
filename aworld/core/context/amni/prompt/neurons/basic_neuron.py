import inspect
from datetime import datetime
from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="basic", desc="Basic neuron for dynamic variables", prio=1)
class BasicNeuron(Neuron):
    """Neuron for handling dynamic variable related properties"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format dynamic variable information (synchronous version, handles synchronous functions)"""
        items = []
        
        try:
            # Import dynamic variables
            from ...prompt.prompt_ext import ALL_PREDEFINED_DYNAMIC_VARIABLES

            target_keys = {
                "current_date"
            }
            # Exclude unused keys
            excluded_keys = {
                'system_platform', 'system_os', 'python_version', 
                'hostname', 'username', 'working_directory', 
                'random_uuid', 'short_uuid',

            }
            
            # Process predefined dynamic variables
            for key, value in ALL_PREDEFINED_DYNAMIC_VARIABLES.items():
                if key not in target_keys:
                    continue
                # Skip excluded keys
                if key in excluded_keys:
                    continue
                try:
                    if callable(value):
                        # Check function signature
                        sig = inspect.signature(value)
                        is_async = inspect.iscoroutinefunction(value)
                        
                        # Only process synchronous functions
                        if not is_async:
                            if "context" in sig.parameters:
                                # Synchronous function, requires context parameter
                                try:
                                    result = value(context=context)
                                    if result:
                                        items.append(f"<{key}>{result}</{key}>")
                                except Exception as e:
                                    items.append(f"<{key}>error: {str(e)}</{key}>")
                            else:
                                # Synchronous function, no context parameter
                                try:
                                    result = value()
                                    if result:
                                        items.append(f"<{key}>{result}</{key}>")
                                except Exception as e:
                                    items.append(f"<{key}>error: {str(e)}</{key}>")
                        else:
                            # Async function, mark as requiring async processing in sync method
                            items.append(f"<{key}>async_function_requires_async_context</{key}>")
                    else:
                        # Non-function value
                        if value:
                            items.append(f"<{key}>{value}</{key}>")
                except Exception as e:
                    items.append(f"<{key}>error: {str(e)}</{key}>")
        
        except ImportError:
            items.append("<dynamic_var_error>Cannot import CONTEXT_PREDEFINED_DYNAMIC_VARIABLES</dynamic_var_error>")
        except Exception as e:
            items.append(f"<dynamic_var_error>Error processing dynamic variables: {str(e)}</dynamic_var_error>")
        
        return items
    
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """Combine dynamic variable information"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return f"\n\nThis year is {datetime.now().strftime('%Y')}, Today is: {datetime.now().strftime('%Y-%m-%d')}, please keep in touch."
