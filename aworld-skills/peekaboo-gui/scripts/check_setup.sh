#!/bin/bash
# check_setup.sh - 验证 Peekaboo 安装和权限

set -e

echo "🔍 Peekaboo 设置检查器"
echo "=========================="
echo ""

# 检查 peekaboo 是否已安装
echo "1. 检查 Peekaboo 安装..."
if command -v peekaboo &> /dev/null; then
    VERSION=$(peekaboo --version 2>&1 || echo "未知")
    echo "   ✅ 已找到 Peekaboo：$VERSION"
    echo "   📍 位置：$(which peekaboo)"
else
    echo "   ❌ 在 PATH 中未找到 Peekaboo"
    echo "   💡 安装方式：brew install steipete/tap/peekaboo"
    exit 1
fi

echo ""

# 检查权限
echo "2. 检查权限..."
PERMS=$(peekaboo permissions status 2>&1 || echo "failed")

if echo "$PERMS" | grep -q "failed"; then
    echo "   ❌ 无法检查权限（可能需要使用正确的 shell 运行）"
    exit 1
fi

if echo "$PERMS" | grep -q "Accessibility.*Granted"; then
    echo "   ✅ 辅助功能：已授予"
else
    echo "   ❌ 辅助功能：未授予"
    echo "   💡 运行：peekaboo permissions grant"
fi

if echo "$PERMS" | grep -q "Screen Recording.*Granted"; then
    echo "   ✅ 屏幕录制：已授予"
else
    echo "   ❌ 屏幕录制：未授予"
    echo "   💡 运行：peekaboo permissions grant"
fi

echo ""

# 测试基本功能
echo "3. 测试基本功能..."
if peekaboo list apps --json &> /dev/null; then
    APP_COUNT=$(peekaboo list apps --json | jq '. | length' 2>/dev/null || echo "?")
    echo "   ✅ 可以列出应用程序（$APP_COUNT 个正在运行）"
else
    echo "   ❌ 无法列出应用程序"
fi

if peekaboo list windows --json &> /dev/null; then
    WINDOW_COUNT=$(peekaboo list windows --json | jq '. | length' 2>/dev/null || echo "?")
    echo "   ✅ 可以列出窗口（$WINDOW_COUNT 个打开）"
else
    echo "   ❌ 无法列出窗口"
fi

echo ""

# 检查 jq 是否可用（用于解析）
echo "4. 检查可选依赖..."
if command -v jq &> /dev/null; then
    echo "   ✅ 已找到 jq（用于 JSON 解析）"
else
    echo "   ⚠️  未找到 jq（安装：brew install jq）"
fi

echo ""
echo "=========================="
echo "设置检查完成！"
echo ""

# 总结
if echo "$PERMS" | grep -q "Accessibility.*Granted" && echo "$PERMS" | grep -q "Screen Recording.*Granted"; then
    echo "✨ 所有检查通过！Peekaboo 已准备就绪。"
    echo ""
    echo "试试这些命令："
    echo "  peekaboo see"
    echo "  peekaboo list apps"
    echo '  peekaboo "打开 Safari"'
    exit 0
else
    echo "⚠️  缺少一些权限。运行此命令授予它们："
    echo "  peekaboo permissions grant"
    echo ""
    echo "然后重启终端并再次运行此脚本。"
    exit 1
fi
