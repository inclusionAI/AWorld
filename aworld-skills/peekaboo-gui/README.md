# Peekaboo GUI 自动化技能

用于使用 Peekaboo CLI 自动化 macOS GUI 的 OpenClaw 技能。

## 安装

### 方式 1：手动安装
```bash
# 复制到技能目录
cp -r peekaboo-gui ~/.agents/skills/

# 重启 OpenClaw Gateway（如果作为服务运行）
openclaw gateway restart
```

### 方式 2：从 .skill 文件
```bash
# 将 .skill 文件解压到技能目录
unzip peekaboo-gui.skill -d ~/.agents/skills/
```

## 前置条件

1. **安装 Peekaboo CLI**
   ```bash
   brew install steipete/tap/peekaboo
   ```

2. **授予权限**
   ```bash
   peekaboo permissions grant
   ```
   然后在系统设置中启用：
   - 隐私与安全性 > 辅助功能
   - 隐私与安全性 > 屏幕录制

3. **验证设置**
   ```bash
   ~/.agents/skills/peekaboo-gui/scripts/check_setup.sh
   ```

## 使用方法

安装后，当你提到以下内容时，OpenClaw 会自动使用此技能：
- "点击按钮"
- "在搜索框中输入"
- "打开 Safari"
- "截图"
- "自动化这个 UI 任务"
- "macOS GUI 自动化"

### 示例命令

**简单的 GUI 操作**：
```
你："帮我点击 Safari 中的刷新按钮"
OpenClaw：（使用 peekaboo-gui 技能自动化点击）
```

**复杂的自动化**：
```
你："打开备忘录并创建一个包含 5 个项目的购物清单"
OpenClaw：（使用 peekaboo 自然语言代理）
```

## 技能结构

```
peekaboo-gui/
├── SKILL.md                    # 主技能定义（156 行）
├── README.md                   # 本文档
├── scripts/
│   └── check_setup.sh          # 设置验证脚本
└── references/
    ├── API_REFERENCE.md        # 完整 CLI 参考（296 行）
    ├── EXAMPLES.md             # 实际示例（387 行）
    └── TROUBLESHOOTING.md      # 详细故障排查（490 行）
```

## 功能特性

- **感知**：截图、元素检测、系统枚举
- **交互**：点击、输入、滚动、拖动、键盘快捷键
- **系统控制**：应用管理、窗口控制、菜单操作
- **自然语言**：使用 `peekaboo "任务"` 进行多步任务自动化
- **MCP 集成**：通过 MCP 在 Claude Desktop/Cursor 中使用
- **错误恢复**：全面的故障排查指南

## 故障排查

### 技能未触发
OpenClaw 可能正在使用捆绑的 `peekaboo` 技能。我们的技能命名为 `peekaboo-gui` 以避免冲突。

### 权限问题
运行设置检查器：
```bash
~/.agents/skills/peekaboo-gui/scripts/check_setup.sh
```

### 未找到 Peekaboo
通过 Homebrew 安装：
```bash
brew install steipete/tap/peekaboo
```

或从源码构建：
```bash
git clone https://github.com/steipete/Peekaboo.git
cd Peekaboo
pnpm install
pnpm run build:cli
```

## 文档

- **快速参考**：查看 `SKILL.md`
- **完整 API**：查看 `references/API_REFERENCE.md`
- **示例**：查看 `references/EXAMPLES.md`
- **故障排查**：查看 `references/TROUBLESHOOTING.md`

## 仓库

原始 Peekaboo 项目：https://github.com/steipete/Peekaboo

## 许可证

此技能遵循 Peekaboo 的许可证。详情请参阅原始仓库。

---

## 技能信息

- **名称**：peekaboo-gui
- **版本**：1.0.0
- **作者**：基于 Peekaboo CLI 创建
- **类型**：系统自动化
- **平台**：macOS
- **依赖**：Peekaboo CLI (https://github.com/steipete/Peekaboo)

## 支持的操作

### 基本操作
- ✅ 点击 UI 元素
- ✅ 输入文本
- ✅ 按键盘快捷键
- ✅ 滚动窗口
- ✅ 拖放元素

### 系统控制
- ✅ 启动/退出应用
- ✅ 切换应用
- ✅ 窗口管理（聚焦、最小化、关闭）
- ✅ 菜单栏操作
- ✅ Mission Control 空间管理

### 高级功能
- ✅ 屏幕截图
- ✅ UI 元素识别
- ✅ 自然语言任务执行
- ✅ 脚本化自动化
- ✅ MCP 协议集成

## 更新日志

### v1.0.0 (2026-03-26)
- 初始版本
- 完整的中文文档
- 包含 API 参考、示例和故障排查
- 自动化设置检查脚本
- 支持所有 Peekaboo CLI 功能
