# Peekaboo 故障排查指南

常见 Peekaboo 问题的详细解决方案。

## 权限问题

### 症状："权限被拒绝" 或 "需要辅助功能访问权限"

**根本原因**：Peekaboo 需要 macOS 辅助功能和屏幕录制权限。

**解决方案**：
1. 检查当前状态：
   ```bash
   peekaboo permissions status
   ```

2. 授予缺失的权限：
   ```bash
   peekaboo permissions grant
   ```
   这会打开系统设置。启用：
   - **隐私与安全性 > 辅助功能** → 添加 Terminal/iTerm
   - **隐私与安全性 > 屏幕录制** → 添加 Terminal/iTerm

3. 授予权限后重启终端。

**预防**：自动化任务前始终运行 `permissions status`。

---

### 症状：已授予权限但仍然失败

**根本原因**：更改权限后需要重启终端应用。

**解决方案**：
```bash
# 完全退出终端
killall Terminal  # 或 iTerm2

# 重新启动并测试
peekaboo permissions status
```

**验证**：所有必需的权限应显示 `✓ 已授予`。

---

## 元素检测问题

### 症状："找不到元素" 或 "没有匹配的元素"

**根本原因**：自上次快照以来 UI 已更改，或元素描述不匹配。

**解决方案**：
1. 更新快照：
   ```bash
   peekaboo see --json > current.json
   cat current.json | jq '.elements[] | {text, role}'
   ```

2. 找到确切的元素文本：
   ```bash
   # 搜索部分匹配
   cat current.json | jq '.elements[] | select(.text | contains("搜索"))'
   ```

3. 使用快照中的确切文本：
   ```bash
   peekaboo click "搜索栏"  # 使用确切文本，而非描述
   ```

**预防**：
- 操作前始终运行 `see`
- 多个操作使用相同的快照 ID：
  ```bash
  SNAP=$(peekaboo see --json | jq -r '.snapshot_id')
  peekaboo click "按钮" --snapshot $SNAP
  peekaboo type "文本" --snapshot $SNAP
  ```

---

### 症状：点击了错误的元素

**根本原因**：多个元素具有相似文本，或快照后 UI 移位。

**解决方案**：
1. 获取更具体的描述：
   ```bash
   peekaboo see --json | jq '.elements[] | select(.text == "提交") | {text, role, bounds}'
   ```

2. 使用角色或位置信息：
   ```bash
   # 点击按钮（非链接）名为提交
   peekaboo see --json | jq '.elements[] | select(.text == "提交" and .role == "button")'
   ```

3. 点击前立即使用新快照：
   ```bash
   peekaboo see --json > /tmp/snap.json
   peekaboo click "提交" --snapshot $(jq -r '.snapshot_id' /tmp/snap.json)
   ```

**预防**：自动化期间保持 UI 稳定（无动画、覆盖层）。

---

## 应用控制问题

### 症状：应用无法启动或启动时间过长

**根本原因**：应用未安装，或系统速度慢。

**解决方案**：
1. 验证应用存在：
   ```bash
   mdfind "kMDItemKind == 'Application'" | grep -i safari
   ```

2. 使用确切的应用名称：
   ```bash
   # 检查正在运行的应用
   peekaboo list apps --json | jq -r '.[].name'
   
   # 使用确切名称
   peekaboo app launch "Safari"
   ```

3. 添加等待时间：
   ```bash
   peekaboo app launch Safari
   sleep 3  # 等待启动
   peekaboo app activate Safari
   ```

**预防**：启动前使用 `list apps` 验证应用名称。

---

### 症状：应用已激活但 UI 未就绪

**根本原因**：应用窗口仍在加载。

**解决方案**：
```bash
# 激活并等待
peekaboo app activate Safari
sleep 2

# 验证窗口存在
peekaboo window list --json | jq -e '.[] | select(.app == "Safari")'

# 继续自动化
peekaboo see
```

**预防**：应用启动/激活后添加延迟。

---

## 窗口管理问题

### 症状：通过标题找不到窗口

**根本原因**：标题不完全匹配，或窗口已最小化。

**解决方案**：
1. 列出所有窗口：
   ```bash
   peekaboo window list --json | jq -r '.[] | "\(.app): \(.title)"'
   ```

2. 使用部分匹配：
   ```bash
   # 查找标题包含 "README" 的窗口
   TITLE=$(peekaboo window list --json | jq -r '.[] | select(.title | contains("README")) | .title')
   peekaboo window focus "$TITLE"
   ```

3. 先取消隐藏：
   ```bash
   peekaboo window list --json | jq -e '.[] | select(.minimized == true)'
   # 如果已最小化，先恢复
   ```

**预防**：交互前先聚焦窗口。

---

## 命令执行问题

### 症状："command not found: peekaboo"

**根本原因**：Peekaboo 不在 PATH 中或未安装。

**解决方案**：
1. 检查安装：
   ```bash
   which peekaboo
   ```

2. 如果缺失则安装：
   ```bash
   # 通过 Homebrew
   brew install steipete/tap/peekaboo
   
   # 或从源码构建
   git clone https://github.com/steipete/Peekaboo.git
   cd Peekaboo
   pnpm install
   pnpm run build:cli
   ```

3. 使用绝对路径：
   ```bash
   /usr/local/bin/peekaboo see
   # 或
   ~/Peekaboo/packages/cli/dist/peekaboo see
   ```

**预防**：在 `~/.zshrc` 中添加到 PATH：
```bash
export PATH="/usr/local/bin:$PATH"
```

---

### 症状：命令超时

**根本原因**：系统过载或 UI 无响应。

**解决方案**：
1. 增加超时：
   ```bash
   peekaboo config default_timeout 30
   ```

2. 分解为更小的步骤：
   ```bash
   # 而不是一个长的代理任务
   peekaboo "打开 Safari"
   sleep 2
   peekaboo "搜索 OpenClaw"
   sleep 2
   peekaboo "点击第一个结果"
   ```

3. 检查系统负载：
   ```bash
   top -l 1 | grep "CPU usage"
   ```

**预防**：为慢速操作使用显式超时。

---

## 自然语言代理问题

### 症状：代理不理解任务

**根本原因**：任务过于模糊或歧义。

**解决方案**：
1. 更具体：
   ```bash
   # 模糊
   peekaboo "打开浏览器"
   
   # 具体
   peekaboo "启动 Safari 并导航到 docs.openclaw.ai"
   ```

2. 分解为步骤：
   ```bash
   peekaboo "打开 Safari"
   peekaboo "在地址栏输入 docs.openclaw.ai 并按回车"
   ```

3. 使用显式代理模式并增加步数：
   ```bash
   peekaboo agent --max-steps 30 --task "复杂任务描述"
   ```

**预防**：先用手动命令测试任务，然后再使用代理。

---

### 症状：代理卡住或循环

**根本原因**：UI 状态未改变，或代理误解反馈。

**解决方案**：
1. 减少最大步数以防止循环：
   ```bash
   peekaboo agent --max-steps 10 --task "..."
   ```

2. 关键步骤使用手动命令：
   ```bash
   # 手动设置
   peekaboo app launch Safari
   sleep 2
   
   # 复杂任务用代理
   peekaboo agent --task "搜索 OpenClaw 并打开第一个结果"
   ```

**预防**：探索性任务用代理，已知工作流用手动命令。

---

## 截图问题

### 症状：截图为空白或黑色

**根本原因**：缺少屏幕录制权限，或捕获太快。

**解决方案**：
1. 检查权限：
   ```bash
   peekaboo permissions status | grep "屏幕录制"
   ```

2. 捕获前添加延迟：
   ```bash
   sleep 1
   peekaboo image --mode screen --path screenshot.png
   ```

3. 改为捕获特定窗口：
   ```bash
   peekaboo image --mode window --window Safari --path window.png
   ```

**预防**：始终验证屏幕录制权限。

---

### 症状：截图分辨率过低

**根本原因**：未使用 Retina 模式。

**解决方案**：
```bash
peekaboo image --mode screen --retina --path high-res.png
```

---

## 性能问题

### 症状：命令很慢

**根本原因**：大量 UI 处理或系统负载。

**解决方案**：
1. 减少不必要的 `see` 调用：
   ```bash
   # 差：每个操作前都看
   peekaboo see; peekaboo click "A"
   peekaboo see; peekaboo click "B"
   
   # 好：重用快照
   SNAP=$(peekaboo see --json | jq -r '.snapshot_id')
   peekaboo click "A" --snapshot $SNAP
   peekaboo click "B" --snapshot $SNAP
   ```

2. 清理缓存的快照：
   ```bash
   peekaboo clean
   ```

3. 降低快照保留时间：
   ```bash
   peekaboo config snapshot_retention 60  # 秒
   ```

**预防**：重用快照，定期清理缓存。

---

## 集成问题

### 症状：MCP 服务器无法连接

**根本原因**：端口冲突或 Claude/Cursor 未配置。

**解决方案**：
1. 检查服务器是否运行：
   ```bash
   lsof -i :8080  # 默认 MCP 端口
   ```

2. 使用显式端口启动：
   ```bash
   peekaboo mcp serve --port 8081
   ```

3. 更新 Claude Desktop 中的 MCP 配置：
   ```json
   {
     "mcpServers": {
       "peekaboo": {
         "command": "peekaboo",
         "args": ["mcp", "serve"],
         "env": {}
       }
     }
   }
   ```

4. 重启 Claude/Cursor。

**预防**：使用非默认端口避免冲突。

---

## 一般调试

### 启用详细日志
```bash
peekaboo config log_level debug
peekaboo see  # 将显示详细日志
```

### 检查原始输出
```bash
# 查看完整的 JSON 响应
peekaboo see --json | jq .

# 检查特定字段
peekaboo see --json | jq '.elements | length'
peekaboo see --json | jq '.elements[] | select(.role == "button")'
```

### 增量测试
```bash
# 逐步构建自动化
peekaboo permissions status  # ✓
peekaboo app launch Safari   # ✓
sleep 2                      # ✓
peekaboo see --json          # ✓
peekaboo click "地址栏"      # ✗ 在这里失败
# 调试：检查 see 输出中的元素名称
```

### 常用命令模式
```bash
# 安全探索（只读）
peekaboo list apps
peekaboo see
peekaboo window list

# 带验证的操作
peekaboo click "提交"
sleep 1
peekaboo see --json | grep -i "成功"

# 带延迟的重试
for i in {1..3}; do
  peekaboo click "按钮" && break
  sleep 1
done
```

---

## 获取帮助

如果问题持续存在：

1. **检查 Peekaboo 版本**：
   ```bash
   peekaboo --version
   ```

2. **更新到最新版本**：
   ```bash
   brew upgrade peekaboo
   ```

3. **报告问题**时提供：
   - 失败的完整命令
   - `peekaboo permissions status` 输出
   - `peekaboo see --json` 输出（如有敏感信息则删除）
   - macOS 版本：`sw_vers`

4. **GitHub Issues**：https://github.com/steipete/Peekaboo/issues

---

## 常见错误代码

| 错误代码 | 含义 | 解决方案 |
|---------|------|---------|
| 0 | 成功 | - |
| 1 | 一般错误 | 检查命令语法 |
| 2 | 权限被拒绝 | 运行 `permissions grant` |
| 3 | 找不到元素 | 更新快照，检查元素文本 |
| 4 | 超时 | 增加 `default_timeout` 配置 |
| 5 | 参数无效 | 检查命令参数格式 |

---

## 快速诊断清单

运行以下命令进行快速诊断：

```bash
#!/bin/bash
echo "=== Peekaboo 诊断 ==="
echo ""
echo "1. 版本："
peekaboo --version
echo ""
echo "2. 权限："
peekaboo permissions status
echo ""
echo "3. 正在运行的应用："
peekaboo list apps --json | jq -r '.[].name' | head -5
echo ""
echo "4. 可见窗口："
peekaboo window list --json | jq -r '.[].title' | head -5
echo ""
echo "5. 配置："
peekaboo config log_level
peekaboo config default_timeout
echo ""
echo "=== 诊断完成 ==="
```

保存为 `diagnose.sh` 并运行 `bash diagnose.sh` 进行快速检查。
