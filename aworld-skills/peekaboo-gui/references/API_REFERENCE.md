# Peekaboo API 参考

所有 Peekaboo CLI 操作的完整命令参考。

## 权限

### `peekaboo permissions status`
检查当前辅助功能权限。

**输出**：包含屏幕录制、辅助功能等权限状态的 JSON。

### `peekaboo permissions grant`
请求缺失的权限（打开系统设置）。

## 感知命令

### `peekaboo see [选项]`
捕获带有检测到的元素的 UI 快照。

**选项**：
- `--json` - 输出结构化 JSON
- `--snapshot <id>` - 重用现有快照
- `--screen <索引>` - 指定屏幕（默认：主屏幕）

**输出**：包含文本、角色、边界、操作的元素列表。

### `peekaboo image [选项]`
捕获截图。

**选项**：
- `--mode <screen|window|area>` - 捕获模式
- `--retina` - 使用 Retina 分辨率
- `--path <文件>` - 输出路径（默认：screenshot.png）
- `--window <名称>` - 指定窗口

### `peekaboo list <类型>`
枚举系统资源。

**类型**：
- `apps` - 正在运行的应用程序
- `windows` - 打开的窗口
- `screens` - 已连接的显示器
- `permissions` - 所需权限状态

## 交互命令

### `peekaboo click <目标> [选项]`
点击 UI 元素。

**目标**：元素文本、描述或坐标。

**选项**：
- `--snapshot <id>` - 使用特定快照
- `--button <left|right|middle>` - 鼠标按钮（默认：left）
- `--modifier <键>` - 按住修饰键（shift、cmd、alt、ctrl）
- `--count <n>` - 点击次数（默认：1）

**示例**：
```bash
peekaboo click "提交"
peekaboo click "文件" --button right
peekaboo click --snapshot abc123 "按钮"
```

### `peekaboo type <文本> [选项]`
输入文字到聚焦的元素或目标。

**选项**：
- `--target <元素>` - 输入前点击目标
- `--snapshot <id>` - 使用特定快照
- `--clear` - 先清除现有文本

**示例**：
```bash
peekaboo type "Hello World"
peekaboo type "搜索查询" --target "搜索框" --clear
```

### `peekaboo press <键> [选项]`
按单个键。

**键**：return、tab、escape、delete、space、方向键等。

**选项**：
- `--modifier <键>` - 按住修饰键

**示例**：
```bash
peekaboo press return
peekaboo press tab --modifier shift
```

### `peekaboo hotkey <快捷键>`
按键盘快捷键。

**格式**：修饰键用 `+` 分隔

**示例**：
```bash
peekaboo hotkey "cmd+c"
peekaboo hotkey "cmd+shift+4"
peekaboo hotkey "ctrl+alt+delete"
```

### `peekaboo scroll <方向> [选项]`
在窗口中滚动。

**方向**：up、down、left、right

**选项**：
- `--amount <像素>` - 滚动距离（默认：100）
- `--window <名称>` - 目标窗口

**示例**：
```bash
peekaboo scroll down --amount 500
peekaboo scroll up --window Safari
```

### `peekaboo drag <从> <到> [选项]`
拖动元素或从坐标。

**选项**：
- `--snapshot <id>` - 使用特定快照

**示例**：
```bash
peekaboo drag "项目 1" "文件夹"
peekaboo drag 100,200 300,400
```

### `peekaboo move <目标>`
移动鼠标到元素或坐标。

**示例**：
```bash
peekaboo move "按钮"
peekaboo move 500,300
```

## 系统控制命令

### `peekaboo app <操作> <名称>`
管理应用程序。

**操作**：
- `launch` - 启动应用程序
- `activate` - 置于前台
- `quit` - 关闭应用程序
- `hide` - 隐藏应用程序
- `unhide` - 显示应用程序

**示例**：
```bash
peekaboo app launch Safari
peekaboo app activate "Visual Studio Code"
peekaboo app quit 备忘录
```

### `peekaboo window <操作> [选项]`
管理窗口。

**操作**：
- `list` - 列出所有窗口
- `focus <名称>` - 按标题聚焦窗口
- `close [名称]` - 关闭窗口
- `minimize [名称]` - 最小化窗口
- `maximize [名称]` - 最大化窗口

**示例**：
```bash
peekaboo window list
peekaboo window focus "未命名"
peekaboo window close
```

### `peekaboo menu click <路径>`
点击菜单栏项。

**路径**：用 ` > ` 分隔的菜单路径

**示例**：
```bash
peekaboo menu click "文件 > 打开"
peekaboo menu click "编辑 > 查找 > 查找下一个"
```

### `peekaboo menubar <操作>`
控制菜单栏。

**操作**：
- `show` - 显示菜单栏
- `hide` - 隐藏菜单栏

### `peekaboo dock <操作>`
控制程序坞。

**操作**：
- `show` - 显示程序坞
- `hide` - 隐藏程序坞

### `peekaboo space <操作> [索引]`
管理 Mission Control 空间。

**操作**：
- `list` - 列出空间
- `switch <索引>` - 切换到空间

### `peekaboo dialog <操作>`
处理系统对话框。

**操作**：
- `dismiss` - 关闭活动对话框
- `accept` - 接受对话框

## 自动化命令

### `peekaboo "<任务>"`
使用代理执行自然语言任务。

**示例**：
```bash
peekaboo "打开备忘录并创建新笔记"
peekaboo "在 Safari 中搜索 OpenClaw"
```

### `peekaboo agent [选项]`
在交互或任务模式下运行代理。

**选项**：
- `--task "<描述>"` - 执行特定任务
- `--max-steps <n>` - 最大步数（默认：10）
- `--timeout <秒>` - 操作超时

### `peekaboo run <脚本>`
执行自动化脚本文件。

**脚本格式**：包含步骤数组的 JSON

**示例脚本**：
```json
{
  "name": "打开并搜索",
  "steps": [
    {"action": "app", "args": ["launch", "Safari"]},
    {"action": "see"},
    {"action": "click", "target": "地址栏"},
    {"action": "type", "text": "example.com"},
    {"action": "press", "key": "return"}
  ]
}
```

## 集成命令

### `peekaboo mcp serve [选项]`
启动 MCP 服务器供 Claude/Cursor 使用。

**选项**：
- `--port <n>` - 服务器端口（默认：自动）
- `--host <地址>` - 绑定地址（默认：127.0.0.1）

**暴露的 MCP 工具**：
- `see` - 捕获 UI 快照
- `click` - 点击元素
- `type` - 输入文字
- `app_*` - 应用控制
- `window_*` - 窗口管理

## 配置

### `peekaboo config <键> [值]`
获取或设置配置值。

**键**：
- `snapshot_retention` - 快照缓存时间（秒）
- `default_timeout` - 命令超时（秒）
- `log_level` - 日志详细程度

**示例**：
```bash
peekaboo config log_level debug
peekaboo config default_timeout
```

### `peekaboo clean`
清理缓存的快照和临时文件。

## 退出代码

- `0` - 成功
- `1` - 一般错误
- `2` - 权限被拒绝
- `3` - 找不到元素
- `4` - 超时
- `5` - 参数无效
