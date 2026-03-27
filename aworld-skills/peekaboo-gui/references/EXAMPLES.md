# Peekaboo 示例

常见自动化模式和实际用例。

## 基本交互

### 点击按钮
```bash
# 先查看 UI
peekaboo see --json

# 通过文本点击
peekaboo click "提交"
peekaboo click "确定"
peekaboo click "搜索"
```

### 输入文字
```bash
# 先聚焦元素
peekaboo click "搜索框"
peekaboo type "OpenClaw 文档"
peekaboo press return
```

### 清除并输入
```bash
peekaboo type "新文本" --target "输入框" --clear
```

## 应用控制

### 启动和激活
```bash
# 如果未运行则启动应用
peekaboo app launch Safari

# 等待启动
sleep 2

# 置于前台
peekaboo app activate Safari
```

### 在应用间切换
```bash
peekaboo app activate "Visual Studio Code"
sleep 1
peekaboo app activate Terminal
```

### 退出应用
```bash
# 退出单个应用
peekaboo app quit 备忘录

# 退出多个应用
for app in Safari Chrome Firefox; do
  peekaboo app quit "$app"
done
```

## 窗口管理

### 按标题聚焦窗口
```bash
peekaboo window focus "README.md"
```

### 排列窗口
```bash
# 最大化当前窗口
peekaboo window maximize

# 最小化其他窗口
peekaboo window list --json | jq -r '.[] | select(.title != "README.md") | .title' | while read title; do
  peekaboo window minimize "$title"
done
```

## 菜单操作

### 文件菜单操作
```bash
peekaboo menu click "文件 > 新建"
peekaboo menu click "文件 > 打开"
peekaboo menu click "文件 > 保存"
```

### 编辑菜单
```bash
peekaboo menu click "编辑 > 拷贝"
peekaboo menu click "编辑 > 粘贴"
peekaboo menu click "编辑 > 查找 > 查找下一个"
```

## 键盘快捷键

### 常用快捷键
```bash
# 拷贝/粘贴
peekaboo hotkey "cmd+c"
peekaboo hotkey "cmd+v"

# 全选
peekaboo hotkey "cmd+a"

# 撤销/重做
peekaboo hotkey "cmd+z"
peekaboo hotkey "cmd+shift+z"

# 截图
peekaboo hotkey "cmd+shift+4"
```

### 导航
```bash
# 切换标签页
peekaboo hotkey "cmd+shift+]"  # 下一个标签页
peekaboo hotkey "cmd+shift+["  # 上一个标签页

# 关闭窗口
peekaboo hotkey "cmd+w"
```

## 截图

### 全屏截图
```bash
peekaboo image --mode screen --retina --path fullscreen.png
```

### 活动窗口
```bash
peekaboo image --mode window --path window.png
```

### 特定窗口
```bash
peekaboo image --mode window --window Safari --path safari.png
```

## 滚动

### 在浏览器中滚动
```bash
# 向下滚动
peekaboo scroll down --amount 500

# 滚动到底部
for i in {1..5}; do
  peekaboo scroll down --amount 1000
  sleep 0.5
done
```

### 在特定窗口中滚动
```bash
peekaboo scroll up --window "文档.pdf" --amount 300
```

## 多步工作流

### 在应用中打开文件
```bash
# 启动应用
peekaboo app launch "Visual Studio Code"
sleep 2

# 打开文件对话框
peekaboo hotkey "cmd+o"
sleep 1

# 输入路径
peekaboo type "~/Documents/project"
peekaboo press return
```

### 搜索并打开结果
```bash
# 聚焦搜索
peekaboo hotkey "cmd+space"
sleep 0.5

# 输入查询
peekaboo type "Safari"
sleep 0.5

# 打开结果
peekaboo press return
```

### 创建备忘录
```bash
# 打开备忘录
peekaboo app activate 备忘录
sleep 1

# 新建笔记
peekaboo hotkey "cmd+n"
sleep 0.5

# 输入内容
peekaboo type "购物清单："
peekaboo press return
peekaboo type "- 牛奶"
peekaboo press return
peekaboo type "- 面包"
```

## 自然语言代理

### 简单任务
```bash
# 单个操作
peekaboo "打开 Safari"

# 多步
peekaboo "打开备忘录并创建新笔记，标题是待办事项"
```

### 复杂自动化
```bash
# 研究任务
peekaboo agent --max-steps 20 --task "在 Safari 中搜索 OpenClaw，打开第一个结果，并截图保存"

# 数据录入
peekaboo agent --task "打开 Numbers，创建表格，输入标题行：姓名、邮箱、电话"
```

## 错误处理

### 失败时重试
```bash
# 最多重试 3 次
for i in {1..3}; do
  if peekaboo click "提交"; then
    echo "成功"
    break
  else
    echo "重试 $i..."
    sleep 1
  fi
done
```

### 操作后验证
```bash
# 点击并验证
peekaboo click "保存"
sleep 1

# 检查是否出现对话框
if peekaboo see --json | jq -e '.elements[] | select(.text == "保存成功")' > /dev/null; then
  echo "保存已确认"
else
  echo "保存可能失败"
fi
```

## 集成模式

### 与其他工具结合
```bash
# 截图并用 Vision API 分析
peekaboo image --mode screen --path screen.png
curl -X POST https://api.openai.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"gpt-4-vision-preview\", \"messages\": [{\"role\": \"user\", \"content\": [{\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,$(base64 screen.png)\"}}]}]}"
```

### 定时自动化
```bash
# 添加到 crontab 进行周期性任务
# 0 9 * * * /usr/local/bin/peekaboo "打开日历，检查今天的会议"
```

## 脚本化自动化

### JSON 脚本示例
```json
{
  "name": "在 Safari 中打开并搜索",
  "description": "启动 Safari，导航到 OpenClaw 文档并搜索",
  "steps": [
    {
      "action": "app",
      "args": ["launch", "Safari"]
    },
    {
      "action": "wait",
      "duration": 2
    },
    {
      "action": "see"
    },
    {
      "action": "click",
      "target": "地址栏"
    },
    {
      "action": "type",
      "text": "docs.openclaw.ai"
    },
    {
      "action": "press",
      "key": "return"
    },
    {
      "action": "wait",
      "duration": 3
    },
    {
      "action": "hotkey",
      "keys": "cmd+f"
    },
    {
      "action": "type",
      "text": "skills"
    }
  ]
}
```

保存为 `search.peekaboo.json` 并运行：
```bash
peekaboo run search.peekaboo.json
```

## 测试模式

### UI 测试套件
```bash
#!/bin/bash
# test-app.sh

echo "开始 UI 测试..."

# 设置
peekaboo app launch 测试应用
sleep 2

# 测试 1：登录
echo "测试：登录"
peekaboo click "用户名"
peekaboo type "testuser"
peekaboo click "密码"
peekaboo type "testpass"
peekaboo click "登录"
sleep 2

# 测试 2：创建项目
echo "测试：创建项目"
peekaboo click "新建项目"
peekaboo type "测试项目"
peekaboo click "保存"

# 清理
peekaboo app quit 测试应用

echo "测试完成"
```

## 调试技巧

### 详细输出
```bash
# 查看检测到的内容
peekaboo see --json | jq .

# 点击前检查元素是否存在
if peekaboo see --json | jq -e '.elements[] | select(.text == "按钮")' > /dev/null; then
  peekaboo click "按钮"
else
  echo "未找到按钮"
fi
```

### 快照检查
```bash
# 捕获快照 ID
SNAPSHOT=$(peekaboo see --json | jq -r '.snapshot_id')

# 为多个操作重用
peekaboo click "第一个" --snapshot $SNAPSHOT
peekaboo click "第二个" --snapshot $SNAPSHOT
```

## 实用场景

### 批量文件重命名
```bash
# 在 Finder 中选择多个文件后
peekaboo hotkey "cmd+shift+r"  # 批量重命名
sleep 1
peekaboo type "新名称"
peekaboo press return
```

### 自动填表
```bash
peekaboo "打开 Safari 并访问表单页面"
sleep 3
peekaboo click "姓名"
peekaboo type "张三"
peekaboo click "邮箱"
peekaboo type "zhangsan@example.com"
peekaboo click "提交"
```

### 演示自动化
```bash
# 自动演示脚本
peekaboo app activate Keynote
sleep 1
for i in {1..10}; do
  peekaboo press space  # 下一张幻灯片
  sleep 3
done
```

### 监控任务
```bash
# 每 5 分钟检查一次通知
while true; do
  if peekaboo see --json | jq -e '.elements[] | select(.text | contains("新消息"))' > /dev/null; then
    echo "有新消息！"
    # 发送提醒
  fi
  sleep 300
done
```
