#!/usr/bin/env python3
"""Quick viewer for X discussion collection data."""

import json
import sys
from pathlib import Path
from datetime import datetime

def load_data(file_path):
    """Load JSON data from file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def print_summary(data):
    """Print collection summary."""
    meta = data['metadata']
    tweets = data['tweets']
    
    print("=" * 70)
    print("📊 X平台AI讨论收集 - 数据概览")
    print("=" * 70)
    print(f"\n📅 收集日期: {meta['collection_date']}")
    print(f"⏰ 收集时间: {meta['collected_at']}")
    print(f"👥 采样账号: {meta['total_accounts_sampled']}个")
    print(f"💬 收集推文: {meta['total_tweets_collected']}条")
    print(f"🏷️  关键词数: {len(meta['keywords'])}个")
    print(f"📌 数据状态: {meta.get('collection_status', 'REAL_DATA')}")
    
    # Category breakdown
    categories = {}
    for tweet in tweets:
        cat = tweet.get('category', 'unknown')
        categories[cat] = categories.get(cat, 0) + 1
    
    print(f"\n📂 分类统计:")
    for cat, count in sorted(categories.items()):
        print(f"   - {cat}: {count}条")
    
    # Top keywords
    keyword_counts = {}
    for tweet in tweets:
        for kw in tweet.get('keywords_matched', []):
            keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
    
    print(f"\n🔥 热门关键词 (Top 10):")
    for kw, count in sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"   - {kw}: {count}次")
    
    # Engagement stats
    total_views = sum(t.get('engagement', {}).get('views', 0) for t in tweets)
    total_likes = sum(t.get('engagement', {}).get('likes', 0) for t in tweets)
    total_retweets = sum(t.get('engagement', {}).get('retweets', 0) for t in tweets)
    
    print(f"\n📈 互动统计:")
    print(f"   - 总浏览量: {total_views:,}")
    print(f"   - 总点赞数: {total_likes:,}")
    print(f"   - 总转发数: {total_retweets:,}")
    print(f"   - 平均浏览: {total_views//len(tweets):,}/条")

def print_tweets(data, limit=5):
    """Print sample tweets."""
    tweets = data['tweets']
    
    print(f"\n{'=' * 70}")
    print(f"💬 推文示例 (前{min(limit, len(tweets))}条)")
    print("=" * 70)
    
    for i, tweet in enumerate(tweets[:limit], 1):
        print(f"\n[{i}] {tweet['author']} - {tweet.get('category', 'unknown')}")
        print(f"⏰ {tweet['timestamp']}")
        print(f"📝 {tweet['text'][:150]}{'...' if len(tweet['text']) > 150 else ''}")
        
        eng = tweet.get('engagement', {})
        print(f"📊 👁️ {eng.get('views', 0):,} | ❤️ {eng.get('likes', 0):,} | 🔄 {eng.get('retweets', 0):,}")
        print(f"🏷️  {', '.join(tweet.get('keywords_matched', []))}")
        print(f"🔗 {tweet['url']}")

def main():
    """Main function."""
    file_path = Path("/Users/raku/workspace/AWorld/survey/x_discussion_20260421.json")
    
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        return 1
    
    try:
        data = load_data(file_path)
        print_summary(data)
        print_tweets(data, limit=5)
        
        print(f"\n{'=' * 70}")
        print("✅ 数据查看完成")
        print(f"📁 文件位置: {file_path}")
        print("=" * 70)
        
        return 0
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
