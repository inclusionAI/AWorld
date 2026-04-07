# AWorld Hooks V2 技术设计文档

**文档版本:** v1.0  
**创建日期:** 2026-04-03  
**作者:** AI Engineering Team  
**状态:** Draft

---

## 目录

1. [设计背景](#1-设计背景)
2. [核心目标](#2-核心目标)
3. [架构设计](#3-架构设计)
4. [详细设计](#4-详细设计)
5. [API设计](#5-api设计)
6. [数据流程](#6-数据流程)
7. [向后兼容性](#7-向后兼容性)
8. [实施计划](#8-实施计划)
9. [风险与缓解](#9-风险与缓解)
10. [附录](#10-附录)

---

## 1. 设计背景

### 1.1 当前问题

AWorld 当前的 hooks 机制存在以下局限性：

**问题 #1: 仅支持 Python 代码**
- 所有 hooks 必须继承 `Hook` 基类
- 终端用户无法通过配置文件添加 hooks
- 需要编写 Python 代码才能扩展功能

**问题 #2: 缺乏关键生命周期点**
- 无会话级 hooks（如 `session_start`）
- 无用户输入级 hooks（如 `user_prompt_submit`）
- 无法响应文件系统变化

**问题 #3: Hook 输出协议简单**
- 仅返回 `Message` 对象
- 无法修改工具调用参数
- 无法动态控制执行流程

### 1.2 设计灵感

Claude Code 的 hooks 机制提供了优秀的参考：

- ✅ **配置驱动**：通过 `settings.json` 配置 hooks
- ✅ **跨语言支持**：Shell/HTTP/JavaScript 多种 hook 类型
- ✅ **丰富的输出协议**：JSON 格式，支持流程控制、参数修改等
- ✅ **完整的生命周期覆盖**：25+ hook events

### 1.3 设计原则

1. **向后兼容**：现有 Python hooks 继续工作
2. **渐进式增强**：分阶段实施，每个阶段都可独立交付
3. **最小破坏性**：优先扩展而非重写
4. **用户友好**：配置简单，文档清晰
5. **性能优先**：Hook 执行不应显著影响性能

---

## 2. 核心目标

### 2.1 主要目标

**目标 #1: 配置化 Hooks（P0）**
- 用户可通过 `.aworld/hooks.yaml` 配置 hooks
- 无需编写 Python 代码

**目标 #2: Shell 命令 Hooks（P0）**
- 支持 Shell 脚本作为 hooks
- 跨语言集成能力

**目标 #3: 丰富的输出协议（P0）**
- Hook 可返回 JSON 格式输出
- 支持流程控制（`continue`, `stop_reason`）
- 支持参数修改（`updated_input`, `updated_output`）

**目标 #4: 新的 Hook Points（P1）**
- `session_start` - 会话初始化
- `user_prompt_submit` - 用户输入后
- `file_changed` - 文件变化（可选）

**目标 #5: 工具参数修改能力（P1）**
- PreToolUse hooks 可修改工具输入
- PostToolUse hooks 可修改工具输出

### 2.2 非目标

以下功能 **不在** 本次设计范围内：

- ❌ 权限系统 hooks（`permission_request`, `permission_denied`）
- ❌ 上下文压缩 hooks（`pre_compact`, `post_compact`）
- ❌ HTTP hooks（可后续扩展）
- ❌ Agent hooks（可后续扩展）

---

## 3. 架构设计

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Configuration Layer                       │
│                                                               │
│  .aworld/hooks.yaml  ◄────┐                                  │
│                           │ YAML config                      │
│  Python @hook decorator ◄─┤                                  │
│                           │ Python code                      │
│  HookFactory.register() ◄─┘                                  │
│                                                               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ load_config_hooks()
                            │ + Factory.hooks()
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   Hook Registry Layer                        │
│                                                               │
│   ┌─────────────────────────────────────────────┐           │
│   │           HookFactory (Modified)            │           │
│   │                                             │           │
│   │  _cls: dict[str, Type[Hook]]  # Python hooks│           │
│   │  _config_hooks: dict          # Config hooks│           │
│   │                                             │           │
│   │  hooks(point) -> List[Hook]:              │           │
│   │    python_hooks = [...]                    │           │
│   │    config_hooks = load_config_hooks()      │           │
│   │    return merge(python_hooks, config_hooks) │           │
│   └─────────────────────────────────────────────┘           │
│                                                               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ .hooks(point)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   Hook Execution Layer                       │
│                                                               │
│   ┌────────────────────────────────────────────┐            │
│   │        run_hooks(context, point, ...)      │            │
│   │                                            │            │
│   │  for hook in hooks:                        │            │
│   │    msg = await hook.exec(message, context) │            │
│   │    yield msg                                │            │
│   └────────────────────────────────────────────┘            │
│                                                               │
│   Hook Types:                                                │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│   │ PythonHook   │  │ CommandHook  │  │ CallbackHook │     │
│   │ (原生)       │  │ Wrapper      │  │ Wrapper      │     │
│   └──────────────┘  └──────────────┘  └──────────────┘     │
│          ↓                  ↓                  ↓            │
│       .exec()            .exec()            .exec()         │
│                                                               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ Message
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│                                                               │
│   TaskRunner  ◄───┐                                          │
│   Handler     ◄───┤  Invoke hooks at lifecycle points       │
│   Executor    ◄───┘                                          │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 核心组件

#### 3.2.1 CommandHookWrapper

Shell 命令 hook 的适配器类，将 Shell 命令包装成 Hook 接口。

**职责：**
- 解析配置文件中的 command hook
- 执行 Shell 命令
- 解析 JSON 输出
- 将输出应用到 Message 对象

**接口：**
```python
class CommandHookWrapper(Hook):
    def __init__(self, config: dict)
    def point(self) -> str
    async def exec(self, message: Message, context: Context) -> Message
```

#### 3.2.2 HookJSONOutput

Hook 输出协议的数据类，定义了 hook 可以返回的所有字段。

**职责：**
- 定义 JSON 输出格式
- 验证输出字段
- 提供类型安全的访问

**接口：**
```python
@dataclass
class HookJSONOutput:
    continue_: bool = True
    stop_reason: str | None = None
    system_message: str | None = None
    additional_context: str | None = None
    updated_input: dict | None = None
    updated_output: dict | None = None
    permission_decision: Literal['allow', 'deny'] | None = None
    watch_paths: list[str] | None = None
```

#### 3.2.3 HookFactory（修改）

Hook 注册中心，负责管理所有 hook 的注册和检索。

**职责：**
- 管理 Python hook 注册（原有功能）
- 加载配置文件 hook（新增功能）
- 合并两种 hook 来源

**接口：**
```python
class HookManager(Factory):
    @staticmethod
    def load_config_hooks(config_path: str) -> dict
    
    def hooks(self, name: str = None) -> Dict[str, List[Hook]]
```

---

## 4. 详细设计

### 4.1 配置文件格式

#### 4.1.1 YAML Schema

```yaml
# .aworld/hooks.yaml

# 配置文件版本
version: "1.0"

# Hooks 定义
hooks:
  # Hook point name (e.g., session_start, user_prompt_submit, pre_tool_use)
  <hook_point_name>:
    - name: <hook_name>            # 必填：Hook 名称
      type: <hook_type>             # 必填：hook 类型 (command, callback)
      enabled: <boolean>            # 可选：是否启用，默认 true
      
      # type: command 时的字段
      command: <shell_command>      # Shell 命令
      timeout: <milliseconds>       # 可选：超时时间(ms)，默认 600000
      shell: <shell_path>           # 可选：指定 shell，默认 /bin/bash
      
      # type: callback 时的字段
      callback: <python_path>       # Python 函数路径 "module:function"
      
      # 通用字段
      description: <string>         # 可选：描述
```

#### 4.1.2 配置示例

```yaml
version: "1.0"

hooks:
  # 会话开始时加载项目上下文
  session_start:
    - name: "load-project-context"
      type: command
      command: "cat .aworld/project_context.md"
      enabled: true
      description: "Load project context at session start"
  
  # 用户输入后扩展文件引用
  user_prompt_submit:
    - name: "expand-file-references"
      type: callback
      callback: "aworld_cli.hooks.file_parser:expand"
      enabled: true
    
    - name: "sync-session-log"
      type: command
      command: "python scripts/sync_log.py"
      timeout: 10000
  
  # 工具调用前修改参数
  pre_tool_use:
    - name: "path-rewriter"
      type: command
      command: "python scripts/rewrite_path.py"
      enabled: true
  
  # 文件变化时自动重新加载
  file_changed:
    - name: "auto-reload-config"
      type: command
      command: "aworld-cli reload"
      enabled: true
      description: "Reload config when files change"
```

### 4.2 Hook 输出协议

#### 4.2.1 JSON 输出格式

**基本格式：**
```json
{
  "continue": true,
  "stopReason": null,
  "systemMessage": null,
  "additionalContext": null,
  "updatedInput": null,
  "updatedOutput": null,
  "permissionDecision": null,
  "watchPaths": null,
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    // Hook 特定的输出字段
  }
}
```

**字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| `continue` | boolean | 否 | 是否继续执行（默认 true） |
| `stopReason` | string | 否 | 停止原因（当 continue=false 时） |
| `systemMessage` | string | 否 | 系统消息（显示给用户） |
| `additionalContext` | string | 否 | 额外上下文（注入到对话） |
| `updatedInput` | object | 否 | 修改后的输入（PreToolUse）|
| `updatedOutput` | object | 否 | 修改后的输出（PostToolUse）|
| `permissionDecision` | enum | 否 | 权限决策 (allow/deny) |
| `watchPaths` | array | 否 | 监听的文件路径列表 |
| `hookSpecificOutput` | object | 否 | Hook 特定的输出 |

#### 4.2.2 Hook 特定输出

**SessionStart：**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "项目背景：...",
    "watchPaths": ["/path/to/config", "/path/to/docs"]
  }
}
```

**UserPromptSubmit：**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "用户历史：..."
  }
}
```

**PreToolUse：**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "updatedInput": {
      "file_path": "/updated/path/to/file"
    },
    "additionalContext": "注意：该文件包含敏感数据"
  }
}
```

**PostToolUse：**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "updatedOutput": {
      "result": "formatted result"
    },
    "additionalContext": "执行耗时：1.2s"
  }
}
```

### 4.3 环境变量注入

CommandHook 执行时，以下环境变量会被自动注入：

| 环境变量 | 说明 | 示例值 |
|---------|------|--------|
| `AWORLD_SESSION_ID` | 会话 ID | `abc123...` |
| `AWORLD_TASK_ID` | 任务 ID | `task-456...` |
| `AWORLD_CWD` | 当前工作目录 | `/path/to/project` |
| `AWORLD_HOOK_POINT` | Hook 点名称 | `user_prompt_submit` |
| `AWORLD_MESSAGE_JSON` | Message 对象的 JSON 序列化 | `{...}` |
| `AWORLD_CONTEXT_JSON` | Context 对象的关键信息 | `{...}` |

**Shell Hook 示例：**
```bash
#!/bin/bash
# scripts/load_context.sh

# 读取环境变量
SESSION_ID=$AWORLD_SESSION_ID
CWD=$AWORLD_CWD

# 加载项目上下文
CONTEXT=$(cat .aworld/project_context.md)

# 返回 JSON
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "$CONTEXT",
    "watchPaths": ["$CWD/.aworld/project_context.md"]
  }
}
EOF
```

---

## 5. API 设计

### 5.1 CommandHookWrapper

```python
class CommandHookWrapper(Hook):
    """Shell 命令 Hook 适配器"""
    
    def __init__(self, config: dict):
        """
        Args:
            config: Hook 配置字典
                - name: Hook 名称
                - hook_point: Hook 点
                - command: Shell 命令
                - timeout: 超时时间(ms)
                - shell: Shell 路径（可选）
        """
        self._config = config
        self._hook_point = config['hook_point']
        self._command = config['command']
        self._timeout = config.get('timeout', 600000)
        self._shell = config.get('shell', '/bin/bash')
    
    def point(self) -> str:
        """返回 hook 点名称"""
        return self._hook_point
    
    async def exec(
        self, 
        message: Message, 
        context: Context
    ) -> Message:
        """
        执行 Shell 命令并解析输出
        
        Args:
            message: 输入消息
            context: 执行上下文
            
        Returns:
            处理后的消息对象
            
        Raises:
            asyncio.TimeoutError: 命令执行超时
            subprocess.CalledProcessError: 命令执行失败
        """
        # 1. 构造环境变量
        env = self._build_env(message, context)
        
        # 2. 执行命令
        proc = await asyncio.create_subprocess_shell(
            self._command,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True,
            executable=self._shell
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout / 1000
            )
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning(f"Hook {self._config['name']} timeout after {self._timeout}ms")
            return message
        
        # 3. 解析输出
        output = self._parse_output(stdout.decode().strip())
        
        # 4. 应用输出到消息
        return self._apply_output(message, output)
    
    def _build_env(
        self, 
        message: Message, 
        context: Context
    ) -> dict:
        """构造环境变量"""
        return {
            **os.environ,
            'AWORLD_SESSION_ID': context.session_id,
            'AWORLD_TASK_ID': context.task_id,
            'AWORLD_CWD': os.getcwd(),
            'AWORLD_HOOK_POINT': self._hook_point,
            'AWORLD_MESSAGE_JSON': json.dumps(message.to_dict()),
            'AWORLD_CONTEXT_JSON': json.dumps({
                'session_id': context.session_id,
                'task_id': context.task_id,
                # ... 其他上下文信息
            })
        }
    
    def _parse_output(self, output_text: str) -> HookJSONOutput | str:
        """解析 hook 输出"""
        if output_text.startswith('{'):
            try:
                return HookJSONOutput.from_json(output_text)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON output from hook: {output_text[:100]}")
                return output_text
        else:
            return output_text
    
    def _apply_output(
        self, 
        message: Message, 
        output: HookJSONOutput | str
    ) -> Message:
        """将 hook 输出应用到消息"""
        if isinstance(output, str):
            # 纯文本输出，作为额外上下文
            message.headers['additional_context'] = output
            return message
        
        # JSON 输出，应用各个字段
        if output.additional_context:
            message.headers['additional_context'] = output.additional_context
        
        if output.updated_input and hasattr(message.payload, 'update'):
            message.payload.update(output.updated_input)
        
        if output.system_message:
            message.headers['system_message'] = output.system_message
        
        if not output.continue_:
            message.headers['prevent_continuation'] = True
            message.headers['stop_reason'] = output.stop_reason
        
        return message
```

### 5.2 HookFactory（修改）

```python
class HookManager(Factory):
    """Hook 注册管理器（修改）"""
    
    _cls: dict[str, Type[Hook]] = {}        # Python hooks（原有）
    _config_hooks_cache: dict = {}          # 配置 hooks 缓存（新增）
    _config_file_mtime: float = 0           # 配置文件修改时间（新增）
    
    @staticmethod
    def load_config_hooks(
        config_path: str = ".aworld/hooks.yaml"
    ) -> dict[str, list[Hook]]:
        """
        从配置文件加载 hooks
        
        Args:
            config_path: 配置文件路径
            
        Returns:
            Hook 字典 {hook_point: [Hook, ...]}
        """
        if not os.path.exists(config_path):
            logger.debug(f"Hook config file not found: {config_path}")
            return {}
        
        # 检查缓存
        current_mtime = os.path.getmtime(config_path)
        if (HookManager._config_hooks_cache and 
            current_mtime == HookManager._config_file_mtime):
            return HookManager._config_hooks_cache
        
        # 加载配置
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        hooks = {}
        for event_name, hook_configs in config.get('hooks', {}).items():
            hooks[event_name] = []
            
            for hc in hook_configs:
                if not hc.get('enabled', True):
                    continue
                
                # 添加 hook_point 到配置
                hc['hook_point'] = event_name
                
                # 根据类型创建 hook
                if hc['type'] == 'command':
                    hooks[event_name].append(CommandHookWrapper(hc))
                elif hc['type'] == 'callback':
                    hooks[event_name].append(CallbackHookWrapper(hc))
                else:
                    logger.warning(f"Unknown hook type: {hc['type']}")
        
        # 更新缓存
        HookManager._config_hooks_cache = hooks
        HookManager._config_file_mtime = current_mtime
        
        logger.info(f"Loaded {sum(len(v) for v in hooks.values())} hooks from config")
        return hooks
    
    def hooks(self, name: str = None) -> Dict[str, List[Hook]]:
        """
        获取所有 hooks（修改）
        
        Args:
            name: Hook 点名称（可选），如果指定则只返回该点的 hooks
            
        Returns:
            Hook 字典 {hook_point: [Hook, ...]}
        """
        # 1. 获取 Python hooks（原有逻辑）
        vals = list(filter(lambda s: not s.startswith('__'), dir(HookPoint)))
        results = {val.lower(): [] for val in vals}
        
        for k, v in self._cls.items():
            hook = v()
            if name and hook.point() != name:
               continue
            results.get(hook.point(), []).append(hook)
        
        # 2. 加载配置 hooks（新增逻辑）
        config_hooks = self.load_config_hooks()
        
        # 3. 合并两种 hooks
        for point, hooks in config_hooks.items():
            if point in results:
                results[point].extend(hooks)
            else:
                results[point] = hooks
        
        # 4. 按优先级排序（Python hooks 优先执行）
        # （可选，当前设计中配置 hooks 后执行）
        
        return results
```

### 5.3 新 Hook Points

```python
# aworld/runners/hook/hooks.py（修改）

class HookPoint:
    # 原有 hook points
    START = "start"
    FINISHED = "finished"
    ERROR = "error"
    PRE_LLM_CALL = "pre_llm_call"
    POST_LLM_CALL = "post_llm_call"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    OUTPUT_PROCESS = "output_process"
    PRE_TASK_CALL = "pre_task_call"
    POST_TASK_CALL = "post_task_call"
    
    # 新增 hook points（P0）
    SESSION_START = "session_start"            # 会话开始
    USER_PROMPT_SUBMIT = "user_prompt_submit"  # 用户输入后
    
    # 新增 hook points（P1）
    FILE_CHANGED = "file_changed"              # 文件变化（可选）
    
# 对应的 Hook 基类
class SessionStartHook(Hook):
    """会话开始 hook"""
    def point(self):
        return HookPoint.SESSION_START

class UserPromptSubmitHook(Hook):
    """用户输入后 hook"""
    def point(self):
        return HookPoint.USER_PROMPT_SUBMIT

class FileChangedHook(Hook):
    """文件变化 hook"""
    def point(self):
        return HookPoint.FILE_CHANGED
```

---

## 6. 数据流程

### 6.1 Hook 执行流程

```
┌─────────────────────────────────────────────────────────────┐
│                   Application Code                           │
│  (TaskRunner/Handler/Executor)                              │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │ run_hooks(context, "session_start", ...)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                   HookFactory.hooks("session_start")         │
│                                                               │
│  1. Get Python hooks from _cls registry                     │
│  2. Load config hooks from .aworld/hooks.yaml               │
│  3. Merge and return unified hook list                      │
│                                                               │
│  Returns: [PythonHook1, CommandHook1, CallbackHook1, ...]   │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                   run_hooks Loop                             │
│                                                               │
│  for hook in hooks:                                          │
│    ┌─────────────────────────────────────────────┐          │
│    │  msg = await hook.exec(message, context)    │          │
│    └─────────────────────────────────────────────┘          │
│                      │                                        │
│                      ▼                                        │
│    ┌─────────────────────────────────────────────┐          │
│    │  Hook Type Dispatch:                        │          │
│    │  - PythonHook: Execute Python code          │          │
│    │  - CommandHook: Execute shell command       │          │
│    │  - CallbackHook: Execute Python function    │          │
│    └─────────────────────────────────────────────┘          │
│                      │                                        │
│                      ▼                                        │
│    ┌─────────────────────────────────────────────┐          │
│    │  Parse Hook Output:                         │          │
│    │  - JSON → HookJSONOutput                    │          │
│    │  - Plain text → additional_context          │          │
│    └─────────────────────────────────────────────┘          │
│                      │                                        │
│                      ▼                                        │
│    ┌─────────────────────────────────────────────┐          │
│    │  Apply Output to Message:                   │          │
│    │  - additional_context                        │          │
│    │  - updated_input/output                      │          │
│    │  - system_message                            │          │
│    │  - prevent_continuation                      │          │
│    └─────────────────────────────────────────────┘          │
│                      │                                        │
│                      ▼                                        │
│    yield modified message                                    │
│                                                               │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │ modified message(s)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                   Application Code                           │
│  (Continue with modified message)                           │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 SessionStart Hook 示例流程

```
User starts session
       │
       ▼
┌──────────────────────────────────────────┐
│  LocalAgentExecutor.execute()            │
│                                          │
│  if self.is_first_turn:                  │
│    run_hooks(ctx, "session_start", ...)  │
└────────────┬─────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────┐
│  HookFactory.hooks("session_start")      │
│                                          │
│  Returns:                                │
│  - CommandHook("load-context")           │
│  - CommandHook("setup-watchers")         │
└────────────┬─────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────┐
│  Hook 1: load-context                    │
│  command: "cat .aworld/context.md"       │
│                                          │
│  Returns JSON:                           │
│  {                                       │
│    "hookSpecificOutput": {               │
│      "hookEventName": "SessionStart",    │
│      "additionalContext": "项目背景..."  │
│    }                                     │
│  }                                       │
└────────────┬─────────────────────────────┘
             │
             │ additionalContext added to message
             ▼
┌──────────────────────────────────────────┐
│  Hook 2: setup-watchers                  │
│  command: "python scripts/watch.py"      │
│                                          │
│  Returns JSON:                           │
│  {                                       │
│    "hookSpecificOutput": {               │
│      "hookEventName": "SessionStart",    │
│      "watchPaths": [                     │
│        ".aworld/config.yaml",            │
│        "docs/"                           │
│      ]                                   │
│    }                                     │
│  }                                       │
└────────────┬─────────────────────────────┘
             │
             │ watchPaths stored in context
             ▼
┌──────────────────────────────────────────┐
│  Message with:                           │
│  - additionalContext: "项目背景..."      │
│  - watchPaths: [".aworld/config.yaml"]   │
│                                          │
│  → Continue session with enhanced context│
└──────────────────────────────────────────┘
```

### 6.3 UserPromptSubmit Hook 示例流程

```
User submits: "分析 @document.txt"
       │
       ▼
┌──────────────────────────────────────────┐
│  LocalAgentExecutor.execute()            │
│                                          │
│  run_hooks(ctx, "user_prompt_submit", ...)│
└────────────┬─────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────┐
│  HookFactory.hooks("user_prompt_submit") │
│                                          │
│  Returns:                                │
│  - CommandHook("expand-files")           │
└────────────┬─────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────┐
│  Hook: expand-files                      │
│  command: "python expand.py"             │
│                                          │
│  Env:                                    │
│  AWORLD_MESSAGE_JSON = {                 │
│    "payload": "分析 @document.txt"       │
│  }                                       │
│                                          │
│  Script logic:                           │
│  - Parse "@document.txt"                 │
│  - Read file content                     │
│  - Return expanded text                  │
│                                          │
│  Returns JSON:                           │
│  {                                       │
│    "hookSpecificOutput": {               │
│      "hookEventName": "UserPromptSubmit",│
│      "additionalContext": "[document.txt content]"│
│    }                                     │
│  }                                       │
└────────────┬─────────────────────────────┘
             │
             │ additionalContext added
             ▼
┌──────────────────────────────────────────┐
│  Modified user message:                  │
│  "分析 @document.txt                     │
│   [document.txt content here]"           │
│                                          │
│  → Agent receives expanded message       │
└──────────────────────────────────────────┘
```

---

## 7. 向后兼容性

### 7.1 兼容性保证

**保证 #1: Python Hooks 继续工作**
- 所有现有的 `@HookFactory.register` 装饰的 hooks 继续工作
- Hook 执行顺序：Python hooks → Config hooks
- 无需修改现有代码

**保证 #2: API 不变**
- `run_hooks()` 函数签名不变
- `Hook.exec()` 方法签名不变
- `HookFactory.hooks()` 返回值类型不变

**保证 #3: 配置可选**
- 如果 `.aworld/hooks.yaml` 不存在，系统继续正常工作
- 配置文件可以为空

### 7.2 迁移路径

**Phase 1: 并行运行**
- Python hooks 和 Config hooks 同时存在
- 用户可以逐步将 Python hooks 迁移到配置文件

**Phase 2: 迁移工具**
```bash
# 自动生成等价的配置文件
aworld-cli hooks migrate --output .aworld/hooks.yaml

# 检查迁移结果
aworld-cli hooks validate .aworld/hooks.yaml
```

**Phase 3: 完全切换（可选）**
```bash
# 用户可以选择禁用 Python hooks
aworld-cli config set hooks.use_python_hooks false
```

### 7.3 兼容性测试

**测试矩阵：**

| 场景 | Python Hooks | Config Hooks | 预期结果 |
|-----|--------------|--------------|----------|
| 只有 Python hooks | ✅ | ❌ | 正常工作 |
| 只有 Config hooks | ❌ | ✅ | 正常工作 |
| 两者都有 | ✅ | ✅ | 都执行，Python 先执行 |
| 两者都无 | ❌ | ❌ | 正常工作，无 hooks |
| 配置文件不存在 | ✅ | ❌ | Python hooks 正常工作 |
| 配置文件格式错误 | ✅ | ❌ | Python hooks 正常工作，config 忽略 |

**测试用例：**
```python
# tests/test_hooks_compatibility.py

async def test_python_hooks_still_work():
    """测试现有 Python hooks 继续工作"""
    @HookFactory.register(name="TestHook")
    class TestHook(StartHook):
        async def exec(self, message, context):
            message.payload = "modified"
            return message
    
    hooks = HookFactory.hooks("start")
    assert len(hooks["start"]) >= 1
    assert any(isinstance(h, TestHook) for h in hooks["start"])

async def test_config_hooks_work():
    """测试配置文件 hooks 正常工作"""
    config_path = create_temp_config({
        "hooks": {
            "start": [{
                "name": "test",
                "type": "command",
                "command": "echo 'test'"
            }]
        }
    })
    
    hooks = HookFactory.load_config_hooks(config_path)
    assert "start" in hooks
    assert len(hooks["start"]) == 1
    assert isinstance(hooks["start"][0], CommandHookWrapper)

async def test_both_hooks_execute():
    """测试两种 hooks 都执行"""
    # 设置 Python hook
    @HookFactory.register(name="PythonTestHook")
    class PythonTestHook(StartHook):
        async def exec(self, message, context):
            message.headers["python_hook"] = True
            return message
    
    # 设置 Config hook
    config_path = create_temp_config({
        "hooks": {
            "start": [{
                "name": "config_test",
                "type": "command",
                "command": "echo '{\"additionalContext\": \"config_hook\"}'"
            }]
        }
    })
    
    # 执行 hooks
    message = Message(...)
    results = []
    async for msg in run_hooks(context, "start", "test"):
        results.append(msg)
    
    # 验证两个 hooks 都执行了
    assert len(results) >= 2
    assert any("python_hook" in msg.headers for msg in results)
    assert any("additional_context" in msg.headers for msg in results)
```

---

## 8. 实施计划

### 8.1 阶段划分

**Phase 1: 核心基础设施（Week 1-2）**

目标：实现 CommandHook 和配置加载

**Week 1:**
- [ ] 创建 `aworld/runners/hook/v2/` 目录结构
- [ ] 实现 `HookJSONOutput` 数据类
- [ ] 实现 `CommandHookWrapper` 类
- [ ] 单元测试（CommandHook 执行逻辑）

**Week 2:**
- [ ] 修改 `HookFactory` 添加 `load_config_hooks()` 方法
- [ ] 修改 `HookFactory.hooks()` 合并逻辑
- [ ] 定义 YAML schema
- [ ] 集成测试（配置加载 + hook 执行）

**Deliverables:**
- ✅ CommandHookWrapper 可执行 Shell 命令
- ✅ HookFactory 可加载配置文件
- ✅ 配置文件和 Python hooks 可并存

---

**Phase 2: 新 Hook Points（Week 3-4）**

目标：添加高价值 hook points

**Week 3:**
- [ ] 在 `HookPoint` 类添加 `SESSION_START`, `USER_PROMPT_SUBMIT`
- [ ] 在 `LocalAgentExecutor` 调用 `session_start` hook
- [ ] 在 `LocalAgentExecutor` 调用 `user_prompt_submit` hook
- [ ] 端到端测试（会话初始化 + 用户输入）

**Week 4:**
- [ ] 增强 `PreToolUse` hook 支持 `updatedInput`
- [ ] 增强 `PostToolUse` hook 支持 `updatedOutput`
- [ ] 编写 Shell hook 示例脚本
- [ ] 集成测试（工具参数修改）

**Deliverables:**
- ✅ session_start hook 可在会话初始化时执行
- ✅ user_prompt_submit hook 可在用户输入后执行
- ✅ PreToolUse hook 可修改工具输入
- ✅ PostToolUse hook 可修改工具输出

---

**Phase 3: 高级特性（Week 5）**

目标：文件监听和向后兼容

**Week 5:**
- [ ] 实现 `FileChanged` hook
- [ ] 集成 `watchdog` 库进行文件监听
- [ ] 实现向后兼容测试套件
- [ ] 性能基准测试

**Deliverables:**
- ✅ file_changed hook 可响应文件变化
- ✅ 所有现有 Python hooks 继续工作
- ✅ 性能无显著下降

---

**Phase 4: 工具和文档（Week 6）**

目标：用户体验和文档

**Week 6:**
- [ ] 迁移工具 `aworld-cli hooks migrate`
- [ ] 验证工具 `aworld-cli hooks validate`
- [ ] 调试工具 `aworld-cli hooks test`
- [ ] 用户文档编写
- [ ] API 文档生成
- [ ] 示例项目创建

**Deliverables:**
- ✅ 用户文档完整
- ✅ 迁移工具可用
- ✅ 示例项目可运行

---

### 8.2 里程碑

| 里程碑 | 日期 | 标志 |
|-------|------|------|
| M1: 核心基础完成 | Week 2 结束 | CommandHook 可执行，配置可加载 |
| M2: 新 hooks 可用 | Week 4 结束 | session_start 和 user_prompt_submit 可用 |
| M3: 功能完整 | Week 5 结束 | 所有 P0+P1 特性完成 |
| M4: 文档完整 | Week 6 结束 | 用户可独立使用 |

### 8.3 验收标准

**M1 验收标准：**
- [ ] CommandHookWrapper 可执行 Shell 命令
- [ ] 可解析 JSON 输出
- [ ] 可解析纯文本输出
- [ ] HookFactory 可加载 `.aworld/hooks.yaml`
- [ ] Python hooks 和 Config hooks 可并存
- [ ] 单元测试覆盖率 > 80%

**M2 验收标准：**
- [ ] session_start hook 在会话初始化时执行
- [ ] user_prompt_submit hook 在用户输入后执行
- [ ] additionalContext 可注入到对话
- [ ] updatedInput 可修改工具参数
- [ ] 端到端测试通过

**M3 验收标准：**
- [ ] file_changed hook 可响应文件变化
- [ ] 向后兼容测试全部通过
- [ ] 性能基准测试通过（hooks 执行不超过 100ms）
- [ ] 集成测试覆盖率 > 70%

**M4 验收标准：**
- [ ] 用户文档完整（安装、配置、使用、示例）
- [ ] API 文档完整（所有公共 API 有文档字符串）
- [ ] 迁移工具可用
- [ ] 示例项目可运行
- [ ] Release notes 完成

---

## 9. 风险与缓解

### 9.1 技术风险

**风险 #1: 破坏现有 Python hooks**

**影响:** ⭐⭐⭐⭐⭐ 高  
**概率:** ⭐⭐ 低

**缓解措施:**
- ✅ 充分的单元测试（覆盖所有现有 hook 类型）
- ✅ 向后兼容测试套件
- ✅ 渐进式部署（先在测试环境验证）
- ✅ 回滚计划（保留原有代码分支）

---

**风险 #2: Shell hook 安全风险**

**影响:** ⭐⭐⭐⭐ 高  
**概率:** ⭐⭐⭐ 中

**缓解措施:**
- ✅ 限制 Shell hook 执行权限（不允许 sudo）
- ✅ 超时控制（默认 600 秒）
- ✅ 环境变量白名单
- ✅ 用户文档明确安全注意事项
- ⚠️ 未来：Sandbox 执行（Docker/VM 隔离）

---

**风险 #3: 配置文件格式变更**

**影响:** ⭐⭐⭐ 中  
**概率:** ⭐⭐⭐ 中

**缓解措施:**
- ✅ 版本化 schema（`version: "1.0"`）
- ✅ 向后兼容的 schema 演化
- ✅ 迁移工具自动升级配置
- ✅ 详细的版本变更日志

---

**风险 #4: 性能下降**

**影响:** ⭐⭐⭐ 中  
**概率:** ⭐⭐ 低

**缓解措施:**
- ✅ Hook 执行缓存（配置文件缓存）
- ✅ 异步并发执行（多个 hooks 可并发）
- ✅ 性能基准测试（持续监控）
- ✅ 超时控制（防止阻塞）

---

**风险 #5: 学习曲线陡峭**

**影响:** ⭐⭐ 低  
**概率:** ⭐⭐⭐ 中

**缓解措施:**
- ✅ 详细的用户文档
- ✅ 丰富的示例代码
- ✅ 视频教程
- ✅ 常见问题 FAQ

---

### 9.2 项目风险

**风险 #1: 需求变更**

**影响:** ⭐⭐⭐ 中  
**概率:** ⭐⭐⭐ 中

**缓解措施:**
- ✅ 灵活的架构设计（易于扩展）
- ✅ 迭代式开发（每周交付）
- ✅ 定期与用户沟通

**风险 #2: 资源不足**

**影响:** ⭐⭐⭐⭐ 高  
**概率:** ⭐⭐ 低

**缓解措施:**
- ✅ 优先级明确（P0 > P1 > P2）
- ✅ MVP 快速交付
- ✅ 自动化测试减少人力

---

## 10. 附录

### 10.1 参考文档

- Claude Code Hooks 源码: `src/types/hooks.ts`
- AWorld 现有 Hooks 实现: `aworld/runners/hook/`
- YAML Schema 规范 (yaml.org)
- Python asyncio 文档 (Python 3 standard library)

### 10.2 术语表

| 术语 | 定义 |
|-----|------|
| Hook | 在特定生命周期点执行的可插拔逻辑 |
| Hook Point | Hook 的触发时机（如 session_start） |
| CommandHook | Shell 命令类型的 hook |
| CallbackHook | Python 函数类型的 hook |
| HookJSONOutput | Hook 输出的 JSON 格式协议 |
| HookFactory | Hook 注册和管理中心 |
| run_hooks | Hook 执行器函数 |

### 10.3 代码结构

```
aworld/
├── runners/
│   ├── hook/
│   │   ├── v2/                          # 新增
│   │   │   ├── __init__.py
│   │   │   ├── wrappers.py              # CommandHookWrapper, CallbackHookWrapper
│   │   │   ├── protocol.py              # HookJSONOutput
│   │   │   └── utils.py                 # 辅助函数
│   │   ├── hooks.py                     # HookPoint（修改）
│   │   ├── hook_factory.py              # HookFactory（修改）
│   │   └── utils.py                     # run_hooks（保持）
│   └── ...
│
└── ...

aworld-cli/
├── src/aworld_cli/
│   ├── executors/
│   │   ├── local.py                     # 调用新 hooks（修改）
│   │   └── ...
│   └── commands/
│       └── hooks.py                     # 新增 hooks 命令
│
└── ...

tests/
├── test_hooks_v2/                       # 新增测试
│   ├── test_command_hook.py
│   ├── test_hook_factory.py
│   ├── test_compatibility.py
│   └── test_integration.py
│
└── ...

docs/
├── user/
│   └── hooks-guide.md                   # 用户文档
└── api/
    └── hooks-api.md                     # API 文档
```

---

**文档状态：** Draft  
**下一步：** Review & Approval

---
