#!/bin/bash

# XBench Dataset Download and Decrypt Script
# This script downloads the official xbench-evals dataset and decrypts it for local use

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_DIR="$SCRIPT_DIR/temp_xbench"
REPO_URL="https://github.com/xbench-ai/xbench-evals.git"

echo "🚀 Starting XBench dataset download and decryption..."
echo ""

# Step 1: Clone the repository
echo "📦 Step 1/4: Cloning xbench-evals repository..."
if [ -d "$TEMP_DIR" ]; then
    echo "   ⚠️  Temporary directory already exists, removing..."
    rm -rf "$TEMP_DIR"
fi

git clone "$REPO_URL" "$TEMP_DIR"
cd "$TEMP_DIR"

echo "   ✅ Repository cloned successfully"
echo ""

# Step 2: Install dependencies
echo "📦 Step 2/4: Installing dependencies..."
pip install -q pandas
echo "   ✅ Dependencies installed"
echo ""

# Step 3: Decrypt dataset
echo "🔓 Step 3/4: Decrypting DeepSearch dataset..."
echo "   This may take a moment..."

# Use the standalone decryption script (Python 3.8+ compatible)
python "$SCRIPT_DIR/decrypt_xbench.py" data/DeepSearch.csv data/DeepSearch_decrypted.csv

echo "   ✅ Dataset decrypted"
echo ""

# Step 4: Copy to benchmark directory
echo "📋 Step 4/4: Copying decrypted dataset..."
if [ -f "data/DeepSearch_decrypted.csv" ]; then
    cp data/DeepSearch_decrypted.csv "$SCRIPT_DIR/"
    echo "   ✅ DeepSearch_decrypted.csv copied"
fi

# Also copy original file if decryption created it in place
if [ -f "data/DeepSearch.csv" ]; then
    cp data/DeepSearch.csv "$SCRIPT_DIR/DeepSearch_original.csv"
    echo "   ✅ DeepSearch.csv (original) copied"
fi

echo ""

# Step 5: Cleanup
echo "🧹 Cleaning up temporary files..."
cd "$SCRIPT_DIR"
rm -rf "$TEMP_DIR"
echo "   ✅ Cleanup complete"
echo ""

# Final message
echo "✅ XBench dataset download and decryption completed successfully!"
echo ""
echo "📁 Downloaded files in: $SCRIPT_DIR"
echo ""
echo "⚠️  IMPORTANT SECURITY NOTICE:"
echo "   - DO NOT upload the decrypted data online"
echo "   - DO NOT commit decrypted data to public repositories"
echo "   - Keep the decrypted datasets local only"
echo ""
echo "🌐 For more information, visit: https://xbench.org"
echo ""

