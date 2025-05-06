# AWorld Output Module

The Output module is a flexible and extensible system for managing outputs and artifacts in the AWorld framework.

## Key Features

- **Unified Output Management**: Centralized management of all output types (messages, artifacts, tool results, etc.) through a flexible Outputs interface.
- **Support for Multiple Output Types**: Handles text, code, files, tool calls, and custom outputs, enabling rich interaction and extensibility.
- **Async & Sync Streaming**: Provides both asynchronous and synchronous streaming of outputs, supporting real-time and batch processing scenarios.
- **Output Aggregation & Dispatch**: Aggregates outputs from various sources and dispatches them to different consumers or UIs.
- **Extensible Output Channels**: Easily extendable output channels and renderers for custom UI or integration needs.
- **Integration with Task & Workspace**: Seamlessly integrates with Task and WorkSpace modules for collaborative, versioned, and observable output management.
- **Real-time, Batch, and Streaming Modes**: Supports real-time, batch, and streaming output modes for flexible workflow requirements.
- **Observer Pattern Support**: Built-in observer pattern for real-time updates and notifications on output or artifact changes.
- **Easy Customization**: Designed for easy extension and customization to fit various application scenarios.
- **Rich UI Rendering**: Supports diverse output rendering with pluggable UI renderers for CLI, web, and custom frontends.
- **Decoupled UI & Output Types**: UI rendering is decoupled from output types, enabling flexible presentation and interaction.
- **Real-time Interactive Display**: Enables real-time, streaming, and interactive output display in various UI environments.
- **UI Extensibility**: Easy to extend and customize UI components to fit different user experiences and workflows.

## Class Diagram

```mermaid
classDiagram
    direction TB
    %% Output Related Classes
    class Output {
        +metadata: Dict
        +parts: List[OutputPart]
    }
    class OutputPart {
        +content: Any
        +metadata: Dict
    }
    Output *-- OutputPart
    class Outputs {
        <<abstract>>
        +add_output(output: Output)
        +sync_add_output(output: Output)
        +stream_events()
        +sync_stream_events()
        +mark_completed()
    }
    class AsyncOutputs {
        +add_output(output: Output)
        +sync_add_output(output: Output)
        +stream_events()
        +sync_stream_events()
    }
    class DefaultOutputs {
        +_outputs: List[Output]
        +add_output(output: Output)
        +sync_add_output(output: Output)
        +stream_events()
        +sync_stream_events()
        +mark_completed()
    }
    class StreamingOutputs {
        +input: Any
        +usage: dict
        +is_complete: bool
        +_output_queue: asyncio.Queue[Output]
        +_visited_outputs: List[Output]
        +_stored_exception: Exception
        +_run_impl_task: asyncio.Task
        +add_output(output: Output)
        +stream_events()
        +mark_completed()
    }
    class MessageOutput {
        +source: Any
        +reason_generator: Any
        +response_generator: Any
        +reasoning: str
        +response: Any
        +has_reasoning: bool
        +finished: bool
    }
    class Artifact {
        +artifact_id: str
        +artifact_type: ArtifactType
        +content: Any
        +metadata: Dict
    }
    class ToolCallOutput {
        +tool_id: str
        +content: Any
        +metadata: Dict
    }
    class ToolResultOutput {
        +tool_id: str
        +params: Any
        +content: Any
        +metadata: Dict
    }
    class SearchOutput {
        +artifact_id: str
        +artifact_type: ArtifactType
        +content: Any
        +metadata: Dict
    }

    %% Inheritance Relationships
    Outputs <|-- AsyncOutputs
    Outputs <|-- DefaultOutputs
    AsyncOutputs <|-- StreamingOutputs
    Output <|-- MessageOutput
    Output <|-- Artifact
    Output <|-- ToolCallOutput
    Output <|-- ToolResultOutput
    ToolResultOutput <|-- SearchOutput

    %% Aggregation/Composition
    Outputs o-- Output
    DefaultOutputs o-- Output
    StreamingOutputs o-- Output

    %% Workspace Related Classes
    class WorkSpace {
        +workspace_id: str
        +name: str
        +created_at: str
        +updated_at: str
        +metadata: Dict
        +artifacts: List[Artifact]
        +observers: List[WorkspaceObserver]
        +repository: ArtifactRepository
        +create_artifact()
        +add_artifact()
        +get_artifact()
        +update_artifact()
        +delete_artifact()
        +list_artifacts()
        +add_observer()
        +remove_observer()
        +save()
        +load()
    }
    class WorkspaceObserver
    class ArtifactRepository

    WorkSpace o-- Artifact
    WorkSpace o-- WorkspaceObserver
    WorkSpace o-- ArtifactRepository

    %% Task Related Classes
    class Task {
        +name: str
        +input: Any
        +outputs: Outputs
    }

    class Outputs

    Task o-- Outputs
```
