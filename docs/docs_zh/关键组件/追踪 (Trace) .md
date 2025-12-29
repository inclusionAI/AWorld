## 总览
AWorld Trace 模块是一个基于 OpenTelemetry 构建的全功能分布式追踪系统，为 AWorld 框架提供完整的可观测性能力。该模块提供了灵活的追踪配置、自动插桩和手动追踪能力，支持多种后端存储和导出方式。



### 核心特性
+ 基于 OpenTelemetry 标准实现，支持与主流可观测性平台兼容
+ 提供自动插桩功能，无需修改代码即可追踪关键组件（Agent、Tool、LLM等）
+ 支持手动创建和管理追踪 Span，实现精细化追踪
+ 内置内存存储和 Trace UI Server，方便快速查看追踪数据
+ 支持与第三方可观测性平台（如 Logfire、Jaeger）集成
+ 提供灵活的配置选项，满足不同场景需求

### 主要组件
+ TraceManager ：核心追踪管理器，负责创建和管理追踪 Span
+ ContextManager ：上下文管理器，处理追踪上下文的传播
+ Instrumentation ：自动插桩组件，对框架核心组件进行追踪
+ OpenTelemetryAdapter ：OpenTelemetry 适配器，实现与 OpenTelemetry API 的桥接
+ TraceServer ：内置追踪 UI 服务器，提供可视化追踪数据查看能力



## 快速开始
### 配置启用 Trace
要在 AWorld 项目中启用 Trace 记录，您需要在项目启动时配置 ObservabilityConfig：

```python
from aworld.trace.config import configure, ObservabilityConfig

# 配置 Trace
config = ObservabilityConfig(
    trace_provider="otlp",
    trace_backends=["memory"],  # 使用内存存储
    trace_server_enabled=True,  # 启用内置 Trace UI Server
    trace_server_port=7079       # Trace UI Server 端口
)

# 应用配置
configure(config)
```

### 使用内置 Trace UI Server 查看 Trace
启用 Trace UI Server 后，您可以通过浏览器访问以下地址查看追踪数据：[http://localhost:7079](http://localhost:7079)

Trace UI 提供了以下功能：

+ 查看所有追踪记录列表
+ 查看单个追踪的完整调用链路
+ 查看每个 Span 的详细信息，包括属性、状态和持续时间
+ 支持按时间排序和筛选追踪记录



### Trace UI 信息解读
![](https://intranetproxy.alipay.com/skylark/lark/0/2025/png/193971/1765178334359-c565bcea-f427-4aca-9c07-07a35529c3dc.png)

AWorld 框架在运行过程中会自动创建多个核心 Span，用于追踪框架各个组件的执行情况。以下是主要的核心 Span 类型：

+ Event Span
    - 名称格式：event.<event类型>
    - 描述：AWorld 是以event驱动的运行框架，Trace系统默认会为各个Event的执行创建一个Span，Span名称以“event.”为前缀，“event.”后面的二级名称为event类型，如:event.output，代表一条output事件
    - 主要属性：
        * event.topic：事件主题
        * event.payload：事件详情
        * event.sender：事件发送者
        * event.receiver：事件接收者
+ Task Span
    - 名称格式：task.<session_id>
    - 描述：代表一个完整的任务执行过程
    - 主要属性：
        * task.id：任务id
        * task.input：用户输入
        * task.is_sub_task：是否为子任务
+ Agent Span
    - 名称格式：agent.<agent名称>
    - 描述：代表一个agent的执行过程
    - 主要属性：
        * agent.name ：agent名称
        * agent.id ：agent ID
        * session.id: session ID
        * user.id: 用户 ID
+ Tool Span
    - 名称格式：tool.<工具名称>
    - 描述：代表一个工具的执行过程，如果是mcp工具，<工具名称>为mcp服务的名称
    - 主要属性
        * tool.name：工具名称
        * agent.name ：调用工具的agent名称
        * agent.id ：调用工具的agent ID
        * ession.id: session ID
        * user.id: 用户 ID
    - 如何查看工具执行结果
        * 查看子span中的“event.output.tool_call_result”span
+ LLM Span
    - 名称格式：llm.<模型名称>
    - 描述：代表一次LLM调用过程
    - 主要属性
        * gen_ai.prompt：调用LLM的提示词
        * gen_ai.prompt.tools：调用LLM的可用工具列表
        * gen_ai.request.<参数名>：调用LLM时的请求参数，如：gen_ai.request.top_k
        * gen_ai.completion.content：LLM完成响应内容
        * gen_ai.duration：LLM完成响应时间
        * gen_ai.completion.reasoning_content：LLM推理内容
        * gen_ai.completion.tool_calls：LLM返回的工具调用指令
        * gen_ai.first_token_duration：LLM返回的首token时间
        * gen_ai.usage.input_tokens：LLM调用的输入token使用量
        * gen_ai.usage.output_tokens：LLM调用的输出token使用量
        * gen_ai.usage.total_tokens：LLM调用的token总使用量



## 核心概念
### Trace（追踪）
Trace 是一个完整的调用链路，由多个 Span 组成，代表了一个完整的操作流程。每个 Trace 都有一个唯一的 Trace ID，用于标识整个调用链路。在AWorld中，默认情况下，一个Trace代表用户请求的一个Task执行过程。

### Span（跨度）
Span 是 Trace 中的单个操作单元，代表了一个具体的执行步骤。每个 Span 都有一个唯一的 Span ID，包含以下信息：

+ 名称：操作的名称
+ 开始时间和结束时间：操作的执行时间范围
+ 持续时间：操作执行的总时长
+ 属性：与操作相关的键值对信息
+ 状态：操作的执行状态（成功、失败等）
+ 父 Span ID：父操作的 Span ID
+ 子 Span 列表：当前操作的子操作列表

### TraceContext（追踪上下文）
TraceContext 是跨服务传递追踪信息的载体，包含 Trace ID、Span ID 等信息，用于在分布式环境中关联不同服务的 Span。

### TraceProvider（追踪提供者）
TraceProvider 是创建和管理 Tracer 的工厂，负责配置和初始化追踪系统。AWorld对Trace操作实现了高层抽象，使AWorld其他各功能模块对Trace的使用不依赖于某个具体的Trace实现框架，不同的三方Trace框架可以提供各自的TraceProvider接入（默认实现了Opentelemetry框架）。

### Tracer（追踪器）
Tracer 是创建和管理 Span 的组件，用于在应用代码中创建和结束 Span。

### SpanConsumer（跨度消费者）
SpanConsumer 是消费 Span 数据的组件，可以帮助用户实现对Spen数据的自定义逻辑，如：处理和导出 Span 数据到不同的后端存储或平台，实时生成Agent执行轨迹等。

#### 使用方式
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
```

### Instrumentation（插桩）
Instrumentation 是自动追踪框架核心组件的机制，通过字节码增强或装饰器的方式，无需修改代码即可实现对核心组件的追踪。AWorld中实现了对以下组件的插桩：

+ EventBusInstrumentor（默认开启）：AWorld内部的消息总线插桩，使Trace能自动为Event执行创建Span，并跟踪Event的父子关系，Event A的处理逻辑中发出了Event B，则Event A的Span是Event B的Span的父Span。
+ AgentInstrumentor（默认开启）: AWorld内部Agent基类插桩，使Trace能自动为Agent执行创建Span，而无需侵入Agent核心代码。
+ ToolInstrumentor（默认开启）：AWorld内部工具基类插桩，使Trace能自动为工具执行创建Span，而无需侵入工具核心代码。
+ LLMModelInstrumentor（默认开启）：AWorld内部大模型调用插桩，使Trace能自动为每次LLM调用创建Span，而无需侵入调用核心代码。
+ ThreadingInstrumentor（默认开启）：对threading库的插桩，使Trace上下文可以在不同线程之间传播，这在多线程执行模式上非常重要。
+ FastAPIInstrumentor：对fastapi库的插桩，适用于在构建在FastAPI之上的web服务的Trace追踪，启用后可以为每次请求的执行创建Span。
+ FlaskInstrumentor：对flask库的插桩，适用于在构建在flask之上的web服务的Trace追踪，启用后可以为每次请求的执行创建Span。
+ RequestsInstrumentor：对requests库的插桩，适用于对外部服务的调用的Trace追踪，启用后可以为每次对外请求创建Span。

#### 启用方式
以FastAPIInstrumentor为例：

```python
from aworld.trace.instrumentation.fastapi import FastAPIInstrumentor

FastAPIInstrumentor().instrument(）
```

## 集成三方观测平台
AWorld默认是基于OpenTelemetry构建的Trace追踪能力，收益于OpenTelemetry的数据传输协议<font style="color:rgb(51, 51, 51);">OTLP的广泛使用，且已成为Trace数据传输协议的事实标准，AWorld可以与广泛的三方观测平台集成，实现Trace数据的可视化展示和分析。</font>

### <font style="color:rgb(51, 51, 51);">使用三方商用平台</font>
#### 使用 Logfire
Pydantic Logfire([https://logfire.pydantic.dev](https://logfire.pydantic.dev/docs/))由Pydantic Validation背后的团队打造，是一种新型的可观测性平台。

要集成 Logfire，您只需要2步：

1. 到Logfire平台中创建项目，申请Write Token，请参阅Logfire官方文档：[https://logfire.pydantic.dev/docs/](https://logfire.pydantic.dev/docs/)，生产环境建议购买付费计划。
2. AWorld应用启动时，配置相应的 Trace Provider 和后端：

```python
from aworld.trace.config import configure, ObservabilityConfig

# 配置 Logfire 集成
config = ObservabilityConfig(
    trace_provider="otlp",
    trace_backends=["logfire"],  # 使用 Logfire 作为后端
    trace_base_url="https://logfire-us.pydantic.dev",  # Logfire API URL
    trace_write_token="your-logfire-write-token"  # Logfire 写入令牌
)

# 应用配置
configure(config)
```



### 使用三方开源平台
#### 使用 Jaeger
Jaeger （[https://www.jaegertracing.io/](https://www.jaegertracing.io/)）是一个分布式追踪平台，由Uber Technologies于 2016 年以开源形式发布，并捐赠给了云原生计算基金会，它是该基金会的毕业项目。

使用Jaeger这样的开源库作为后端观测平台，需要用户自己搭建平台服务，运维成本与难度相对较大。Jaeger后端平台搭建请参阅官方文档：[https://www.jaegertracing.io/docs/2.13/deployment/](https://www.jaegertracing.io/docs/2.13/deployment/)

AWorld应用启动时，配置相应的 Trace Provider 和后端：

```python
from aworld.trace.config import configure, ObservabilityConfig

# 配置 Jaeger 集成
config = ObservabilityConfig(
    trace_provider="otlp",
    trace_backends=["other_otlp"],  # 使用自定义 OTLP 后端
    trace_base_url="http://localhost:4317",  # Jaeger OTLP 接收器地址
    trace_write_token=None  # Jaeger 通常不需要令牌
)

# 应用配置
configure(config)
```



## 如何自定义 Trace 记录
### 使用上下文管理器创建 Span
您可以使用 TraceManager 的 span 方法创建自定义 Span：

```python
import aworld.trace as trace 

with trace.span("custom_operation", attributes={"custom_attr": "value"}):
    # 执行需要追踪的操作
    result = perform_custom_operation()
```



### 装饰器方式创建 Span
您可以使用 func_span 装饰器为函数创建追踪 Span：

```python
import aworld.trace as trace 

# 使用装饰器创建 Span
@trace.func_span("custom_function", attributes={"function_type": "business"},extract_args=True)
def custom_function(param1, param2):
    # 函数实现
    return result
```

其中extract_args参数用于控制是否抽取函数参数列表，写入Span属性中，默认值为False。



### 获取当前 Span 并添加属性
您可以获取当前活动的 Span 并添加自定义属性：

```python
import aworld.trace as trace 

# 获取当前 Span
current_span = trace.get_current_span()

# 添加自定义属性
current_span.set_attribute("custom_key", "custom_value")
```



### 手动管理 Span 生命周期
您也可以手动管理 Span 的生命周期：

```python
import aworld.trace as trace 

# 创建 Span
span = trace.span("manual_operation")

try:
    # 执行需要追踪的操作
    result = perform_operation()
    
    # 设置 Span 状态为成功
    span.set_status("OK")
finally:
    # 结束 Span
    span.end()
```



## 高级功能
### 自动追踪配置
您可以配置自动追踪以打开函数调用的自动追踪功能，开启后，每一个函数调用都会自动创建一个Span。

```python
import aworld.trace as trace

# 配置自动追踪
trace.auto_tracing(
    modules=["my_module", "another_module"],  # 要追踪的模块
    min_duration=0.1  # 最小持续时间（秒），只有超过此时间的函数才会被追踪
)
```



### Trace命名空间隔离
您可能使用AWorld构建了多个不同的应用，不同应用的Trace数据使用不同的命名空间进行隔离。您可以通过环境变量来指令Trace命名空间。

```python
os.environ["MONITOR_SERVICE_NAME"] = "otlp_example"
```



### 集成其他Trace传播协议
AWorld Trace 模块内部默认使用Opentelemetry的W3C传播协议，当使用其他Trace传播协议的应用与AWorld应用交互时，由于协议不一致，trace就无法跨应用传播。为此，AWorld Trace 支持通过自定义传播协议实现不同系统间的追踪上下文传递。传播协议负责在服务间或应用间传递 TraceContext 信息，确保追踪链路的连续性。例如，AWorld支持与Sofa应用的Trace无缝传播，您也可以通过实现自定义传播协议，实现AWorld应用与使用Zipkin做Trace追踪的应用互通Trace上下文。

![画板](https://intranetproxy.alipay.com/skylark/lark/0/2025/jpeg/193971/1765194838474-01995985-87aa-47ea-80af-48a19f18a06f.jpeg)

#### 核心概念
+ Propagator ：传播器接口，定义了从载体提取和注入追踪上下文的方法
+ TraceContext ：追踪上下文，包含 trace_id、span_id、版本等核心信息
+ Carrier ：载体，用于存储和传输追踪上下文的媒介（如 HTTP 头、消息头）

#### 传播协议集成架构
AWorld 使用 CompositePropagator 实现多协议支持，其架构如下：

![画板](https://intranetproxy.alipay.com/skylark/lark/0/2025/jpeg/193971/1765247036744-57c8b857-59da-40a8-86cd-ff9848fab367.jpeg)



#### 实现自定义传播协议
1、继承 Propagator 抽象类

要实现自定义传播协议，首先需要继承 Propagator 抽象类并实现 extract 和 inject 方法：

+ extract：当一个请求从其他传播协议（如：Zipkin B3 传播协议）的应用进入AWorld应用时，根据B3协议抽取请求上下文中的Trace信息（trace_id, span_id, baggage等）。
+ inject：当一个请求从AWorld应用发送到其他传播协议应用时，将trace信息按目标应用的trace传播协议写入请求上下文中。

```python
from aworld.trace.base import Propagator, TraceContext, Carrier
from aworld.logs.util import logger

class CustomTracePropagator(Propagator):
    """
    自定义 Trace 传播协议实现
    """
    
    # 定义协议头名称
    _CUSTOM_TRACE_ID_HEADER = "X-Custom-Trace-ID"
    _CUSTOM_SPAN_ID_HEADER = "X-Custom-Span-ID"
    _CUSTOM_FLAGS_HEADER = "X-Custom-Flags"
    
    def extract(self, carrier: Carrier) -> Optional[TraceContext]:
        """
        从载体中提取自定义追踪上下文
        """
        # 获取自定义协议头
        trace_id = self._get_value(carrier, self._CUSTOM_TRACE_ID_HEADER)
        span_id = self._get_value(carrier, self._CUSTOM_SPAN_ID_HEADER)
        flags = self._get_value(carrier, self._CUSTOM_FLAGS_HEADER) or "01"
        
        logger.debug(f"Extract custom trace context: trace_id={trace_id}, span_id={span_id}")
        
        # 验证必要参数
        if not trace_id or not span_id:
            return None
            
        # 创建并返回 TraceContext
        return TraceContext(
            trace_id=trace_id,
            span_id=span_id,
            trace_flags=flags,
            version="00",  # 自定义版本号
            attributes={
                "custom_protocol": "v1",
                # 可以添加更多自定义属性
            }
        )
    
    def inject(self, trace_context: TraceContext, carrier: Carrier) -> None:
        """
        将追踪上下文注入到载体中
        """
        if not trace_context:
            return
            
        logger.debug(f"Inject custom trace context: trace_id={trace_context.trace_id}, span_id={trace_context.span_id}")
        
        # 注入自定义协议头
        carrier.set(self._CUSTOM_TRACE_ID_HEADER, trace_context.trace_id)
        carrier.set(self._CUSTOM_SPAN_ID_HEADER, trace_context.span_id)
        carrier.set(self._CUSTOM_FLAGS_HEADER, trace_context.trace_flags)
        
        # 可以注入更多自定义属性
        if "custom_protocol" in trace_context.attributes:
            carrier.set("X-Custom-Protocol-Version", trace_context.attributes["custom_protocol"])
```

2、注册自定义传播器

实现自定义传播器后，需要将其注册到全局传播器中：

```python
from aworld.trace.propagator import get_global_trace_propagator, CompositePropagator
from my_custom_propagator import CustomTracePropagator

# 获取当前全局传播器
global_propagator = get_global_trace_propagator()

# 获取现有传播器列表
if isinstance(global_propagator, CompositePropagator):
    existing_propagators = list(global_propagator._propagators)
else:
    existing_propagators = [global_propagator]

# 添加自定义传播器
existing_propagators.append(CustomTracePropagator())

# 创建新的复合传播器
new_composite_propagator = CompositePropagator(existing_propagators)

# 替换全局传播器
from aworld.trace.propagator import _GLOBAL_TRACE_PROPAGATOR
_GLOBAL_TRACE_PROPAGATOR = new_composite_propagator
```

