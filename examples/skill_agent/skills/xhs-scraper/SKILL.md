---
name: xhs-scraper
description: 小红书搜索抓取 skill - 通过 agent-browser (CDP) 抓取小红书搜索结果，支持列表+详情、多格式输出。使用场景：按关键词抓取笔记列表与正文、生成 RSS/JSON/Markdown。
---

# 小红书抓取 (xhs-scraper)

## 概述

通过已连接 CDP 的浏览器（agent-browser）抓取小红书搜索结果：列表页滚动采集卡片信息，可选进入详情页获取正文，输出为 Markdown / RSS / JSON。

## 工具路径

- 脚本：`.claude/skills/xhs-scraper/scrape_xhs.sh`
- 依赖：`agent-browser`（CDP 已连接）、`python3`

## 用法

```bash
./.claude/skills/xhs-scraper/scrape_xhs.sh -k <keyword> [-p <cdp_port>] [-n <max_scrolls>] [-d <detail_count>] [-o <output_file>] [-f <format>]
```

### 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `-k` | 搜索关键词（必填） | - |
| `-p` | CDP 端口 | 9222 |
| `-n` | 列表页最大滚动次数 | 5 |
| `-d` | 进入详情页获取正文的条数（0=仅列表） | 10 |
| `-o` | 输出文件路径 | stdout |
| `-f` | 格式：`md` \| `rss` \| `json` | md |

### 示例

```bash
./.claude/skills/xhs-scraper/scrape_xhs.sh -k "Agent开发工程师"
./.claude/skills/xhs-scraper/scrape_xhs.sh -k "AI Agent岗位" -d 5 -f rss -o feed.xml
./.claude/skills/xhs-scraper/scrape_xhs.sh -k "大模型面经" -n 10 -d 20 -f json -o data.json
```
