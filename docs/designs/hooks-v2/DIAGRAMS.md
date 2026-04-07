# AWorld Hooks V2 - UML Diagrams

**文档版本:** v1.0  
**创建日期:** 2026-04-03  
**作者:** AI Engineering Team

---

## 目录

1. [类图（Class Diagrams）](#1-类图)
2. [序列图（Sequence Diagrams）](#2-序列图)
3. [状态图（State Diagrams）](#3-状态图)
4. [活动图（Activity Diagrams）](#4-活动图)

---

## 1. 类图（Class Diagrams）

### 1.1 核心类结构

```
┌─────────────────────────────────────────────────────────────┐
│                         Hook (ABC)                          │
│  Abstract base class for all hooks                          │
├─────────────────────────────────────────────────────────────┤
│  + point() -> str                                           │
│  + exec(message: Message, context: Context) -> Message      │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │ inheritance
         ┌──────────────┼──────────────────────────────┐
         │              │                              │
         ▼              ▼                              ▼
┌────────────────┐ ┌────────────────┐ ┌──────────────────────┐
│  PythonHook    │ │ CommandHook    │ │  CallbackHook        │
│  (原生)        │ │ Wrapper        │ │  Wrapper             │
├────────────────┤ ├────────────────┤ ├──────────────────────┤
│- _point: str   │ │- _config: dict │ │- _config: dict       │
│                │ │- _command: str │ │- _callback: callable │
│                │ │- _timeout: int │ │                      │
├────────────────┤ ├────────────────┤ ├──────────────────────┤
│+ point()       │ │+ point()       │ │+ point()             │
│+ exec()        │ │+ exec()        │ │+ exec()              │
│                │ │- _build_env()  │ │                      │
│                │ │- _parse_output()│ │                      │
│                │ │- _apply_output()│ │                      │
└────────────────┘ └────────────────┘ └──────────────────────┘
```

### 1.2 Hook Factory 类结构

```
┌─────────────────────────────────────────────────────────────┐
│                      HookManager (Factory)                   │
│  Manages hook registration and retrieval                    │
├─────────────────────────────────────────────────────────────┤
│  - _cls: dict[str, Type[Hook]]         # Python hooks       │
│  - _config_hooks_cache: dict           # Config hooks cache │
│  - _config_file_mtime: float           # Config file mtime  │
├─────────────────────────────────────────────────────────────┤
│  + register(name: str) -> decorator    # Decorator          │
│  + hooks(name: str = None) -> dict     # Get all hooks      │
│  + load_config_hooks(path: str) -> dict # Load config       │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ uses
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Hook (interface)                        │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 Hook 输出协议类结构

```
┌─────────────────────────────────────────────────────────────┐
│                    HookJSONOutput (dataclass)                │
│  Defines the JSON output protocol for hooks                 │
├─────────────────────────────────────────────────────────────┤
│  + continue_: bool = True              # Continue execution  │
│  + stop_reason: str | None = None      # Stop reason         │
│  + system_message: str | None = None   # System message      │
│  + additional_context: str | None      # Additional context  │
│  + updated_input: dict | None          # Modified input      │
│  + updated_output: dict | None         # Modified output     │
│  + permission_decision: str | None     # Permission decision │
│  + watch_paths: list[str] | None       # Watch paths         │
├─────────────────────────────────────────────────────────────┤
│  + from_json(json_str: str) -> HookJSONOutput               │
│  + to_dict() -> dict                                        │
│  + validate() -> bool                                       │
└─────────────────────────────────────────────────────────────┘
```

### 1.4 完整类关系图

```
                    ┌──────────────────┐
                    │   HookFactory    │
                    │  (Singleton)     │
                    └────────┬─────────┘
                             │
                             │ manages
                             ▼
                    ┌──────────────────┐
                    │   Hook (ABC)     │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
    │ StartHook   │  │PreLLMCall   │  │PreToolUse   │
    │             │  │Hook         │  │Hook         │
    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
           │                │                │
           │                │                │
    ┌──────▼──────────────────────────────────▼──────┐
    │        Concrete Hook Implementations            │
    ├─────────────────────────────────────────────────┤
    │  - FileParseHook                                │
    │  - ContextLoadHook                              │
    │  - ... (user-defined Python hooks)              │
    └─────────────────────────────────────────────────┘
           │                │                │
           │                │                │
    ┌──────▼──────────────────────────────────▼──────┐
    │         Hook Wrappers (Adapters)                │
    ├─────────────────────────────────────────────────┤
    │  - CommandHookWrapper                           │
    │  - CallbackHookWrapper                          │
    │  - HttpHookWrapper (future)                     │
    └─────────────────────────────────────────────────┘
                             │
                             │ uses
                             ▼
                    ┌──────────────────┐
                    │ HookJSONOutput   │
                    │   (Protocol)     │
                    └──────────────────┘
```

---

## 2. 序列图（Sequence Diagrams）

### 2.1 Session Start Hook 执行序列

```
User         Executor      HookFactory    CommandHook    ShellProcess   Context
  │              │              │              │              │            │
  │ Start        │              │              │              │            │
  │ Session      │              │              │              │            │
  │──────────────>│              │              │              │            │
  │              │              │              │              │            │
  │              │ hooks("session_start")      │              │            │
  │              │─────────────>│              │              │            │
  │              │              │              │              │            │
  │              │              │ load_config_hooks()         │            │
  │              │              │─┐            │              │            │
  │              │              │ │ Read       │              │            │
  │              │              │<┘ .aworld/   │              │            │
  │              │              │   hooks.yaml │              │            │
  │              │              │              │              │            │
  │              │              │ Create CommandHook          │            │
  │              │              │──────────────>│              │            │
  │              │              │              │              │            │
  │              │ [List of hooks]             │              │            │
  │              │<─────────────│              │              │            │
  │              │              │              │              │            │
  │              │ for each hook               │              │            │
  │              │──────────────────────────────>│            │            │
  │              │                               │            │            │
  │              │              exec(message, context)        │            │
  │              │                               │            │            │
  │              │                               │ spawn      │            │
  │              │                               │ process    │            │
  │              │                               │───────────>│            │
  │              │                               │            │            │
  │              │                               │            │ Execute    │
  │              │                               │            │ command    │
  │              │                               │            │            │
  │              │                               │  stdout    │            │
  │              │                               │<───────────│            │
  │              │                               │            │            │
  │              │                               │ Parse JSON │            │
  │              │                               │───┐        │            │
  │              │                               │   │        │            │
  │              │                               │<──┘        │            │
  │              │                               │            │            │
  │              │                               │ Apply to   │            │
  │              │                               │ message    │            │
  │              │                               │───────────────────────>│
  │              │                               │            │            │
  │              │                modified message            │            │
  │              │<──────────────────────────────│            │            │
  │              │              │              │              │            │
  │  Enhanced    │              │              │              │            │
  │  Context     │              │              │              │            │
  │<─────────────│              │              │              │            │
  │              │              │              │              │            │
```

### 2.2 User Prompt Submit Hook 执行序列

```
User         Executor      HookFactory    CommandHook    Context
  │              │              │              │            │
  │ Input:       │              │              │            │
  │ "分析 @doc"  │              │              │            │
  │──────────────>│              │              │            │
  │              │              │              │            │
  │              │ hooks("user_prompt_submit") │            │
  │              │─────────────>│              │            │
  │              │              │              │            │
  │              │ [CommandHook("expand-files")]           │
  │              │<─────────────│              │            │
  │              │              │              │            │
  │              │ exec(message, context)      │            │
  │              │──────────────────────────────>│          │
  │              │                               │          │
  │              │              │ Build env vars│          │
  │              │              │ (AWORLD_MESSAGE_JSON)    │
  │              │              │               │          │
  │              │              │ Execute:      │          │
  │              │              │ python expand.py         │
  │              │              │               │          │
  │              │              │ Script reads: │          │
  │              │              │ - Parse "@doc"│          │
  │              │              │ - Read doc content      │
  │              │              │ - Return JSON │          │
  │              │              │               │          │
  │              │              │ JSON output:  │          │
  │              │              │ {             │          │
  │              │              │   "additionalContext":   │
  │              │              │   "[doc content]"        │
  │              │              │ }             │          │
  │              │              │               │          │
  │              │ Modified message (with doc content)    │
  │              │<──────────────────────────────│          │
  │              │              │              │            │
  │  Expanded    │              │              │            │
  │  Input       │              │              │            │
  │<─────────────│              │              │            │
  │              │              │              │            │
```

### 2.3 Pre Tool Use Hook 执行序列

```
Agent       ToolHandler   HookFactory   PreToolUse    ShellProcess
  │              │             │           Hook          │
  │ Call Tool    │             │           Wrapper       │
  │ read_file    │             │             │           │
  │──────────────>│             │             │           │
  │              │             │             │           │
  │              │ hooks("pre_tool_use")    │           │
  │              │────────────>│             │           │
  │              │             │             │           │
  │              │ [PreToolUseHook]          │           │
  │              │<────────────│             │           │
  │              │             │             │           │
  │              │ exec(message, context)    │           │
  │              │───────────────────────────>│          │
  │              │                            │          │
  │              │             │ Env vars:    │          │
  │              │             │ AWORLD_TOOL_NAME="read_file"│
  │              │             │ AWORLD_TOOL_INPUT={"path":"..."}│
  │              │             │              │          │
  │              │             │ Execute:     │          │
  │              │             │ python rewrite_path.py │
  │              │             │              │──────────>│
  │              │             │              │          │
  │              │             │              │ Script:  │
  │              │             │              │ - Rewrite path│
  │              │             │              │ - Validate│
  │              │             │              │          │
  │              │             │ JSON output: │<─────────│
  │              │             │ {            │          │
  │              │             │   "updatedInput": {    │
  │              │             │     "path": "/abs/path"│
  │              │             │   }          │          │
  │              │             │ }            │          │
  │              │             │              │          │
  │              │ Modified message (updated path)      │
  │              │<───────────────────────────│          │
  │              │             │              │          │
  │              │ Execute tool with updated input     │
  │              │───┐         │              │          │
  │              │   │         │              │          │
  │              │<──┘         │              │          │
  │              │             │              │          │
  │  Tool        │             │              │          │
  │  Result      │             │              │          │
  │<─────────────│             │              │          │
  │              │             │              │          │
```

### 2.4 配置加载序列

```
Executor    HookFactory   FileSystem    YAMLParser   CommandHook
  │             │             │              │           Wrapper
  │             │             │              │             │
  │ Initialize  │             │              │             │
  │────────────>│             │              │             │
  │             │             │              │             │
  │             │ load_config_hooks()        │             │
  │             │─┐           │              │             │
  │             │ │           │              │             │
  │             │<┘           │              │             │
  │             │             │              │             │
  │             │ Check cache (mtime)        │             │
  │             │─┐           │              │             │
  │             │ │           │              │             │
  │             │<┘           │              │             │
  │             │             │              │             │
  │             │ Read .aworld/hooks.yaml    │             │
  │             │─────────────>│              │             │
  │             │             │              │             │
  │             │             │ File content │             │
  │             │<─────────────│              │             │
  │             │             │              │             │
  │             │ Parse YAML  │              │             │
  │             │─────────────────────────────>│            │
  │             │                              │            │
  │             │             │ Parsed config │            │
  │             │<─────────────────────────────│            │
  │             │             │              │             │
  │             │ For each hook config       │             │
  │             │─┐           │              │             │
  │             │ │ Create CommandHookWrapper│             │
  │             │ │───────────────────────────────────────>│
  │             │ │           │              │             │
  │             │<┘           │              │             │
  │             │             │              │             │
  │             │ Update cache               │             │
  │             │─┐           │              │             │
  │             │ │           │              │             │
  │             │<┘           │              │             │
  │             │             │              │             │
  │ Hooks ready │             │              │             │
  │<────────────│             │              │             │
  │             │             │              │             │
```

---

## 3. 状态图（State Diagrams）

### 3.1 Hook 生命周期状态

```
     ┌──────────────┐
     │  Registered  │
     │   (初始化)   │
     └──────┬───────┘
            │
            │ HookFactory.hooks() called
            ▼
     ┌──────────────┐
     │   Loaded     │
     │  (已加载)    │
     └──────┬───────┘
            │
            │ run_hooks() called
            ▼
     ┌──────────────┐
     │  Executing   │────────────┐
     │  (执行中)    │            │ Timeout
     └──────┬───────┘            │
            │                    │
   ┌────────┼────────┐           │
   │ Success│ Error  │           │
   ▼        ▼        ▼           ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────────┐
│Comp- │ │Failed│ │Timed │ │  Killed  │
│leted │ │      │ │ Out  │ │          │
└──────┘ └──────┘ └──────┘ └──────────┘
```

### 3.2 配置文件状态

```
     ┌──────────────┐
     │  Not Exist   │
     │  (不存在)    │
     └──────┬───────┘
            │
            │ File created
            ▼
     ┌──────────────┐
     │   Exists     │
     │   (存在)     │
     └──────┬───────┘
            │
            │ HookFactory.load_config_hooks()
            ▼
     ┌──────────────┐
     │   Loading    │
     │   (加载中)   │
     └──────┬───────┘
            │
   ┌────────┼────────┐
   │ Valid  │ Invalid│
   ▼        ▼        
┌──────┐ ┌──────────┐
│Loaded│ │  Error   │
│(已加载)│ │ (格式错误)│
└───┬──┘ └──────────┘
    │
    │ File modified
    ▼
┌──────────────┐
│   Stale      │
│   (过期)     │
└──────┬───────┘
       │
       │ Reload
       └────────────────┐
                        │
                        ▼
                 ┌──────────────┐
                 │   Loading    │
                 │   (重新加载) │
                 └──────────────┘
```

### 3.3 Hook 执行流程状态

```
     ┌──────────────┐
     │   Pending    │
     │   (等待)     │
     └──────┬───────┘
            │
            │ run_hooks() enters
            ▼
     ┌──────────────┐
     │   Running    │─────────────┐
     │   (运行中)   │             │ User cancels
     └──────┬───────┘             │
            │                     │
    ┌───────┼───────┐             │
    │ continue=true │             │
    │               │             │
    ▼               ▼             ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│Continue  │  │  Stopped │  │Cancelled │
│Next Hook │  │(continue=│  │(用户取消) │
└────┬─────┘  │  false)  │  └──────────┘
     │        └──────────┘
     │
     │ More hooks?
     ├─ Yes ──────────┐
     │                │
     │                ▼
     │         ┌──────────────┐
     │         │   Running    │
     │         │  Next Hook   │
     │         └──────────────┘
     │
     └─ No ───────────┐
                      │
                      ▼
               ┌──────────────┐
               │   Completed  │
               │   (完成)     │
               └──────────────┘
```

---

## 4. 活动图（Activity Diagrams）

### 4.1 Hook 执行活动流程

```
Start
  │
  ▼
┌─────────────────┐
│Get hook point   │
│name from caller │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│HookFactory.     │
│hooks(point)     │
└────────┬────────┘
         │
         ▼
    ┌────────┐
    │Hooks   │ No
    │empty?  │────────────────────┐
    └───┬────┘                    │
        │ Yes                     │
        ▼                         │
┌─────────────────┐               │
│For each hook    │               │
│in list:         │               │
└────────┬────────┘               │
         │                        │
         ▼                        │
┌─────────────────┐               │
│Build Message    │               │
│object           │               │
└────────┬────────┘               │
         │                        │
         ▼                        │
┌─────────────────┐               │
│Call hook.exec() │               │
│(async)          │               │
└────────┬────────┘               │
         │                        │
    ┌────┴────┐                   │
    │Success? │ No                │
    └───┬─────┘ ─────┐            │
        │ Yes        │            │
        │            ▼            │
        │   ┌─────────────────┐  │
        │   │Log error        │  │
        │   │Continue to next │  │
        │   └─────────────────┘  │
        │                        │
        ▼                        │
┌─────────────────┐               │
│Parse hook output│               │
└────────┬────────┘               │
         │                        │
    ┌────┴────┐                   │
    │JSON?    │ No                │
    └───┬─────┘ ────────┐         │
        │ Yes           │         │
        │               ▼         │
        │     ┌─────────────────┐ │
        │     │Treat as plain   │ │
        │     │text context     │ │
        │     └─────────────────┘ │
        │                         │
        ▼                         │
┌─────────────────┐               │
│Apply output to  │               │
│message:         │               │
│- additionalCtx  │               │
│- updatedInput   │               │
│- systemMsg      │               │
└────────┬────────┘               │
         │                        │
    ┌────┴────┐                   │
    │continue │ No                │
    │= false? │────────┐          │
    └───┬─────┘        │          │
        │ Yes          │          │
        │              ▼          │
        │     ┌─────────────────┐ │
        │     │Stop hook chain  │ │
        │     │Return message   │ │
        │     └────────┬────────┘ │
        │              │          │
        ▼              │          │
┌─────────────────┐   │          │
│Yield modified   │   │          │
│message          │   │          │
└────────┬────────┘   │          │
         │            │          │
    ┌────┴────┐       │          │
    │More     │ No    │          │
    │hooks?   │───────┼──────────┘
    └───┬─────┘       │
        │ Yes         │
        └─────────────┘
                      │
                      ▼
                    End
```

### 4.2 配置加载活动流程

```
Start
  │
  ▼
┌─────────────────┐
│Check config file│
│path exists      │
└────────┬────────┘
         │
    ┌────┴────┐
    │Exists?  │ No
    └───┬─────┘ ────────────────┐
        │ Yes                   │
        ▼                       │
┌─────────────────┐             │
│Get file mtime   │             │
└────────┬────────┘             │
         │                      │
    ┌────┴────┐                 │
    │mtime    │ Yes             │
    │changed? │──────┐          │
    └───┬─────┘      │          │
        │ No         │          │
        │            ▼          │
        │   ┌─────────────────┐ │
        │   │Read file content│ │
        │   └────────┬────────┘ │
        │            │          │
        │            ▼          │
        │   ┌─────────────────┐ │
        │   │Parse YAML       │ │
        │   └────────┬────────┘ │
        │            │          │
        │       ┌────┴────┐     │
        │       │Valid?   │ No  │
        │       └───┬─────┘─────┼──┐
        │           │ Yes       │  │
        │           ▼           │  │
        │   ┌─────────────────┐ │  │
        │   │For each hook    │ │  │
        │   │config:          │ │  │
        │   └────────┬────────┘ │  │
        │            │          │  │
        │            ▼          │  │
        │   ┌─────────────────┐ │  │
        │   │Create hook      │ │  │
        │   │wrapper based on │ │  │
        │   │type             │ │  │
        │   └────────┬────────┘ │  │
        │            │          │  │
        │       ┌────┴────┐     │  │
        │       │enabled? │ No  │  │
        │       └───┬─────┘─────┘  │
        │           │ Yes          │
        │           ▼              │
        │   ┌─────────────────┐   │
        │   │Add to hook list │   │
        │   └────────┬────────┘   │
        │            │             │
        │       ┌────┴────┐        │
        │       │More     │ Yes    │
        │       │configs? │────────┘
        │       └───┬─────┘
        │           │ No
        │           ▼
        │   ┌─────────────────┐
        │   │Update cache     │
        │   │(mtime + hooks)  │
        │   └────────┬────────┘
        │            │
        ▼            ▼
┌─────────────────────────┐
│Return hooks by point    │
│(dict)                   │
└────────┬────────────────┘
         │
         ▼
       End
```

### 4.3 Shell Hook 执行活动流程

```
Start
  │
  ▼
┌─────────────────┐
│Build environment│
│variables:       │
│- SESSION_ID     │
│- TASK_ID        │
│- MESSAGE_JSON   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Spawn subprocess │
│with command     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Wait for output  │
│(with timeout)   │
└────────┬────────┘
         │
    ┌────┴────┐
    │Timeout? │ Yes ───────────┐
    └───┬─────┘                │
        │ No                   │
        │                      ▼
        │             ┌─────────────────┐
        │             │Kill process     │
        │             │Log warning      │
        │             │Return original  │
        │             │message          │
        │             └────────┬────────┘
        │                      │
        ▼                      │
┌─────────────────┐            │
│Read stdout &    │            │
│stderr           │            │
└────────┬────────┘            │
         │                     │
    ┌────┴────┐                │
    │Exit code│ 0               │
    │= 0?     │───────┐        │
    └───┬─────┘       │        │
        │ Non-zero    │        │
        │             ▼        │
        │    ┌─────────────────┐│
        │    │Log error        ││
        │    │(stderr)         ││
        │    │Return original  ││
        │    │message          ││
        │    └────────┬────────┘│
        │             │         │
        ▼             │         │
┌─────────────────┐  │         │
│Parse stdout     │  │         │
└────────┬────────┘  │         │
         │           │         │
    ┌────┴────┐      │         │
    │Starts   │ No   │         │
    │with {?  │──────┼─────────┼──┐
    └───┬─────┘      │         │  │
        │ Yes        │         │  │
        │            │         │  │
        ▼            │         │  │
┌─────────────────┐  │         │  │
│Parse JSON       │  │         │  │
└────────┬────────┘  │         │  │
         │           │         │  │
    ┌────┴────┐      │         │  │
    │Valid    │ No   │         │  │
    │JSON?    │──────┼─────────┼──┤
    └───┬─────┘      │         │  │
        │ Yes        │         │  │
        │            │         │  │
        ▼            ▼         ▼  ▼
┌──────────────────────────────────┐
│Apply output to message:          │
│                                  │
│- JSON: Apply structured fields  │
│- Plain text: Add as context     │
└────────┬─────────────────────────┘
         │
         ▼
┌─────────────────┐
│Return modified  │
│message          │
└────────┬────────┘
         │
         ▼
       End
```

---

## 5. 组件交互图（Component Interaction）

### 5.1 系统层次交互

```
┌─────────────────────────────────────────────────────────────┐
│                      User Interface                          │
│  (CLI, Web UI, IDE Extension)                               │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │ User inputs / Commands
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                   Application Layer                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Executor     │  │ TaskRunner   │  │ Handler      │      │
│  │ (CLI)        │  │              │  │              │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                 │                │
│         └─────────────────┼─────────────────┘                │
│                           │                                  │
│                           │ Invoke hooks at lifecycle points │
│                           ▼                                  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Hook Registry Layer                       │
│  ┌─────────────────────────────────────────────────┐        │
│  │           HookFactory (Registry)                │        │
│  │                                                 │        │
│  │  ┌─────────────────┐    ┌─────────────────┐  │        │
│  │  │ Python Hooks    │    │ Config Hooks    │  │        │
│  │  │ (Code Registry) │    │ (YAML Loader)   │  │        │
│  │  └────────┬────────┘    └────────┬────────┘  │        │
│  │           │                      │            │        │
│  │           └──────────┬───────────┘            │        │
│  │                      │                        │        │
│  │                      ▼                        │        │
│  │            [Unified Hook List]                │        │
│  └──────────────────────┬──────────────────────────────┘  │
│                         │                                  │
└─────────────────────────┼──────────────────────────────────┘
                          │
                          │ Execute hooks
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  Hook Execution Layer                        │
│  ┌─────────────────────────────────────────────────┐        │
│  │               run_hooks() Executor              │        │
│  │                                                 │        │
│  │  for hook in hooks:                             │        │
│  │    message = await hook.exec(message, context)  │        │
│  └─────────────────────┬───────────────────────────┘        │
│                        │                                     │
│         ┌──────────────┼──────────────┐                     │
│         │              │              │                     │
│         ▼              ▼              ▼                     │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐             │
│  │ PythonHook │ │CommandHook │ │CallbackHook│             │
│  │ (direct)   │ │ Wrapper    │ │ Wrapper    │             │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘             │
│        │              │              │                     │
└────────┼──────────────┼──────────────┼─────────────────────┘
         │              │              │
         ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                   External Systems                           │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐            │
│  │ Python     │  │ Shell      │  │ Python     │            │
│  │ Code       │  │ Process    │  │ Function   │            │
│  └────────────┘  └────────────┘  └────────────┘            │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 数据流交互

```
User Input
    │
    ▼
┌────────────┐
│ Executor   │
└─────┬──────┘
      │
      │ 1. Create Message
      │    (payload + headers)
      ▼
┌────────────┐
│ Message    │ ───────────┐
│ Object     │            │
└─────┬──────┘            │
      │                   │
      │ 2. run_hooks()    │
      ▼                   │
┌────────────┐            │
│HookFactory │            │
└─────┬──────┘            │
      │                   │
      │ 3. Return hooks   │
      ▼                   │
┌────────────┐            │
│ Hook List  │            │
└─────┬──────┘            │
      │                   │
      │ 4. For each hook  │
      │    exec()         │
      ▼                   │
┌────────────┐            │
│    Hook    │            │
│  .exec()   │            │
└─────┬──────┘            │
      │                   │
      │ 5. Process &      │
      │    transform      │
      │    message        │
      ▼                   │
┌────────────┐            │
│  Modified  │ ←──────────┘
│  Message   │ (additional_context,
└─────┬──────┘  updated_input, etc.)
      │
      │ 6. Return to
      │    application
      ▼
┌────────────┐
│   Agent    │
│  (with     │
│ enhanced   │
│ context)   │
└────────────┘
```

---

**文档状态：** Complete  
**下一步：** Integration with main design document

---
