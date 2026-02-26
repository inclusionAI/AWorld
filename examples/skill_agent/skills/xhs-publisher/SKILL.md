---
name: xhs-publisher
description: 小红书发布 skill - 通过 agent-browser (CDP) 自动发布小红书图文笔记，支持多图上传、标题正文填写、一键发布。使用场景：自动化发布图文笔记到小红书创作中心。
---

# 小红书发布 (xhs-publisher)

## 概述

通过已连接 CDP 的浏览器（agent-browser）自动发布小红书图文笔记：导航到创作中心、上传图片、填写标题和正文、点击发布。

## 工具路径

- 脚本：`.claude/skills/xhs-publisher/publish_xhs.sh`
- 依赖：`agent-browser`（CDP 已连接且已登录小红书）、`python3`

## 用法

```bash
./.claude/skills/xhs-publisher/publish_xhs.sh -t <title> -i <images> [-c <content> | -f <content_file>] [-p <cdp_port>]
```

### 参数

| 参数 | 说明 | 必填 | 默认 |
|------|------|------|------|
| `-t` | 标题（≤20 字符） | 是 | - |
| `-i` | 图片路径，逗号分隔或多次 `-i`（至少 1 张） | 是 | - |
| `-c` | 正文内容（与 `-f` 二选一） | 二选一 | - |
| `-f` | 从文件读取正文（与 `-c` 二选一） | 二选一 | - |
| `-p` | CDP 端口 | 否 | 9222 |

### 示例

```bash
# 单图 + 短正文
./.claude/skills/xhs-publisher/publish_xhs.sh \
  -t "测试帖子" \
  -c "这是一条测试帖子" \
  -i /path/to/test.png

# 多图 + 文件正文
./.claude/skills/xhs-publisher/publish_xhs.sh \
  -t "多图测试" \
  -f content.txt \
  -i img1.png,img2.png,img3.png

# 多次 -i 指定图片
./.claude/skills/xhs-publisher/publish_xhs.sh \
  -t "分享日记" \
  -c "今天的风景真好" \
  -i photo1.jpg -i photo2.jpg
```

### 注意事项

- 浏览器需已登录小红书创作者中心
- 图片文件必须存在且为有效图片格式
- 标题不超过 20 个字符
- 正文通过 `-c` 直接传入或 `-f` 从文件读取，二者必选其一
