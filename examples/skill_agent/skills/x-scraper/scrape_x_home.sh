#!/usr/bin/env bash
# ============================================================
# scrape_x_home.sh — 通过 agent-browser (CDP) 抓取 X 首页推荐流
#
# 用法:
#   ./scrape_x_home.sh [-t <tab>] [-p <cdp_port>] [-n <max_scrolls>] [-o <output_file>] [-f <format>]
#
# 参数:
#   -t  推荐 Tab: foryou (默认) | following
#   -p  CDP 端口号，默认 9222
#   -n  最大滚动次数，默认 5
#   -o  输出文件路径，默认 stdout
#   -f  输出格式: md (Markdown, 默认) | rss (RSS XML) | json (原始 JSON)
#
# 依赖:
#   - agent-browser (已通过 CDP 连接到运行中的浏览器，且已登录 X)
#   - python3
#
# 示例:
#   ./scrape_x_home.sh                           # 抓取 For you 推荐流
#   ./scrape_x_home.sh -t following -n 10        # 抓取 Following 时间线
#   ./scrape_x_home.sh -f json -o feed.json      # JSON 输出到文件
#   ./scrape_x_home.sh -n 3 -f rss -o home.xml   # 少量抓取，RSS 输出
# ============================================================

set -euo pipefail

# ---------- 默认参数 ----------
CDP_PORT=9222
MAX_SCROLLS=5
OUTPUT_FILE=""
TAB="foryou"
FORMAT="md"

# ---------- 解析参数 ----------
while getopts "t:p:n:o:f:h" opt; do
  case $opt in
    t) TAB="$OPTARG" ;;
    p) CDP_PORT="$OPTARG" ;;
    n) MAX_SCROLLS="$OPTARG" ;;
    o) OUTPUT_FILE="$OPTARG" ;;
    f) FORMAT="$OPTARG" ;;
    h)
      head -26 "$0" | tail -24
      exit 0
      ;;
    *)
      echo "用法: $0 [-t foryou|following] [-p <cdp_port>] [-n <max_scrolls>] [-o <output_file>] [-f md|rss|json]" >&2
      exit 1
      ;;
  esac
done

if [[ "$TAB" != "foryou" && "$TAB" != "following" ]]; then
  echo "错误: -t 必须为 foryou 或 following" >&2
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

# 1. 导航到 X 首页
log "正在导航到 X 首页..."
$AB open "https://x.com/home" >/dev/null 2>&1
sleep 3

# 2. 等待页面加载
log "等待页面加载..."
$AB wait --load networkidle >/dev/null 2>&1 || true
sleep 2

# 3. 切换到目标 Tab
if [[ "$TAB" == "foryou" ]]; then
  TAB_LABEL="For you"
else
  TAB_LABEL="Following"
fi

log "切换到 Tab: ${TAB_LABEL}..."
$AB eval "
  (() => {
    const tabs = document.querySelectorAll('[role=\"tab\"]');
    for (const tab of tabs) {
      if (tab.textContent.trim() === '$TAB_LABEL') {
        const active = tab.getAttribute('aria-selected');
        if (active !== 'true') tab.click();
        return 'switched to $TAB_LABEL';
      }
    }
    return 'tab not found, staying on current';
  })()
" >/dev/null 2>&1
sleep 2

# 4. 滚动 + 提取帖子内容
PREV_COUNT=0
echo "[]" > "$TWEETS_JSON"

for ((i = 1; i <= MAX_SCROLLS; i++)); do
  log "第 ${i}/${MAX_SCROLLS} 轮抓取..."

  # 提取帖子，过滤广告（placementTracking）
  EVAL_TMPFILE="$TMPDIR_SCRAPER/eval_${i}.json"
  $AB eval "
    JSON.stringify(
      Array.from(document.querySelectorAll('article[data-testid=\"tweet\"]'))
        .filter(el => !el.querySelector('[data-testid=\"placementTracking\"]'))
        .map(el => {
          const tweetText = el.querySelector('[data-testid=\"tweetText\"]');
          const time = el.querySelector('time');
          const nameEl = el.querySelector('[data-testid=\"User-Name\"]');
          const linkEl = el.querySelector('a[href*=\"/status/\"]');
          const imgEls = el.querySelectorAll('img[src*=\"pbs.twimg.com/media\"]');
          const retweetEl = el.querySelector('[data-testid=\"socialContext\"]');
          return {
            author: nameEl ? nameEl.textContent.trim().replace(/\\n/g, ' ') : '',
            time: time ? time.getAttribute('datetime') : '',
            text: tweetText ? tweetText.innerText.trim() : '',
            link: linkEl ? 'https://x.com' + linkEl.getAttribute('href') : '',
            hasMedia: imgEls.length > 0,
            retweet: retweetEl ? retweetEl.textContent.trim() : ''
          };
        })
        .filter(t => t.text)
    )
  " > "$EVAL_TMPFILE" 2>/dev/null || echo "[]" > "$EVAL_TMPFILE"

  # 合并去重
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
    if key and key not in seen:
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

  $AB scroll down 1500 >/dev/null 2>&1
  sleep 3
done

# 5. 格式化输出
log "正在格式化输出 (格式: $FORMAT)..."

FORMAT_OUTPUT=$(python3 - "$TWEETS_JSON" "$TAB_LABEL" "$FORMAT" << 'PYEOF'
import json, sys, html, re
from datetime import datetime, timezone
from email.utils import format_datetime

tweets_file = sys.argv[1]
tab_label = sys.argv[2]
fmt = sys.argv[3]

with open(tweets_file, "r") as f:
    tweets = json.load(f)

def parse_time(t):
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except:
        return None

def parse_author(raw):
    """解析 author 字段，提取 name 和 handle"""
    raw = raw.strip()
    # 格式通常为: "Name@handle · 17h" 或 "Name @handle · 17h"
    m = re.match(r'^(.+?)@(\w+)', raw)
    if m:
        name = m.group(1).strip()
        handle = "@" + m.group(2)
        return name, handle
    return raw, ""

def get_val(tweet, key, default=""):
    return tweet.get(key, default) if isinstance(tweet, dict) else default

# ==================== Markdown ====================
if fmt == "md":
    print(f"# X 推荐流 - {tab_label}")
    print()
    print(f"- **抓取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"- **帖子数量**: {len(tweets)}")
    print()
    print("---")
    print()
    for i, tweet in enumerate(tweets, 1):
        text = get_val(tweet, "text")
        time_raw = get_val(tweet, "time")
        link = get_val(tweet, "link")
        author_raw = get_val(tweet, "author")
        retweet = get_val(tweet, "retweet")
        has_media = get_val(tweet, "hasMedia", False)

        name, handle = parse_author(author_raw)
        dt = parse_time(time_raw)
        time_str = dt.strftime("%Y-%m-%d %H:%M") if dt else ""

        print(f"## {i}. {name} {handle}")
        meta = []
        if time_str:
            meta.append(f"🕐 {time_str}")
        if has_media:
            meta.append("🖼️ 含图片/视频")
        if retweet:
            meta.append(f"🔁 {retweet}")
        if meta:
            print(f"> {' | '.join(meta)}")
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
    feed_link = "https://x.com/home"

    print('<?xml version="1.0" encoding="UTF-8"?>')
    print('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">')
    print('  <channel>')
    print(f'    <title>X 推荐流 - {html.escape(tab_label)}</title>')
    print(f'    <link>{feed_link}</link>')
    print(f'    <description>X {html.escape(tab_label)} 推荐内容</description>')
    print(f'    <language>zh-cn</language>')
    print(f'    <lastBuildDate>{now_rfc822}</lastBuildDate>')
    print(f'    <generator>scrape_x_home.sh</generator>')
    print()

    for tweet in tweets:
        text = get_val(tweet, "text")
        link = get_val(tweet, "link") or feed_link
        author_raw = get_val(tweet, "author")
        time_raw = get_val(tweet, "time")
        dt = parse_time(time_raw)
        name, handle = parse_author(author_raw)

        title = f"[{name}] " + text[:70].replace("\n", " ").strip()
        if len(text) > 70:
            title += "..."

        desc_lines = []
        for line in text.split("\n"):
            line = line.strip()
            if line:
                desc_lines.append(f"<p>{html.escape(line)}</p>")

        print('    <item>')
        print(f'      <title>{html.escape(title)}</title>')
        print(f'      <link>{html.escape(link)}</link>')
        print(f'      <guid isPermaLink="true">{html.escape(link)}</guid>')
        if dt:
            print(f'      <pubDate>{format_datetime(dt)}</pubDate>')
        print(f'      <description><![CDATA[{"".join(desc_lines)}]]></description>')
        print(f'      <author>{html.escape(name)} {html.escape(handle)}</author>')
        print('    </item>')

    print('  </channel>')
    print('</rss>')

# ==================== JSON ====================
elif fmt == "json":
    # 对 author 做结构化处理
    processed = []
    for tweet in tweets:
        t = dict(tweet)
        name, handle = parse_author(t.get("author", ""))
        t["author_name"] = name
        t["author_handle"] = handle
        processed.append(t)

    output = {
        "meta": {
            "platform": "x",
            "tab": tab_label,
            "scraped_at": datetime.now().isoformat(),
            "count": len(processed)
        },
        "tweets": processed
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
PYEOF
)

# 6. 输出结果
if [[ -n "$OUTPUT_FILE" ]]; then
  echo "$FORMAT_OUTPUT" > "$OUTPUT_FILE"
  log "结果已写入: $OUTPUT_FILE"
else
  echo "$FORMAT_OUTPUT"
fi

log "抓取完成!"
