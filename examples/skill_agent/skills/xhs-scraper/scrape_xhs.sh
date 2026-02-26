#!/usr/bin/env bash
# ============================================================
# scrape_xhs.sh — 通过 agent-browser (CDP) 抓取小红书搜索结果
#
# 用法:
#   ./scrape_xhs.sh -k <keyword> [-p <cdp_port>] [-n <max_scrolls>] [-d <detail_count>] [-o <output_file>] [-f <format>]
#
# 参数:
#   -k  搜索关键词，必填
#   -p  CDP 端口号，默认 9222
#   -n  最大滚动次数（列表页），默认 5
#   -d  进入详情页获取正文的帖子数量，默认 10（0 = 仅抓列表）
#   -o  输出文件路径，默认 stdout
#   -f  输出格式: md (Markdown, 默认) | rss (RSS XML) | json (原始 JSON)
#
# 依赖:
#   - agent-browser (已通过 CDP 连接到运行中的浏览器)
#   - python3
#
# 示例:
#   ./scrape_xhs.sh -k "Agent开发工程师"
#   ./scrape_xhs.sh -k "AI Agent岗位" -d 5 -f rss -o feed.xml
#   ./scrape_xhs.sh -k "大模型面经" -n 10 -d 20 -f json -o data.json
# ============================================================

set -euo pipefail

# ---------- 默认参数 ----------
CDP_PORT=9222
MAX_SCROLLS=5
DETAIL_COUNT=10
OUTPUT_FILE=""
KEYWORD=""
FORMAT="md"

# ---------- 解析参数 ----------
while getopts "k:p:n:d:o:f:h" opt; do
  case $opt in
    k) KEYWORD="$OPTARG" ;;
    p) CDP_PORT="$OPTARG" ;;
    n) MAX_SCROLLS="$OPTARG" ;;
    d) DETAIL_COUNT="$OPTARG" ;;
    o) OUTPUT_FILE="$OPTARG" ;;
    f) FORMAT="$OPTARG" ;;
    h)
      head -27 "$0" | tail -25
      exit 0
      ;;
    *)
      echo "用法: $0 -k <keyword> [-p <cdp_port>] [-n <max_scrolls>] [-d <detail_count>] [-o <output_file>] [-f md|rss|json]" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$KEYWORD" ]]; then
  echo "错误: 必须指定 -k <keyword>" >&2
  exit 1
fi

if [[ "$FORMAT" != "md" && "$FORMAT" != "rss" && "$FORMAT" != "json" ]]; then
  echo "错误: 格式必须为 md, rss 或 json" >&2
  exit 1
fi

# ---------- 工具函数 ----------
AB="agent-browser --cdp $CDP_PORT"
TMPDIR_SCRAPER=$(mktemp -d)
POSTS_JSON="$TMPDIR_SCRAPER/posts.json"

cleanup() {
  rm -rf "$TMPDIR_SCRAPER"
}
trap cleanup EXIT

log() {
  echo "[$(date '+%H:%M:%S')] $*" >&2
}

# ---------- 主流程 ----------

# 1. 构建搜索 URL 并导航
ENCODED_KW=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${KEYWORD}'))")
TARGET_URL="https://www.xiaohongshu.com/search_result?keyword=${ENCODED_KW}&source=web_search_result_notes"

log "正在搜索小红书: ${KEYWORD}"
$AB open "$TARGET_URL" >/dev/null 2>&1
sleep 3

# 2. 等待页面加载
log "等待页面加载..."
$AB wait --load networkidle >/dev/null 2>&1 || true
sleep 2

# 3. 滚动列表页 + 提取帖子卡片信息
PREV_COUNT=0
echo "[]" > "$POSTS_JSON"

for ((i = 1; i <= MAX_SCROLLS; i++)); do
  log "第 ${i}/${MAX_SCROLLS} 轮抓取列表..."

  EVAL_TMPFILE="$TMPDIR_SCRAPER/eval_${i}.json"
  $AB eval "
    JSON.stringify(
      Array.from(document.querySelectorAll('section.note-item, div.note-item')).map(el => {
        const titleEl = el.querySelector('.title span') || el.querySelector('.title');
        const authorEl = el.querySelector('.author-wrapper .name, .name');
        const likesEl = el.querySelector('.like-wrapper .count, .count');
        const linkEl = el.querySelector('a[href*=\"/search_result/\"], a[href*=\"/explore/\"], a');
        const imgEl = el.querySelector('img');
        let href = '';
        if (linkEl) {
          href = linkEl.getAttribute('href') || '';
          if (href.startsWith('/')) href = 'https://www.xiaohongshu.com' + href;
        }
        return {
          title: titleEl ? titleEl.textContent.trim() : '',
          author: authorEl ? authorEl.textContent.trim() : '',
          likes: likesEl ? likesEl.textContent.trim() : '',
          link: href,
          cover: imgEl ? imgEl.getAttribute('src') || '' : ''
        };
      }).filter(p => p.title)
    )
  " > "$EVAL_TMPFILE" 2>/dev/null || echo "[]" > "$EVAL_TMPFILE"

  # 合并去重
  python3 - "$POSTS_JSON" "$EVAL_TMPFILE" << 'PYEOF'
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
        new_posts = json.loads(raw) if isinstance(raw, str) else raw
except:
    new_posts = []

def get_key(item):
    if isinstance(item, dict):
        return item.get("title", "")[:100]
    return str(item)[:100]

seen = set()
for t in existing:
    seen.add(get_key(t))

merged = list(existing)
for t in (new_posts if isinstance(new_posts, list) else []):
    key = get_key(t)
    if key and key not in seen:
        seen.add(key)
        merged.append(t)

with open(existing_file, "w") as f:
    json.dump(merged, f, ensure_ascii=False)

print(len(merged))
PYEOF

  CURRENT_COUNT=$(python3 -c "import json; print(len(json.load(open('$POSTS_JSON'))))" 2>/dev/null || echo "0")
  log "  已收集 ${CURRENT_COUNT} 条帖子 (本轮新增 $((CURRENT_COUNT - PREV_COUNT)))"

  if [[ "$CURRENT_COUNT" -eq "$PREV_COUNT" && "$i" -gt 1 ]]; then
    log "没有更多新帖子，停止滚动"
    break
  fi
  PREV_COUNT=$CURRENT_COUNT

  $AB scroll down 1200 >/dev/null 2>&1
  sleep 2
done

log "列表抓取完成，共 ${CURRENT_COUNT} 条"

# 4. 进入详情页获取正文（按 likes 排序取 top N）
if [[ "$DETAIL_COUNT" -gt 0 ]]; then
  log "开始获取前 ${DETAIL_COUNT} 条帖子的详情正文..."

  python3 - "$POSTS_JSON" "$DETAIL_COUNT" << 'PYEOF'
import json, sys

posts_file = sys.argv[1]
detail_count = int(sys.argv[2])

with open(posts_file, "r") as f:
    posts = json.load(f)

# 按 likes 排序（数字越大越前）
def parse_likes(s):
    s = str(s).strip()
    if not s:
        return 0
    # 处理 "1.2万" 这种格式
    if '万' in s:
        return int(float(s.replace('万', '')) * 10000)
    try:
        return int(s)
    except:
        return 0

posts.sort(key=lambda p: parse_likes(p.get("likes", "0")), reverse=True)

# 输出需要获取详情的帖子索引
indices = []
for i, p in enumerate(posts[:detail_count]):
    indices.append(i)

# 重写排序后的数据
with open(posts_file, "w") as f:
    json.dump(posts, f, ensure_ascii=False)

# 输出需要详情的数量
print(min(detail_count, len(posts)))
PYEOF

  DETAIL_ACTUAL=$(python3 -c "import json; posts=json.load(open('$POSTS_JSON')); print(min($DETAIL_COUNT, len(posts)))")

  for ((j = 0; j < DETAIL_ACTUAL; j++)); do
    TITLE=$(python3 -c "
import json
posts = json.load(open('$POSTS_JSON'))
print(posts[$j].get('title', ''))
")

    if [[ -z "$TITLE" ]]; then
      continue
    fi

    log "  获取详情 [$((j+1))/${DETAIL_ACTUAL}]: ${TITLE:0:40}..."

    # 将标题写入临时文件，避免 bash 引号转义问题
    TITLE_FILE="$TMPDIR_SCRAPER/title_${j}.txt"
    echo "$TITLE" > "$TITLE_FILE"
    TITLE_JSON=$(python3 -c "import json; print(json.dumps(open('$TITLE_FILE').read().strip()))")

    # 先滚回顶部
    $AB scroll up 50000 >/dev/null 2>&1
    sleep 1

    # 在列表中查找并点击 a.cover（触发弹窗，不要用 a[href^="/explore"] 会直跳 404）
    CLICK_RESULT="not_found"
    for ((s = 0; s < 8; s++)); do
      CLICK_RESULT=$($AB eval "
        (() => {
          const target = $TITLE_JSON;
          const items = document.querySelectorAll('section.note-item, div.note-item');
          for (const item of items) {
            const titleEl = item.querySelector('.title span') || item.querySelector('.title');
            if (titleEl && titleEl.textContent.trim().includes(target.substring(0, 15))) {
              const cover = item.querySelector('a.cover') || item.querySelector('a[href*=\"search_result\"]');
              if (cover) { cover.click(); return 'clicked'; }
            }
          }
          return 'not_found';
        })()
      " 2>/dev/null || echo "not_found")

      if [[ "$CLICK_RESULT" == *"clicked"* ]]; then
        break
      fi
      $AB scroll down 600 >/dev/null 2>&1
      sleep 1
    done

    if [[ "$CLICK_RESULT" == *"not_found"* ]]; then
      log "    跳过（未在页面中找到）"
      continue
    fi

    # 等待弹窗加载
    sleep 4

    # 提取正文（多选择器 fallback）+ 日期
    DETAIL_TMPFILE="$TMPDIR_SCRAPER/detail_${j}.json"
    $AB eval "
      (() => {
        const noteText = document.querySelector('#detail-desc')
          || document.querySelector('.note-content .desc .note-text')
          || document.querySelector('span.note-text');
        const title = document.querySelector('.note-content .title');
        const dateEl = document.querySelector('.note-content .bottom-container');
        // 如果 note-text 为空（图文帖），尝试从 scroller 获取
        let text = noteText ? noteText.innerText.trim() : '';
        if (!text) {
          const scroller = document.querySelector('.note-scroller .content, .note-scroller');
          if (scroller) {
            // 从 scroller 提取，去掉评论区
            const raw = scroller.innerText;
            const endIdx = raw.indexOf('条评论');
            text = endIdx > 0 ? raw.substring(raw.indexOf('\\n', endIdx) + 1).trim() : '';
          }
        }
        return JSON.stringify({
          text: text,
          title: title ? title.innerText.trim() : '',
          date: dateEl ? dateEl.innerText.trim() : ''
        });
      })()
    " > "$DETAIL_TMPFILE" 2>/dev/null || echo '{}' > "$DETAIL_TMPFILE"

    # 写回 JSON
    python3 - "$POSTS_JSON" "$j" "$DETAIL_TMPFILE" << 'PYEOF'
import json, sys

posts_file = sys.argv[1]
idx = int(sys.argv[2])
detail_file = sys.argv[3]

with open(posts_file, "r") as f:
    posts = json.load(f)

try:
    with open(detail_file, "r") as f:
        raw = f.read().strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = json.loads(raw)
        detail = json.loads(raw) if isinstance(raw, str) else raw
except:
    detail = {}

text = detail.get("text", "") if isinstance(detail, dict) else ""
date = detail.get("date", "") if isinstance(detail, dict) else ""

if idx < len(posts):
    if text:
        posts[idx]["detail"] = text
    if date:
        posts[idx]["date"] = date

with open(posts_file, "w") as f:
    json.dump(posts, f, ensure_ascii=False)

print(f"OK len={len(text)}")
PYEOF

    DETAIL_LEN=$(python3 -c "import json; p=json.load(open('$POSTS_JSON'))[$j]; print(len(p.get('detail','')))")
    if [[ "$DETAIL_LEN" -gt 0 ]]; then
      log "    已获取正文 (${DETAIL_LEN} 字符)"
    else
      log "    未获取到正文（可能是图文帖）"
    fi

    # 关闭弹窗
    $AB press Escape >/dev/null 2>&1
    sleep 1

    # 确认回到搜索页（防止意外跳转）
    CURRENT_URL=$($AB get url 2>/dev/null || echo "")
    if [[ "$CURRENT_URL" != *"search_result"* ]]; then
      log "    检测到页面跳转，返回搜索页..."
      $AB back >/dev/null 2>&1
      sleep 2
    fi
  done
fi

# 5. 格式化输出
log "正在格式化输出 (格式: $FORMAT)..."

FORMAT_OUTPUT=$(python3 - "$POSTS_JSON" "$KEYWORD" "$FORMAT" << 'PYEOF'
import json, sys, html
from datetime import datetime, timezone
from email.utils import format_datetime

posts_file = sys.argv[1]
keyword = sys.argv[2]
fmt = sys.argv[3]

with open(posts_file, "r") as f:
    posts = json.load(f)

def get_val(post, key, default=""):
    return post.get(key, default) if isinstance(post, dict) else default

# ==================== Markdown ====================
if fmt == "md":
    print("# 小红书搜索结果")
    print()
    print(f"- **关键词**: {keyword}")
    print(f"- **抓取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"- **帖子数量**: {len(posts)}")
    print()
    print("---")
    print()
    for i, post in enumerate(posts, 1):
        title = get_val(post, "title")
        author = get_val(post, "author")
        likes = get_val(post, "likes")
        link = get_val(post, "link")
        detail = get_val(post, "detail")
        date = get_val(post, "date")

        print(f"## {i}. {title}")
        meta_parts = []
        if author:
            meta_parts.append(f"👤 {author}")
        if likes:
            meta_parts.append(f"❤️ {likes}")
        if date:
            meta_parts.append(f"📅 {date}")
        if meta_parts:
            print(f"> {' | '.join(meta_parts)}")
        if link:
            print(f"> 🔗 {link}")
        print()
        if detail:
            for line in detail.split("\n"):
                line = line.strip()
                if line:
                    print(line)
            print()
        print("---")
        print()

# ==================== RSS ====================
elif fmt == "rss":
    now_rfc822 = format_datetime(datetime.now(timezone.utc))
    feed_link = "https://www.xiaohongshu.com"

    print('<?xml version="1.0" encoding="UTF-8"?>')
    print('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">')
    print('  <channel>')
    print(f'    <title>小红书 - {html.escape(keyword)}</title>')
    print(f'    <link>{feed_link}</link>')
    print(f'    <description>小红书搜索「{html.escape(keyword)}」的结果</description>')
    print(f'    <language>zh-cn</language>')
    print(f'    <lastBuildDate>{now_rfc822}</lastBuildDate>')
    print(f'    <generator>scrape_xhs.sh</generator>')
    print()

    for post in posts:
        title = get_val(post, "title")
        author = get_val(post, "author")
        link = get_val(post, "link") or feed_link
        detail = get_val(post, "detail")
        likes = get_val(post, "likes")

        desc_text = detail if detail else title
        desc_lines = []
        for line in desc_text.split("\n"):
            line = line.strip()
            if line:
                desc_lines.append(f"<p>{html.escape(line)}</p>")
        if likes:
            desc_lines.append(f"<p>❤️ {html.escape(str(likes))} 赞</p>")

        print('    <item>')
        print(f'      <title>{html.escape(title)}</title>')
        print(f'      <link>{html.escape(link)}</link>')
        print(f'      <guid isPermaLink="false">{html.escape(title[:80])}</guid>')
        print(f'      <description><![CDATA[{"".join(desc_lines)}]]></description>')
        if author:
            print(f'      <author>{html.escape(author)}</author>')
        print('    </item>')

    print('  </channel>')
    print('</rss>')

# ==================== JSON ====================
elif fmt == "json":
    output = {
        "meta": {
            "platform": "xiaohongshu",
            "keyword": keyword,
            "scraped_at": datetime.now().isoformat(),
            "count": len(posts)
        },
        "posts": posts
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
