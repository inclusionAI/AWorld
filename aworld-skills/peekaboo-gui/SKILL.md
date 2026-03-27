---
name: peekaboo-gui
description: 使用 Peekaboo CLI 自动化 macOS GUI 操作。当用户需要点击按钮、输入文字、控制窗口/应用、截图或与 UI 元素交互时使用。支持自然语言代理和 MCP 集成。触发词：GUI 自动化、桌面控制、UI 测试、"点击这个"、"输入到"、"打开 Safari"、权限错误。
---

# Peekaboo - macOS GUI 自动化

## 快速入门

Peekaboo 使用"先看后做"的方法进行可靠的 GUI 自动化：

```bash
# 1. 首先检查权限
peekaboo permissions status

# 2. 查看屏幕上的内容
peekaboo see --json

# 3. 与元素交互
peekaboo click "Safari 刷新按钮"
peekaboo type "Hello World" --target "搜索框"
```

## 核心命令

### 感知类
- `peekaboo see [--json]` - 捕获屏幕快照及 UI 元素
- `peekaboo image --mode screen --retina --path output.png` - 截图
- `peekaboo list apps|windows|screens` - 枚举系统状态

### 交互类
- `peekaboo click <目标>` - 通过文本/描述点击元素
- `peekaboo type <文本> [--target <元素>]` - 输入文字到元素
- `peekaboo hotkey <快捷键>` - 按键盘快捷键（如 "cmd+c"）
- `peekaboo scroll <方向> [--amount <像素>]` - 在窗口中滚动

### 系统控制
- `peekaboo app launch|activate|quit <名称>` - 管理应用程序
- `peekaboo window list|focus|close|minimize` - 窗口管理
- `peekaboo menu click <路径>` - 点击菜单项（如 "文件 > 打开"）

### 自动化
- `peekaboo "自然语言任务"` - 用代理执行多步任务
- `peekaboo agent --max-steps 20 "..."` - 显式代理模式
- `peekaboo run <脚本.peekaboo.json>` - 运行自动化脚本

### 实用工具
- `peekaboo permissions status|grant` - 检查/请求辅助功能权限
- `peekaboo mcp serve` - 启动 MCP 服务器，供 Claude/Cursor 集成

## 标准工作流

**始终遵循以下顺序：**

1. **验证设置**
   ```bash
   command -v peekaboo && peekaboo permissions status
   ```

2. **捕获 UI 状态**
   ```bash
   peekaboo see --json > snapshot.json
   ```

3. **执行操作**（使用相同的快照上下文）
   ```bash
   peekaboo click "按钮名称"
   ```

4. **验证结果**（可选）
   ```bash
   peekaboo see --json  # 检查操作是否成功
   ```

## 最佳实践

- **先看后做**：交互前始终运行 `see` 获取最新 UI 状态
- **使用快照**：多个操作引用同一快照避免漂移
- **先检查权限**：大多数失败都是因为缺少辅助功能权限
- **目标应用可见性**：确保目标应用在最前面且 UI 可见
- **自然语言回退**：对于复杂任务，使用 `peekaboo "描述任务"`

## 故障排查

### 权限被拒绝
```bash
peekaboo permissions grant  # 打开系统设置
```

### 找不到元素
```bash
# 更新快照后重试
peekaboo see --json
peekaboo click "元素" --snapshot <新快照ID>
```

### 命令未找到
```bash
# 检查安装
command -v peekaboo || echo "将 peekaboo 添加到 PATH"
```

### 点击不可靠
- 将目标应用置于前台：`peekaboo app activate <名称>`
- 禁用动画：系统设置 > 辅助功能 > 显示 > 减少动态效果
- 在命令之间添加延迟：`sleep 1`

## 高级功能

### 脚本化
创建可重用的自动化流程：
```json
{
  "steps": [
    {"action": "see"},
    {"action": "click", "target": "按钮"},
    {"action": "type", "text": "内容"}
  ]
}
```
运行：`peekaboo run flow.peekaboo.json`

### MCP 集成
在 Claude Desktop/Cursor 中使用 Peekaboo：
```bash
peekaboo mcp serve
```
添加到 MCP 配置并重启客户端。

### 自然语言代理
处理复杂的多步任务：
```bash
peekaboo "打开 Safari，搜索 OpenClaw，点击第一个结果"
```

## 开发（仅限源码仓库）

在 Peekaboo 源码仓库中工作时：
```bash
pnpm install
pnpm run build:cli
pnpm run lint
pnpm run test:safe
```

## 参考文件

详细信息请参阅：
- **API 参考**：查看 [API_REFERENCE.md](references/API_REFERENCE.md) 了解所有命令
- **示例**：查看 [EXAMPLES.md](references/EXAMPLES.md) 了解常见模式
- **故障排查指南**：查看 [TROUBLESHOOTING.md](references/TROUBLESHOOTING.md) 了解详细修复方法

---

**仓库**：https://github.com/steipete/Peekaboo.git  
**安装**：`brew install steipete/tap/peekaboo` 或从源码构建
