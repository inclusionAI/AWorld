#!/usr/bin/env bash
# ============================================================
# publish_xhs.sh — 通过 agent-browser (CDP) 自动发布小红书图文笔记
#
# 用法:
#   ./publish_xhs.sh -t <title> -i <images> [-c <content> | -f <content_file>] [-p <cdp_port>]
#
# 参数:
#   -t  标题（≤20 字符）
#   -i  图片路径，逗号分隔或多次 -i（至少 1 张）
#   -c  正文内容（与 -f 二选一）
#   -f  从文件读取正文（与 -c 二选一）
#   -p  CDP 端口号，默认 9222
#
# 依赖:
#   - agent-browser (已通过 CDP 连接到运行中的浏览器，且已登录小红书)
#   - python3
#
# 示例:
#   ./publish_xhs.sh -t "测试帖子" -c "这是一条测试帖子" -i /path/to/test.png
#   ./publish_xhs.sh -t "多图测试" -f content.txt -i img1.png,img2.png,img3.png
# ============================================================

set -euo pipefail

# ---------- 默认参数 ----------
CDP_PORT=9222
TITLE=""
CONTENT=""
CONTENT_FILE=""
IMAGE_ARGS=()

# ---------- 解析参数 ----------
while getopts "t:i:c:f:p:h" opt; do
  case $opt in
    t) TITLE="$OPTARG" ;;
    i) IMAGE_ARGS+=("$OPTARG") ;;
    c) CONTENT="$OPTARG" ;;
    f) CONTENT_FILE="$OPTARG" ;;
    p) CDP_PORT="$OPTARG" ;;
    h)
      head -24 "$0" | tail -22
      exit 0
      ;;
    *)
      echo "用法: $0 -t <title> -i <images> [-c <content> | -f <content_file>] [-p <cdp_port>]" >&2
      exit 1
      ;;
  esac
done

# ---------- 工具函数 ----------
AB="agent-browser --cdp $CDP_PORT"
TMPDIR_PUB=$(mktemp -d)

cleanup() {
  rm -rf "$TMPDIR_PUB"
}
trap cleanup EXIT

log() {
  echo "[$(date '+%H:%M:%S')] $*" >&2
}

# ---------- 参数校验 ----------

# 校验标题
if [[ -z "$TITLE" ]]; then
  echo "错误: 必须指定 -t <title>" >&2
  exit 1
fi

TITLE_LEN=$(python3 -c "print(len('${TITLE//\'/\\\'}'))")
if [[ "$TITLE_LEN" -gt 20 ]]; then
  echo "错误: 标题长度 ${TITLE_LEN} 超过 20 字符限制" >&2
  exit 1
fi

# 解析图片列表（支持逗号分隔和多次 -i）
IMAGES=()
for arg in "${IMAGE_ARGS[@]}"; do
  IFS=',' read -ra SPLIT <<< "$arg"
  for img in "${SPLIT[@]}"; do
    img=$(echo "$img" | xargs)  # trim 空格
    if [[ -n "$img" ]]; then
      IMAGES+=("$img")
    fi
  done
done

if [[ ${#IMAGES[@]} -eq 0 ]]; then
  echo "错误: 必须指定至少 1 张图片 (-i)" >&2
  exit 1
fi

# 校验图片文件存在
for img in "${IMAGES[@]}"; do
  if [[ ! -f "$img" ]]; then
    echo "错误: 图片文件不存在: $img" >&2
    exit 1
  fi
done

# 校验正文（-c 或 -f 二选一）
if [[ -n "$CONTENT" && -n "$CONTENT_FILE" ]]; then
  echo "错误: -c 和 -f 不能同时指定" >&2
  exit 1
fi

if [[ -z "$CONTENT" && -z "$CONTENT_FILE" ]]; then
  echo "错误: 必须指定 -c <content> 或 -f <content_file>" >&2
  exit 1
fi

if [[ -n "$CONTENT_FILE" ]]; then
  if [[ ! -f "$CONTENT_FILE" ]]; then
    echo "错误: 正文文件不存在: $CONTENT_FILE" >&2
    exit 1
  fi
  CONTENT=$(cat "$CONTENT_FILE")
fi

if [[ -z "$CONTENT" ]]; then
  echo "错误: 正文内容不能为空" >&2
  exit 1
fi

log "参数校验通过: 标题='${TITLE}', 图片=${#IMAGES[@]}张, 正文=${#CONTENT}字符"

# ---------- 主流程 ----------

# 步骤 1: 导航到创作中心
log "步骤 1/6: 导航到小红书创作中心..."
$AB open "https://creator.xiaohongshu.com/publish/publish?source=official" >/dev/null 2>&1
sleep 3

log "等待页面加载..."
$AB wait --load networkidle >/dev/null 2>&1 || true
sleep 2

# 步骤 2: 切换到"上传图文" tab
log "步骤 2/6: 切换到'上传图文' tab..."
$AB eval "
  (() => {
    const tabs = document.querySelectorAll('div.creator-tab');
    for (const tab of tabs) {
      if (tab.textContent.trim().includes('上传图文')) {
        tab.click();
        return 'clicked';
      }
    }
    return 'not_found';
  })()
" >/dev/null 2>&1
sleep 2

# 步骤 3: 上传图片
log "步骤 3/6: 上传图片 (${#IMAGES[@]} 张)..."

# 构建 upload 命令参数
UPLOAD_FILES=""
for img in "${IMAGES[@]}"; do
  # 获取绝对路径
  ABS_PATH=$(cd "$(dirname "$img")" && pwd)/$(basename "$img")
  UPLOAD_FILES="$UPLOAD_FILES \"$ABS_PATH\""
done

eval "$AB upload \"input.upload-input\" $UPLOAD_FILES" >/dev/null 2>&1

# 轮询等待上传完成
EXPECTED_COUNT=${#IMAGES[@]}
log "等待图片上传完成 (共 ${EXPECTED_COUNT} 张)..."

for ((attempt = 1; attempt <= 30; attempt++)); do
  UPLOADED_COUNT=$($AB eval "
    document.querySelectorAll('.image-item').length
  " 2>/dev/null || echo "0")

  # 清理返回值中的引号和空白
  UPLOADED_COUNT=$(echo "$UPLOADED_COUNT" | tr -d '"' | xargs)

  if [[ "$UPLOADED_COUNT" -ge "$EXPECTED_COUNT" ]] 2>/dev/null; then
    log "图片上传完成: ${UPLOADED_COUNT}/${EXPECTED_COUNT}"
    break
  fi

  if [[ "$attempt" -eq 30 ]]; then
    log "警告: 上传等待超时，当前已上传 ${UPLOADED_COUNT}/${EXPECTED_COUNT}"
  fi

  sleep 2
done

sleep 1

# 步骤 4: 填写标题
log "步骤 4/6: 填写标题..."

# 将标题写入临时文件，通过 Python json.dumps 转为 JS 安全字符串
echo "$TITLE" > "$TMPDIR_PUB/title.txt"
TITLE_JS=$(python3 -c "
import json
with open('$TMPDIR_PUB/title.txt', 'r') as f:
    title = f.read().strip()
print(json.dumps(title))
")

$AB eval "
  (() => {
    const input = document.querySelector('input.d-text');
    if (!input) return 'title_input_not_found';
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype, 'value'
    ).set;
    nativeSetter.call(input, $TITLE_JS);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return 'title_set';
  })()
" >/dev/null 2>&1

sleep 1

# 步骤 5: 填写正文
log "步骤 5/6: 填写正文..."

# 将正文写入临时文件，通过 Python 转为 HTML 段落
echo "$CONTENT" > "$TMPDIR_PUB/content.txt"

CONTENT_HTML=$(python3 -c "
import json

with open('$TMPDIR_PUB/content.txt', 'r') as f:
    content = f.read().strip()

lines = content.split('\n')
paragraphs = []
for line in lines:
    line = line.strip()
    if line:
        # 转义 HTML 特殊字符
        line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        paragraphs.append(f'<p>{line}</p>')
    else:
        paragraphs.append('<p><br></p>')

html_content = ''.join(paragraphs)
print(json.dumps(html_content))
")

$AB eval "
  (() => {
    const editor = document.querySelector('div.tiptap.ProseMirror');
    if (!editor) return 'editor_not_found';
    editor.innerHTML = $CONTENT_HTML;
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    return 'content_set';
  })()
" >/dev/null 2>&1

sleep 1

# 步骤 6: 点击发布
log "步骤 6/6: 点击发布按钮..."

# 获取发布前的 URL
BEFORE_URL=$($AB eval "window.location.href" 2>/dev/null || echo "")

$AB eval "
  (() => {
    // 查找发布按钮（多种选择器 fallback）
    const btn = document.querySelector('button.publishBtn')
      || document.querySelector('button.css-k4lz0r')
      || document.querySelector('.btn-publish button')
      || (() => {
        const buttons = document.querySelectorAll('button');
        for (const b of buttons) {
          if (b.textContent.trim() === '发布' || b.textContent.trim().includes('发布')) {
            return b;
          }
        }
        return null;
      })();
    if (!btn) return 'publish_btn_not_found';
    btn.click();
    return 'publish_clicked';
  })()
" >/dev/null 2>&1

# 等待发布完成（检测 URL 变化或成功提示）
log "等待发布结果..."
PUBLISH_SUCCESS=false

for ((attempt = 1; attempt <= 20; attempt++)); do
  sleep 2

  CURRENT_URL=$($AB eval "window.location.href" 2>/dev/null || echo "")

  # URL 变化说明页面跳转（发布成功通常会跳转）
  if [[ "$CURRENT_URL" != "$BEFORE_URL" && -n "$CURRENT_URL" ]]; then
    PUBLISH_SUCCESS=true
    log "检测到页面跳转，发布成功！"
    log "跳转到: $CURRENT_URL"
    break
  fi

  # 检测页面上是否出现成功提示
  SUCCESS_CHECK=$($AB eval "
    (() => {
      const body = document.body.innerText || '';
      if (body.includes('发布成功') || body.includes('已发布')) return 'success';
      if (body.includes('发布失败') || body.includes('发布错误')) return 'failed';
      return 'waiting';
    })()
  " 2>/dev/null || echo "waiting")

  SUCCESS_CHECK=$(echo "$SUCCESS_CHECK" | tr -d '"' | xargs)

  if [[ "$SUCCESS_CHECK" == "success" ]]; then
    PUBLISH_SUCCESS=true
    log "检测到发布成功提示！"
    break
  elif [[ "$SUCCESS_CHECK" == "failed" ]]; then
    log "错误: 检测到发布失败提示"
    exit 1
  fi

  if [[ "$attempt" -eq 20 ]]; then
    log "警告: 等待发布结果超时（40s），请手动确认发布状态"
  fi
done

if $PUBLISH_SUCCESS; then
  log "✅ 小红书笔记发布成功！"
  log "  标题: $TITLE"
  log "  图片: ${#IMAGES[@]} 张"
  log "  正文: ${#CONTENT} 字符"
else
  log "⚠️ 发布状态未确认，请手动检查"
  exit 1
fi
