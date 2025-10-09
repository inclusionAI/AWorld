# Trace Module

## Overview
The trace module is built on top of OpenTelemetry and provides a flexible interface for creating and managing spans in your application. It offers multiple ways to create spans to accommodate different use cases and supports various backends for storing and visualizing trace data.


## Trace Configuration
The trace module provides flexible configuration options for different scenarios:

### Basic Configuration
```python
import aworld.trace as trace

# Default configuration
trace.configure()

# Custom configuration
trace.configure(ObservabilityConfig(
    trace_provider="otlp",
    trace_backends=["memory", "logfire"],
    trace_base_url="https://api.logfire.io/v1/traces",
    trace_write_token="your_api_token",
    trace_server_enabled=True,
    trace_server_port=7079
))

```

### Configuration Options
The ObservabilityConfig class provides the following key configuration options:
| Option | Description | Default Value|
|--------|-------------|---------|
| `trace_provider` | The trace provider to use (currently only "otlp" is supported) | "otlp" |
| `trace_backends` | List of trace backends to use (e.g., "memory", "logfire") | ["memory"] |
| `trace_base_url` | Base URL for trace export (e.g., Logfire API endpoint) | None |
| `trace_write_token` | Write token for trace export | None |
| `trace_span_consumers` | List of span consumers to process spans | [] |
| `trace_storage` | Custom storage for trace data | None |
| `trace_server_enabled` | Whether to enable the trace server | False |
| `trace_server_port` | Port for the trace server | 7079 |
| `trace_id_generator` | Custom ID generator for trace IDs | None |

## Span Creation Methods
The trace module provides several methods for creating spans, each suitable for different scenarios:

### 1. Basic Span Creation
Use the span() function for general-purpose span creation:

```python
import aworld.trace as trace

with trace.span("my_span") as span:
    # Code to be traced
    pass

# With attributes
with span("operation_name", attributes={"key": "value"}):
    # Code to be traced
    pass
```
This method is available through the GLOBAL_TRACE_MANAGER instance and creates a ContextSpan that automatically ends when the context manager exits.

### 2. Function Tracing
Use the func_span() decorator to trace function execution:

```python
import aworld.trace as trace

@trace.func_span()
def my_function():
    # Code to be traced
    pass

@trace.func_span("custom_span_name", attributes={"key": "value"})
def another_function():
    # Code to be traced
    pass

# With argument extraction
@trace.func_span(extract_args=True)
def function_with_args(param1, param2):
    # Function arguments will be added as span attributes
    pass
```

### 3. Message Tracing
For tracing event messages in the AWorld event system:

```python
import aworld.trace as trace

# Trace a message
message = Message(category="AGENT", payload={}, sender="agent1", receiver="agent2")
with trace.message_span(message) as span:
    # Code handling the message
    pass
```
The message_span() function automatically extracts relevant information from the message and sets appropriate span attributes.

### 4. Handler Tracing
For tracing message handlers:

```python
import aworld.trace as trace

async def my_handler(self, message):
    with trace.handler_span(message, self):
        # Handler logic
        pass
```

### 5. Task Tracing
For tracing task execution:

```python
import aworld.trace as trace

# Trace a task
with trace.task_span(session_id="session123", task=task_object) as span:
    # Task execution code
    pass
```

### 6. Automatic Tracing
Enable automatic tracing for entire modules:

```python
import aworld.trace as trace

# Trace functions in specific modules that take longer than 100ms
trace.auto_tracing(module_name="my_module", min_duration_ms=0.1)

# Or with a custom filter function
def module_filter(module):
    return "aworld" in module.name and not "test" in module.name

trace.auto_tracing(modules=module_filter, min_duration=0.1)

```

## Span Consumer
Span consumers allow you to process span data for various purposes such as logging, metrics collection, or sending to external systems.

To create a custom span consumer, implement the SpanConsumer abstract class, and register it using the register_span_consumer decorator:

```python
from aworld.trace.span_cosumer import SpanConsumer
from aworld.trace.base import Span
from aworld.trace.span_cosumer import register_span_consumer
from typing import Sequence

@register_span_consumer
class MySpanConsumer(SpanConsumer):
    def consume(self, spans: Sequence[Span]) -> None:
        # Process the spans
        for span in spans:
            print(f"Span processed: {span.get_name()}")
```

