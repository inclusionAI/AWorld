---
name: html-to-image
description: HTML 转图片 skill - 将 HTML 文件或内容通过 agent-browser 渲染并截图为图片。适用于生成信息图、社交媒体配图、数据可视化截图等场景。
---

# HTML 转图片 (html-to-image)

## 概述

将 HTML 文件或内容通过 agent-browser 渲染为图片。典型用法：Claude 生成精美 HTML → 本 skill 截图 → 得到可直接发布的图片。

## 工具路径

- 脚本：`.claude/skills/html-to-image/html_to_image.sh`
- 依赖：`agent-browser`（CDP 已连接）、`python3`

## 用法

```bash
./html_to_image.sh -o <output> [-f <html_file> | -c <html_content>] [-w <width>] [-p <cdp_port>] [--full]
```

### 参数

| 参数 | 说明 | 必填 | 默认 |
|------|------|------|------|
| `-o` | 输出图片路径（.png） | 是 | - |
| `-f` | HTML 文件路径（与 `-c` 二选一） | 二选一 | - |
| `-c` | HTML 内容字符串（与 `-f` 二选一） | 二选一 | - |
| `-w` | 视口宽度 | 否 | 1080 |
| `-e` | 视口高度（不指定则全页截图） | 否 | - |
| `-p` | CDP 端口 | 否 | 9222 |
| `--full` | 全页截图（忽略视口高度限制） | 否 | 默认开启 |

### 示例

```bash
# 从 HTML 文件截图
./html_to_image.sh \
  -f card.html -o card.png

# 直接传入 HTML 内容
./html_to_image.sh \
  -c '<html><body><h1>Hello</h1></body></html>' \
  -o hello.png

# 指定宽度（适配手机尺寸）
./html_to_image.sh \
  -f infographic.html -o output.png -w 750

# 固定视口截图（非全页）
./html_to_image.sh \
  -f page.html -o output.png -w 1080 -e 1920 --no-full
```

### 典型工作流

1. Claude 根据内容生成精美 HTML（信息图、卡片等）
2. 使用本 skill 截图为 PNG
3. 将截图传给 `xhs-publisher` 发布到小红书

```bash
# 生成图片
./html_to_image.sh -f card.html -o card.png

# 发布到小红书
./.claude/skills/xhs-publisher/publish_xhs.sh -t "标题" -c "正文" -i card.png
```
