#!/usr/bin/env bash
# ============================================================
# html_to_image.sh — 将 HTML 渲染为图片（通过 agent-browser CDP 截图）
#
# 用法:
#   ./html_to_image.sh -o <output.png> [-f <html_file> | -c <html_content>] [-w <width>] [-e <height>] [-p <cdp_port>] [--full | --no-full]
#
# 参数:
#   -o  输出图片路径（必填）
#   -f  HTML 文件路径（与 -c 二选一）
#   -c  HTML 内容字符串（与 -f 二选一）
#   -w  视口宽度，默认 1080
#   -e  视口高度（不指定则自动适应内容）
#   -p  CDP 端口号，默认 9222
#   --full     全页截图（默认）
#   --no-full  仅截取视口区域
#
# 依赖:
#   - agent-browser (已通过 CDP 连接到运行中的浏览器)
#
# 示例:
#   ./html_to_image.sh -f card.html -o card.png
#   ./html_to_image.sh -c '<h1>Hello</h1>' -o hello.png -w 750
# ============================================================

set -euo pipefail

# ---------- 默认参数 ----------
CDP_PORT=9222
OUTPUT=""
HTML_FILE=""
HTML_CONTENT=""
WIDTH=1080
HEIGHT=""
FULL_PAGE=true

# ---------- 解析参数 ----------
# 先处理长参数，再用 getopts 处理短参数
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --full)    FULL_PAGE=true ;;
    --no-full) FULL_PAGE=false ;;
    *)         ARGS+=("$arg") ;;
  esac
done

# 重置位置参数
set -- "${ARGS[@]}"

while getopts "o:f:c:w:e:p:h" opt; do
  case $opt in
    o) OUTPUT="$OPTARG" ;;
    f) HTML_FILE="$OPTARG" ;;
    c) HTML_CONTENT="$OPTARG" ;;
    w) WIDTH="$OPTARG" ;;
    e) HEIGHT="$OPTARG" ;;
    p) CDP_PORT="$OPTARG" ;;
    h)
      head -22 "$0" | tail -20
      exit 0
      ;;
    *)
      echo "用法: $0 -o <output.png> [-f <html_file> | -c <html_content>] [-w <width>] [-e <height>] [-p <cdp_port>]" >&2
      exit 1
      ;;
  esac
done

# ---------- 工具函数 ----------
AB="agent-browser --cdp $CDP_PORT"
TMPDIR_HTI=$(mktemp -d)

cleanup() {
  rm -rf "$TMPDIR_HTI"
}
trap cleanup EXIT

log() {
  echo "[$(date '+%H:%M:%S')] $*" >&2
}

# ---------- 参数校验 ----------
if [[ -z "$OUTPUT" ]]; then
  echo "错误: 必须指定 -o <output.png>" >&2
  exit 1
fi

if [[ -n "$HTML_FILE" && -n "$HTML_CONTENT" ]]; then
  echo "错误: -f 和 -c 不能同时指定" >&2
  exit 1
fi

if [[ -z "$HTML_FILE" && -z "$HTML_CONTENT" ]]; then
  echo "错误: 必须指定 -f <html_file> 或 -c <html_content>" >&2
  exit 1
fi

if [[ -n "$HTML_FILE" && ! -f "$HTML_FILE" ]]; then
  echo "错误: HTML 文件不存在: $HTML_FILE" >&2
  exit 1
fi

# 确保输出目录存在
OUTPUT_DIR=$(dirname "$OUTPUT")
if [[ ! -d "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR"
fi

# ---------- 准备 HTML 文件 ----------
if [[ -n "$HTML_CONTENT" ]]; then
  # 内容模式：写入临时文件
  TARGET_HTML="$TMPDIR_HTI/page.html"
  echo "$HTML_CONTENT" > "$TARGET_HTML"
  log "HTML 内容已写入临时文件 (${#HTML_CONTENT} 字符)"
else
  # 文件模式：使用绝对路径
  TARGET_HTML=$(cd "$(dirname "$HTML_FILE")" && pwd)/$(basename "$HTML_FILE")
  log "使用 HTML 文件: $TARGET_HTML"
fi

# ---------- 主流程 ----------

# 步骤 1: 设置视口
log "设置视口宽度: ${WIDTH}px"
if [[ -n "$HEIGHT" ]]; then
  $AB set viewport "$WIDTH" "$HEIGHT" >/dev/null 2>&1
  log "设置视口高度: ${HEIGHT}px"
else
  # 先设一个较大的高度，后面全页截图会自动处理
  $AB set viewport "$WIDTH" 2000 >/dev/null 2>&1
fi

# 步骤 2: 打开 HTML 文件
log "渲染 HTML..."
$AB open "file://$TARGET_HTML" >/dev/null 2>&1
sleep 1

# 等待页面加载完成
$AB wait --load networkidle >/dev/null 2>&1 || true
sleep 1

# 步骤 3: 等待字体和图片加载
$AB eval "
  document.fonts ? document.fonts.ready.then(() => 'fonts_loaded') : 'no_font_api'
" >/dev/null 2>&1 || true
sleep 1

# 步骤 4: 截图
OUTPUT_ABS=$(cd "$OUTPUT_DIR" && pwd)/$(basename "$OUTPUT")

if $FULL_PAGE; then
  log "全页截图..."
  $AB screenshot --full "$OUTPUT_ABS" >/dev/null 2>&1
else
  log "视口截图..."
  $AB screenshot "$OUTPUT_ABS" >/dev/null 2>&1
fi

# 步骤 5: 验证输出
if [[ -f "$OUTPUT_ABS" ]]; then
  FILE_SIZE=$(wc -c < "$OUTPUT_ABS" | xargs)
  log "✅ 截图完成: $OUTPUT_ABS (${FILE_SIZE} bytes)"
else
  log "错误: 截图文件未生成"
  exit 1
fi
