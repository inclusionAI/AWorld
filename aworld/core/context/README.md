# AWorld Agent Context

Core context management system in the AWorld architecture for storing and managing complete Agent state information, including configuration data and runtime state.

## Architecture Overview

![Context Management](../../../readme_assets/context_management.png)

The Context Management system implements intelligent context processing with multiple optimization strategies based on context length analysis and configuration parameters.

## Features

- Complete Agent state management with support for state restoration and recovery
- Immutable configuration management and mutable runtime state tracking
- Intelligent LLM Prompt management and context optimization
- Complete LLM call intervention and control mechanisms
- Hook system support for extensible processing workflows
- Content compression and context window management

## Core Components

- `AgentContext`: Unified management container for Agent state and configuration
- `PromptProcessor`: Intelligent context processor supporting content compression and truncate
- `Hook System`: Extensible hook system supporting full-process LLM call intervention

## Context Object

Context is a singleton object that enables state sharing and coordination between multiple Agents in the AWorld framework.

### Key Features

- **Singleton Pattern**: Ensures a single shared context instance across the entire application
- **Dictionary Interface**: Supports simple key-value state storage using `context['key'] = value` syntax  
- **Multi-Agent Coordination**: Enables seamless data exchange between different Agents

### Example: State Management and Recovery

> **📋 Test Implementation**: See complete test implementation at [`tests/test_context_management.py::TestContextManagement::test_state_management_and_recovery()`](../../../tests/test_context_management.py)

```python
# Test state modification
agent.agent_context.system_prompt = "You are a Python expert who provides detailed and practical answers."
agent.agent_context['state'] = 1

# run with new state
response = agent.run("What is an agent. describe within 20 words")
assert response.answer is not None

# Verify state changes
assert agent.agent_context.system_prompt == "You are a Python expert who provides detailed and practical answers."
assert agent.agent_context['state'] == 1
```

### Example: Hook System, LLM Call Intervention and Agent state sharing

> **📋 Test Implementation**: See complete Hook system test implementations at:
> - [`tests/test_context_management.py::TestHookSystem::test_hook_registration()`](../../../tests/test_context_management.py) - Hook registration test
> - [`tests/test_context_management.py::TestHookSystem::test_hook_execution()`](../../../tests/test_context_management.py) - Hook execution test

```python
from aworld.runners.hook.hooks import PreLLMCallHook, PostLLMCallHook
from aworld.runners.hook.hook_factory import HookFactory
from aworld.utils.common import convert_to_snake
from aworld.core.event.base import Message
from aworld.core.context.base import Context

# Test Hook System functionality
@HookFactory.register(name="TestPreLLMHook", desc="Test pre-LLM hook")
class TestPreLLMHook(PreLLMCallHook):
    """Test hook for pre-LLM processing"""
    
    def name(self):
        return convert_to_snake("TestPreLLMHook")
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        """Test hook execution"""
        agent_context = context.get_agent_context(message.sender)
        if agent_context is not None:
            agent_context.step = 1 
        
        assert agent_context.step == 1 or agent_context.step == 2
        return message


@HookFactory.register(name="TestPostLLMHook", desc="Test post-LLM hook")
class TestPostLLMHook(PostLLMCallHook):
    """Test hook for post-LLM processing"""
    
    def name(self):
        return convert_to_snake("TestPostLLMHook")
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        """Test hook execution with llm_output processing"""
        agent_context = context.get_agent_context(message.sender)
        if agent_context is not None and agent_context.llm_output is not None:
            # Test dynamic prompt adjustment based on LLM output
            if hasattr(agent_context.llm_output, 'content'):
                content = agent_context.llm_output.content.lower()
                if content is not None:
                    agent_context.agent_prompt = "Success mode activated"

        assert agent_context.agent_prompt == "Success mode activated"
        return message

# Test hook registration and retrieval
assert "TestPreLLMHook" in HookFactory._cls
assert "TestPostLLMHook" in HookFactory._cls

# Test hook creation using __call__ method
pre_hook = HookFactory("TestPreLLMHook")
post_hook = HookFactory("TestPostLLMHook")

assert isinstance(pre_hook, TestPreLLMHook)
assert isinstance(post_hook, TestPostLLMHook)

# Test hook execution
response = agent.run("What is an agent. describe within 20 words")
assert response.answer is not None
```

## AgentContext Object

AgentContext is the core object for state management, containing the following key attributes:

### Immutable Configuration Fields
- `agent_id`: Agent unique identifier
- `agent_name`: Agent name
- `agent_desc`: Agent description
- `system_prompt`: System prompt
- `agent_prompt`: Agent-specific prompt
- `tool_names`: Tool name list
- `context_rule`: Context processing rules

### Mutable Runtime Fields
- `tools`: Tool object list
- `step`: Current execution step
- `messages`: Conversation message history
- `context_usage`: Context usage statistics

Example:
```python
agent = Agent(conf=config, name="example_agent")

# Access immutable configuration
print(f"Agent ID: {agent.agent_context.agent_id}")
print(f"System Prompt: {agent.agent_context.system_prompt}")
print(f"Tool Names: {agent.agent_context.tool_names}")

# Access runtime state
print(f"Current Step: {agent.agent_context.step}")
print(f"Message History: {len(agent.agent_context.messages)}")
print(f"Available Tools: {len(agent.agent_context.tools)}")
```

## Context Rule Configuration

`ContextRuleConfig` provides comprehensive context management through two main configuration components:

### OptimizationConfig

Controls context optimization behavior:

- `enabled`: Whether to enable context optimization (default: `False`)
- `max_token_budget_ratio`: Maximum token budget ratio for context window usage (default: `0.5`, range: 0.0-1.0)

### LlmCompressionConfig (Beta Feature)

**⚠️ Beta Feature**: This configuration is currently in beta and may undergo changes in future versions.

Controls intelligent context compression:

- `enabled`: Whether to enable LLM-based compression (default: `False`)
- `trigger_compress_token_length`: Token threshold to trigger basic compression (default: `10000`)
- `trigger_mapreduce_compress_token_length`: Token threshold to trigger map-reduce compression (default: `100000`)
- `compress_model`: ModelConfig for compression LLM calls (optional)


### Example: Using Default Context Configuration (Recommended)

> **📋 Test Implementation**: See default configuration test at [`tests/test_context_management.py::TestContextManagement::test_default_context_configuration()`](../../../tests/test_context_management.py)

```python
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig

# No need to explicitly configure context_rule, system automatically uses default configuration
# Default configuration is equivalent to:
# context_rule=ContextRuleConfig(
#     optimization_config=OptimizationConfig(
#         enabled=True,
#         max_token_budget_ratio=1.0  # Use 100% of context window
#     ),
#     llm_compression_config=LlmCompressionConfig(
#         enabled=False  # Compression disabled by default
#     )
# )
response = agent.run("What is an agent. describe within 20 words")

assert response.answer is not None
assert agent.agent_context.model_config.llm_model_name == "llama-2-7b-chat-hf-function-calling-v2"

# Test default context rule behavior
assert agent.agent_context.context_rule is not None
assert agent.agent_context.context_rule.optimization_config is not None
```

### Example: Custom Context Configuration

> **📋 Test Implementation**: See custom configuration test at [`tests/test_context_management.py::TestContextManagement::test_custom_context_configuration()`](../../../tests/test_context_management.py)

```python
from aworld.config.conf import AgentConfig, ContextRuleConfig, OptimizationConfig, LlmCompressionConfig, ModelConfig

# Create custom context rules
context_rule = ContextRuleConfig(
    optimization_config=OptimizationConfig(
        enabled=True,
        max_token_budget_ratio=0.8  # Use 80% of context window
    ),
    llm_compression_config=LlmCompressionConfig(
        enabled=True,  # Enable beta compression feature
        trigger_compress_token_length=100,
        trigger_mapreduce_compress_token_length=1000,
        compress_model=ModelConfig(
            llm_model_name="llama-2-7b-chat-hf-function-calling-v2",
            llm_base_url="http://localhost:1234/v1",
            llm_api_key="lm-studio",
        )
    )
)

# Save original rule for restoration
origin_rule = agent.agent_context.context_rule
agent.update_context_rule(context_rule)

# Test the agent with custom configuration
response = agent.run("What is an agent. describe within 20 words")
assert response.answer is not None

# Test configuration values
assert agent.agent_context.context_rule.optimization_config.enabled == True
assert agent.agent_context.context_rule.llm_compression_config.enabled == True

# Restore original rule
agent.update_context_rule(origin_rule)
```

## Notes

1. **Beta Features**: The `llm_compression_config` is currently in beta. Use with caution in production environments.
2. **Performance Trade-offs**: Enabling compression can save token usage but increases processing time. Adjust configuration based on actual needs.
3. **Model Compatibility**: Different models have different context length limitations. The system automatically adapts to model capabilities.
4. **Default Configuration**: The system provides reasonable default configuration. Manual configuration is unnecessary for most scenarios.
5. **State Management**: AgentContext supports state sharing between multiple Agents and ensures state consistency. State persistence functionality is currently under development.

Through proper configuration of AgentContext and context processors, you can significantly improve Agent performance in long conversations and complex tasks while optimizing token usage efficiency.
