# coding: utf-8
"""
Executor-level hooks for aworld-cli executors.

Hooks allow custom processing at different stages of task execution:
- Input parsing (before/after)
- Context building (before/after)
- Task building (before/after)
- Task execution (before/after)
- Error handling

Reference: aworld-app-infra/src/aworldappinfra/core/hooks.py
"""
import abc
from typing import TYPE_CHECKING, Any, Optional

from aworld.runners.hook.hooks import Hook

if TYPE_CHECKING:
    from aworld.core.event.base import Message
    from aworld.core.context.base import Context

# Import concrete hook implementations to register them
# Hooks are registered in aworld.runners.hook.hook_factory.HookFactory


class ExecutorHookPoint:
    """Executor-level hook points."""
    # Input processing stage
    PRE_INPUT_PARSE = "pre_input_parse"        # Before parsing user input
    POST_INPUT_PARSE = "post_input_parse"      # After parsing user input
    
    # Context building stage
    PRE_BUILD_CONTEXT = "pre_build_context"    # Before building context
    POST_BUILD_CONTEXT = "post_build_context"  # After building context
    
    # Task building stage
    PRE_BUILD_TASK = "pre_build_task"          # Before building Task
    POST_BUILD_TASK = "post_build_task"        # After building Task
    
    # Task execution stage
    PRE_RUN_TASK = "pre_run_task"              # Before running task
    POST_RUN_TASK = "post_run_task"            # After running task
    
    # Error handling
    ON_TASK_ERROR = "on_task_error"            # When task execution fails


class ExecutorHook(Hook):
    """Base class for executor-level hooks.
    
    Executor hooks follow the same interface as runner hooks (Message, Context),
    but can extract additional parameters from message.headers or message.payload.
    """
    __metaclass__ = abc.ABCMeta
    
    @abc.abstractmethod
    def point(self) -> str:
        """Return the hook point name.
        
        Returns:
            Hook point name from ExecutorHookPoint
        """
        pass
    
    async def exec(self, message: 'Message', context: 'Context' = None) -> 'Message':  # type: ignore
        """
        Execute hook function.
        
        Executor hooks can extract parameters from:
        - message.payload: Main data payload
        - message.headers: Additional parameters (kwargs)
        - context: ApplicationContext or Context
        
        Args:
            message: Message object containing payload and headers
            context: Context object (ApplicationContext for executor hooks)
            
        Returns:
            Message object (can be same or modified)
            
        Example:
            >>> class MyHook(ExecutorHook):
            ...     async def exec(self, message, context):
            ...         user_message = message.headers.get('user_message')
            ...         # Process...
            ...         return message
        """
        # Default implementation: return message as-is
        # Subclasses should override this method
        return message


class PreInputParseHook(ExecutorHook):
    """Hook executed before parsing user input.
    
    Use case:
    - Extract @file references from raw user input
    - Validate input format
    - Pre-process input text
    
    Example:
        >>> @HookFactory.register(name="MyPreInputHook")
        >>> class MyPreInputHook(PreInputParseHook):
        ...     async def exec(self, message, context):
        ...         user_message = message.headers.get('user_message')
        ...         # Process user_message
        ...         message.headers['user_message'] = user_message.upper()
        ...         return message
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.PRE_INPUT_PARSE


class PostInputParseHook(ExecutorHook):
    """Hook executed after parsing user input.
    
    Use case:
    - Save parsed images to context working_dir
    - Extract image descriptions from user input
    - Store image metadata to context
    - Process multimodal content
    
    Example:
        >>> @HookFactory.register(name="ImageParseHook")
        >>> class ImageParseHook(PostInputParseHook):
        ...     async def exec(self, message, context):
        ...         # Extract params from message.headers
        ...         image_urls = message.headers.get('image_urls')
        ...         # Process images and save to context
        ...         message.headers['context'] = context
        ...         return message
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.POST_INPUT_PARSE


class PreBuildContextHook(ExecutorHook):
    """Hook executed before building context.
    
    Use case:
    - Modify TaskInput before context creation
    - Set up context configuration
    - Prepare context prerequisites
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.PRE_BUILD_CONTEXT


class PostBuildContextHook(ExecutorHook):
    """Hook executed after building context.
    
    Use case:
    - Add custom metadata to context
    - Initialize context state
    - Set up context-specific configurations
    
    Example:
        >>> @HookFactory.register(name="ImageMetadataHook")
        >>> class ImageMetadataHook(PostBuildContextHook):
        ...     async def exec(self, message, context):
        ...         # Add image metadata to context
        ...         message.headers['context'] = context
        ...         return message
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.POST_BUILD_CONTEXT


class PreBuildTaskHook(ExecutorHook):
    """Hook executed before building Task.
    
    Use case:
    - Modify task content before Task creation
    - Validate task parameters
    - Prepare task prerequisites
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.PRE_BUILD_TASK


class PostBuildTaskHook(ExecutorHook):
    """Hook executed after building Task.
    
    Use case:
    - Modify Task before execution
    - Attach additional metadata
    - Set up task-specific configurations
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.POST_BUILD_TASK


class PreRunTaskHook(ExecutorHook):
    """Hook executed before running task.
    
    Use case:
    - Final validation before task execution
    - Set up execution environment
    - Log task start
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.PRE_RUN_TASK


class PostRunTaskHook(ExecutorHook):
    """Hook executed after running task.
    
    Use case:
    - Process task results
    - Clean up resources
    - Generate reports
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.POST_RUN_TASK


class OnTaskErrorHook(ExecutorHook):
    """Hook executed when task encounters an error.
    
    Use case:
    - Error logging and reporting
    - Error recovery
    - Cleanup on error
    """
    
    def point(self) -> str:
        return ExecutorHookPoint.ON_TASK_ERROR
