#!/usr/bin/env python3
"""Collect X discussions from high-signal accounts."""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Target date
TARGET_DATE = "2026-04-21"

# High-signal accounts to sample (prioritized list)
ACCOUNTS = [
    # Model companies / official
    "@OpenAI",
    "@AnthropicAI", 
    "@GoogleDeepMind",
    "@huggingface",
    # Core people / researchers
    "@sama",
    "@karpathy",
    # AI coding / agent-tool ecosystem
    "@llama_index",
    "@LangChainAI",
    "@SimonWillison",
    "@jerryjliu0",
    "@hwchase17",
    "@ClementDelangue",
    "@LoganMarkewich",
]

# Keywords to filter
KEYWORDS = [
    "ChatGPT", "Claude", "Gemini", "OpenAI", "Anthropic", 
    "MCP", "AI agent", "AI coding", "GPT-4", "GPT-5",
    "o1", "o3", "Sonnet", "Opus", "Haiku"
]

def run_agent_browser(cmd_args, timeout=60):
    """Run agent-browser command."""
    cmd = ["/opt/homebrew/bin/agent-browser", "--cdp", "9222"] + cmd_args
    try:
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=timeout,
            check=False
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except Exception as e:
        return "", str(e), -1

def navigate_to_profile(username):
    """Navigate to a user's profile page."""
    url = f"https://x.com/{username}"
    print(f"Navigating to {url}...")
    stdout, stderr, code = run_agent_browser(["navigate", url], timeout=30)
    if code != 0:
        print(f"  Warning: Navigation failed - {stderr}")
        return False
    time.sleep(3)  # Wait for page load
    return True

def extract_page_content():
    """Extract content from current page."""
    print("  Extracting page content...")
    stdout, stderr, code = run_agent_browser(["extract", "--format", "markdown"], timeout=30)
    if code != 0:
        print(f"  Warning: Extraction failed - {stderr}")
        return None
    return stdout

def parse_tweets_from_markdown(markdown_content, username):
    """Parse tweets from markdown content."""
    tweets = []
    lines = markdown_content.split('\n')
    
    current_tweet = {}
    in_tweet = False
    
    for line in lines:
        line = line.strip()
        
        # Look for tweet indicators
        if any(keyword.lower() in line.lower() for keyword in KEYWORDS):
            if current_tweet and 'text' in current_tweet:
                tweets.append(current_tweet)
            current_tweet = {
                'author': username,
                'text': line,
                'url': f"https://x.com/{username}",
                'collected_at': datetime.now().isoformat()
            }
            in_tweet = True
        elif in_tweet and line:
            # Continue collecting tweet text
            if 'text' in current_tweet:
                current_tweet['text'] += ' ' + line
    
    if current_tweet and 'text' in current_tweet:
        tweets.append(current_tweet)
    
    return tweets

def collect_from_account(username):
    """Collect tweets from a single account."""
    print(f"\n{'='*60}")
    print(f"Collecting from {username}")
    print('='*60)
    
    if not navigate_to_profile(username):
        return []
    
    content = extract_page_content()
    if not content:
        return []
    
    tweets = parse_tweets_from_markdown(content, username)
    print(f"  Found {len(tweets)} relevant tweets")
    
    return tweets

def main():
    """Main collection function."""
    print(f"Starting X discussion collection for {TARGET_DATE}")
    print(f"Sampling {len(ACCOUNTS)} high-signal accounts")
    print(f"Keywords: {', '.join(KEYWORDS)}")
    
    all_tweets = []
    
    for account in ACCOUNTS[:10]:  # Limit to first 10 accounts
        try:
            tweets = collect_from_account(account)
            all_tweets.extend(tweets)
            time.sleep(2)  # Rate limiting
        except Exception as e:
            print(f"Error collecting from {account}: {e}")
            continue
    
    # Save results
    output_path = Path("/Users/raku/workspace/AWorld/survey/x_discussion_20260421.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    output_data = {
        "collection_date": TARGET_DATE,
        "collected_at": datetime.now().isoformat(),
        "accounts_sampled": ACCOUNTS[:10],
        "keywords": KEYWORDS,
        "total_tweets": len(all_tweets),
        "tweets": all_tweets
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*60}")
    print(f"Collection complete!")
    print(f"Total tweets collected: {len(all_tweets)}")
    print(f"Output saved to: {output_path}")
    print('='*60)
    
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCollection interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
