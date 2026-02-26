#!/usr/bin/env bash
# ============================================================
# scrape_x_user.sh — 通过 agent-browser (CDP) 抓取 X 用户帖子
#
# 用法:
#   ./scrape_x_user.sh [-u <username>] [-k <keyword>] [-p <cdp_port>] [-n <max_scrolls>] [-o <output_file>] [-f <format>]
#
# 参数:
#   -u  X 用户名 (不带@)，默认 Alibaba_Qwen
#   -k  搜索关键词，可选（不指定则抓取用户所有最新帖子）
#   -p  CDP 端口号，默认 9222
#   -n  最大滚动次数，默认 10
#   -o  输出文件路径，默认 stdout
#   -f  输出格式: md (Markdown, 默认) | rss (RSS XML) | json (原始 JSON)
#
# 依赖:
#   - agent-browser (已通过 CDP 连接到运行中的浏览器)
#   - python3
#
# 示例:
#   ./scrape_x_user.sh                                    # 抓取 Alibaba_Qwen 所有最新帖子
#   ./scrape_x_user.sh -k qwen3                           # 抓取 Alibaba_Qwen 含 qwen3 的帖子
#   ./scrape_x_user.sh -u chenchengpro -k claw -f rss -o feed.xml
#   ./scrape_x_user.sh -u chenchengpro -f json -n 20 -o data.json
# ============================================================

set -euo pipefail

# ---------- 默认参数 ----------
CDP_PORT=9222
MAX_SCROLLS=10
OUTPUT_FILE=""
USERNAME="Alibaba_Qwen"
KEYWORD=""
FORMAT="md"

# ---------- 解析参数 ----------
while getopts "u:k:p:n:o:f:h" opt; do
  case $opt in
    u) USERNAME="$OPTARG" ;;
    k) KEYWORD="$OPTARG" ;;
    p) CDP_PORT="$OPTARG" ;;
    n) MAX_SCROLLS="$OPTARG" ;;
    o) OUTPUT_FILE="$OPTARG" ;;
    f) FORMAT="$OPTARG" ;;
    h)
      head -26 "$0" | tail -24
      exit 0
      ;;
    *)
      echo "用法: $0 [-u <username>] [-k <keyword>] [-p <cdp_port>] [-n <max_scrolls>] [-o <output_file>] [-f md|rss|json]" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$USERNAME" ]]; then
  echo "错误: 必须指定 -u <username>" >&2
  exit 1
fi

if [[ "$FORMAT" != "md" && "$FORMAT" != "rss" && "$FORMAT" != "json" ]]; then
  echo "错误: 格式必须为 md, rss 或 json" >&2
  exit 1
fi

# ---------- 工具函数 ----------
AB="agent-browser --cdp $CDP_PORT"
TMPDIR_SCRAPER=$(mktemp -d)
TWEETS_JSON="$TMPDIR_SCRAPER/tweets.json"

cleanup() {
  rm -rf "$TMPDIR_SCRAPER"
}
trap cleanup EXIT

log() {
  echo "[$(date '+%H:%M:%S')] $*" >&2
}

# ---------- 主流程 ----------

# 1. 构建目标 URL 并导航
if [[ -n "$KEYWORD" ]]; then
  ENCODED_QUERY=$(python3 -c "import urllib.parse; print(urllib.parse.quote('from:${USERNAME} ${KEYWORD}'))")
  TARGET_URL="https://x.com/search?q=${ENCODED_QUERY}&src=typed_query&f=live"
  log "正在导航到搜索页: from:${USERNAME} ${KEYWORD}"
else
  TARGET_URL="https://x.com/${USERNAME}"
  log "正在导航到用户主页: @${USERNAME}"
fi
$AB open "$TARGET_URL" >/dev/null 2>&1
sleep 3

# 2. 等待页面加载
log "等待页面加载..."
$AB wait --load networkidle >/dev/null 2>&1 || true
sleep 2

# 3. 滚动 + 提取帖子内容
PREV_COUNT=0
echo "[]" > "$TWEETS_JSON"

for ((i = 1; i <= MAX_SCROLLS; i++)); do
  log "第 ${i}/${MAX_SCROLLS} 轮抓取..."

  # 用 eval 提取帖子，过滤广告
  EVAL_TMPFILE="$TMPDIR_SCRAPER/eval_${i}.json"
  $AB eval "
    JSON.stringify(
      Array.from(document.querySelectorAll('article[data-testid=\"tweet\"]'))
        .filter(el => !el.querySelector('[data-testid=\"placementTracking\"]'))
        .filter(el => {
          const nameEl = el.querySelector('[data-testid=\"User-Name\"]');
          return nameEl && nameEl.textContent.includes('$USERNAME');
        })
        .map(el => {
          const tweetText = el.querySelector('[data-testid=\"tweetText\"]');
          const time = el.querySelector('time');
          const nameEl = el.querySelector('[data-testid=\"User-Name\"]');
          const linkEl = el.querySelector('a[href*=\"/status/\"]');
          return JSON.stringify({
            author: nameEl ? nameEl.textContent.trim() : '',
            time: time ? time.getAttribute('datetime') : '',
            text: tweetText ? tweetText.innerText.trim() : '',
            link: linkEl ? 'https://x.com' + linkEl.getAttribute('href') : ''
          });
        })
        .map(s => JSON.parse(s))
    )
  " > "$EVAL_TMPFILE" 2>/dev/null || echo "[]" > "$EVAL_TMPFILE"

  # 用 python 合并去重
  python3 - "$TWEETS_JSON" "$EVAL_TMPFILE" << 'PYEOF'
import json, sys

existing_file = sys.argv[1]
new_file = sys.argv[2]

try:
    with open(existing_file, "r") as f:
        existing = json.load(f)
except:
    existing = []

try:
    with open(new_file, "r") as f:
        raw = f.read().strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = json.loads(raw)
        new_tweets = json.loads(raw) if isinstance(raw, str) else raw
except:
    new_tweets = []

def get_key(item):
    if isinstance(item, dict):
        return item.get("text", "")[:150]
    return str(item)[:150]

seen = set()
for t in existing:
    seen.add(get_key(t))

merged = list(existing)
for t in (new_tweets if isinstance(new_tweets, list) else []):
    key = get_key(t)
    if key not in seen:
        seen.add(key)
        merged.append(t)

with open(existing_file, "w") as f:
    json.dump(merged, f, ensure_ascii=False)

print(len(merged))
PYEOF

  CURRENT_COUNT=$(python3 -c "import json; print(len(json.load(open('$TWEETS_JSON'))))" 2>/dev/null || echo "0")
  log "  已收集 ${CURRENT_COUNT} 条帖子 (本轮新增 $((CURRENT_COUNT - PREV_COUNT)))"

  if [[ "$CURRENT_COUNT" -eq "$PREV_COUNT" && "$i" -gt 1 ]]; then
    log "没有更多新帖子，停止滚动"
    break
  fi
  PREV_COUNT=$CURRENT_COUNT

  $AB scroll down 1200 >/dev/null 2>&1
  sleep 2
done

# 4. 格式化输出
log "正在格式化输出 (格式: $FORMAT)..."

FORMAT_OUTPUT=$(python3 - "$TWEETS_JSON" "$USERNAME" "$KEYWORD" "$FORMAT" << 'PYEOF'
import json, sys, html
from datetime import datetime, timezone
from email.utils import format_datetime

tweets_file = sys.argv[1]
username = sys.argv[2]
keyword = sys.argv[3]
fmt = sys.argv[4]

with open(tweets_file, "r") as f:
    tweets = json.load(f)

def parse_time(t):
    """解析 ISO 时间字符串"""
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except:
        return None

def get_text(tweet):
    if isinstance(tweet, dict):
        return tweet.get("text", "")
    return str(tweet)

def get_time(tweet):
    if isinstance(tweet, dict):
        return parse_time(tweet.get("time", ""))
    return None

def get_link(tweet):
    if isinstance(tweet, dict):
        return tweet.get("link", "")
    return ""

# ==================== Markdown ====================
if fmt == "md":
    print("# X 用户帖子抓取结果")
    print()
    print(f"- **用户**: @{username}")
    print(f"- **关键词**: {keyword if keyword else '(全部帖子)'}")
    print(f"- **抓取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"- **帖子数量**: {len(tweets)}")
    print()
    print("---")
    print()
    for i, tweet in enumerate(tweets, 1):
        dt = get_time(tweet)
        text = get_text(tweet)
        link = get_link(tweet)
        time_str = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
        print(f"## 帖子 {i}")
        if time_str:
            print(f"> 🕐 {time_str}")
        if link:
            print(f"> 🔗 {link}")
        print()
        if text:
            for line in text.split("\n"):
                line = line.strip()
                if line:
                    print(line)
        print()
        print("---")
        print()

# ==================== RSS ====================
elif fmt == "rss":
    now_rfc822 = format_datetime(datetime.now(timezone.utc))
    feed_link = f"https://x.com/{username}"

    print('<?xml version="1.0" encoding="UTF-8"?>')
    print('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">')
    print('  <channel>')
    kw_label = html.escape(keyword) if keyword else "全部帖子"
    print(f'    <title>@{username} - {kw_label}</title>')
    print(f'    <link>{feed_link}</link>')
    print(f'    <description>X 用户 @{username} 的{kw_label}</description>')
    print(f'    <language>zh-cn</language>')
    print(f'    <lastBuildDate>{now_rfc822}</lastBuildDate>')
    print(f'    <generator>scrape_x_user.sh</generator>')
    print()

    for tweet in tweets:
        dt = get_time(tweet)
        text = get_text(tweet)
        link = get_link(tweet) or feed_link
        # 标题取正文前 80 字符
        title = text[:80].replace("\n", " ").strip()
        if len(text) > 80:
            title += "..."

        print('    <item>')
        print(f'      <title>{html.escape(title)}</title>')
        print(f'      <link>{html.escape(link)}</link>')
        print(f'      <guid isPermaLink="true">{html.escape(link)}</guid>')
        if dt:
            print(f'      <pubDate>{format_datetime(dt)}</pubDate>')
        # description 用 CDATA 包裹保留原始格式
        desc_lines = []
        for line in text.split("\n"):
            line = line.strip()
            if line:
                desc_lines.append(f"<p>{html.escape(line)}</p>")
        print(f'      <description><![CDATA[{"".join(desc_lines)}]]></description>')
        print(f'      <author>@{username}</author>')
        print('    </item>')

    print('  </channel>')
    print('</rss>')

# ==================== JSON ====================
elif fmt == "json":
    output = {
        "meta": {
            "username": username,
            "keyword": keyword,
            "scraped_at": datetime.now().isoformat(),
            "count": len(tweets)
        },
        "tweets": tweets
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
PYEOF
)

# 5. 输出结果
if [[ -n "$OUTPUT_FILE" ]]; then
  echo "$FORMAT_OUTPUT" > "$OUTPUT_FILE"
  log "结果已写入: $OUTPUT_FILE"
else
  echo "$FORMAT_OUTPUT"
fi

log "抓取完成!"
