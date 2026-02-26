---
name: x-scraper
description: X (Twitter) 抓取 skill - 通过 agent-browser (CDP) 抓取指定用户推文或首页推荐流，支持关键词过滤、Tab 切换、多格式输出。使用场景：按用户/关键词抓取时间线、查看首页推荐流、生成 RSS/JSON/Markdown。
---

# X 抓取 (x-scraper)

## 概述

通过已连接 CDP 的浏览器（agent-browser）抓取 X (Twitter) 内容，包含两个脚本：

1. **scrape_x_user.sh** — 抓取指定用户时间线，可选关键词过滤
2. **scrape_x_home.sh** — 抓取当前登录用户的首页推荐流（For you / Following）

输出格式统一支持 Markdown / RSS / JSON。

## 工具路径

- 用户抓取：`.claude/skills/x-scraper/scrape_x_user.sh`
- 首页推荐：`.claude/skills/x-scraper/scrape_x_home.sh`
- 依赖：`agent-browser`（CDP 已连接且已登录 X）、`python3`

---

## 1. 用户帖子抓取 (scrape_x_user.sh)

按用户名抓取最新帖子，可选关键词搜索过滤。

### 用法

```bash
./.claude/skills/x-scraper/scrape_x_user.sh [-u <username>] [-k <keyword>] [-p <cdp_port>] [-n <max_scrolls>] [-o <output_file>] [-f <format>]
```

### 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `-u` | X 用户名（不带 @） | Alibaba_Qwen |
| `-k` | 搜索关键词（可选，不指定则抓取用户全部最新帖子） | - |
| `-p` | CDP 端口 | 9222 |
| `-n` | 最大滚动次数 | 10 |
| `-o` | 输出文件路径 | stdout |
| `-f` | 格式：`md` \| `rss` \| `json` | md |

### 示例

```bash
./.claude/skills/x-scraper/scrape_x_user.sh
./.claude/skills/x-scraper/scrape_x_user.sh -k qwen3
./.claude/skills/x-scraper/scrape_x_user.sh -u chenchengpro -k claw -f rss -o feed.xml
./.claude/skills/x-scraper/scrape_x_user.sh -u chenchengpro -f json -n 20 -o data.json
```

---

## 2. 首页推荐流抓取 (scrape_x_home.sh)

抓取当前登录用户的 X 首页推荐内容，支持 For you / Following 两个 Tab 切换。

### 用法

```bash
./.claude/skills/x-scraper/scrape_x_home.sh [-t <tab>] [-p <cdp_port>] [-n <max_scrolls>] [-o <output_file>] [-f <format>]
```

### 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `-t` | 推荐 Tab：`foryou` \| `following` | foryou |
| `-p` | CDP 端口 | 9222 |
| `-n` | 最大滚动次数 | 5 |
| `-o` | 输出文件路径 | stdout |
| `-f` | 格式：`md` \| `rss` \| `json` | md |

### 输出字段

每条帖子包含：`author`（作者名 + handle）、`time`（ISO 时间戳）、`text`（正文）、`link`（帖子链接）、`hasMedia`（是否含图片/视频）、`retweet`（转推/置顶上下文）

### 示例

```bash
./.claude/skills/x-scraper/scrape_x_home.sh                           # 抓取 For you 推荐流
./.claude/skills/x-scraper/scrape_x_home.sh -t following -n 10        # 抓取 Following 时间线
./.claude/skills/x-scraper/scrape_x_home.sh -f json -o feed.json      # JSON 输出到文件
./.claude/skills/x-scraper/scrape_x_home.sh -n 3 -f rss -o home.xml   # 少量抓取，RSS 输出
```
