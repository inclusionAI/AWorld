#!/usr/bin/env python3
"""
Collect X (Twitter) AI discussions from high-signal accounts.
Date: 2026-04-21

This script collects tweets from curated high-signal AI accounts,
filters by date and keywords, and saves structured data.
"""

import json
import subprocess
import sys
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

# Configuration
TARGET_DATE = "2026-04-21"
CDP_PORT = 9222
AGENT_BROWSER = "/opt/homebrew/bin/agent-browser"
COOKIE_FILE = Path("/tmp/last_7_days_news_x_cookie.txt")

# High-signal accounts (prioritized)
HIGH_SIGNAL_ACCOUNTS = [
    # Model companies / official
    {"handle": "OpenAI", "category": "official", "priority": 1},
    {"handle": "AnthropicAI", "category": "official", "priority": 1},
    {"handle": "GoogleDeepMind", "category": "official", "priority": 1},
    {"handle": "huggingface", "category": "official", "priority": 1},
    
    # Core people / researchers
    {"handle": "sama", "category": "researcher", "priority": 2},
    {"handle": "karpathy", "category": "researcher", "priority": 2},
    {"handle": "geoffreyhinton", "category": "researcher", "priority": 2},
    
    # AI coding / agent-tool ecosystem
    {"handle": "llama_index", "category": "ecosystem", "priority": 3},
    {"handle": "LangChainAI", "category": "ecosystem", "priority": 3},
    {"handle": "SimonWillison", "category": "ecosystem", "priority": 3},
    {"handle": "jerryjliu0", "category": "ecosystem", "priority": 3},
    {"handle": "hwchase17", "category": "ecosystem", "priority": 3},
    {"handle": "ClementDelangue", "category": "ecosystem", "priority": 3},
]

# Keywords for filtering
AI_KEYWORDS = [
    # Models
    "ChatGPT", "GPT-4", "GPT-5", "o1", "o3",
    "Claude", "Sonnet", "Opus", "Haiku",
    "Gemini", "Gemma",
    
    # Companies
    "OpenAI", "Anthropic", "Google DeepMind",
    
    # Technologies
    "MCP", "Model Context Protocol",
    "AI agent", "AI agents",
    "AI coding", "code generation",
    "LLM", "large language model",
    "transformer", "attention",
    
    # Tools & Frameworks
    "LangChain", "LlamaIndex",
    "AutoGPT", "BabyAGI",
]

def log(message: str, level: str = "INFO"):
    """Log message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {level}: {message}")

def run_agent_browser(args: List[str], timeout: int = 30) -> tuple[str, str, int]:
    """Execute agent-browser command."""
    cmd = [AGENT_BROWSER, "--cdp", str(CDP_PORT)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except Exception as e:
        return "", str(e), -1

def check_cookie_validity() -> bool:
    """Check if X cookie file exists and is valid."""
    if not COOKIE_FILE.exists():
        log("Cookie file not found", "ERROR")
        return False
    
    content = COOKIE_FILE.read_text().strip()
    if not content:
        log("Cookie file is empty", "ERROR")
        return False
    
    # Check for required cookies
    required = ["auth_token", "ct0"]
    for req in required:
        if req not in content:
            log(f"Missing required cookie: {req}", "ERROR")
            return False
    
    log("Cookie file is valid", "SUCCESS")
    return True

def navigate_to_profile(handle: str) -> bool:
    """Navigate to user profile."""
    url = f"https://x.com/{handle}"
    log(f"Navigating to {url}")
    
    stdout, stderr, code = run_agent_browser(["open", url], timeout=30)
    if code != 0:
        log(f"Navigation failed: {stderr}", "ERROR")
        return False
    
    # Wait for page load
    time.sleep(3)
    
    # Check if page loaded successfully
    stdout, stderr, code = run_agent_browser(["get", "title"])
    if code == 0 and stdout:
        log(f"Page loaded: {stdout}")
        return True
    
    return False

def extract_page_snapshot() -> Optional[str]:
    """Extract page content using snapshot."""
    log("Extracting page snapshot...")
    stdout, stderr, code = run_agent_browser(["snapshot", "-i"], timeout=30)
    
    if code != 0:
        log(f"Snapshot extraction failed: {stderr}", "ERROR")
        return None
    
    return stdout

def parse_tweets_from_snapshot(snapshot: str, handle: str) -> List[Dict]:
    """Parse tweets from accessibility snapshot."""
    tweets = []
    
    # This is a simplified parser - actual implementation would need
    # to parse the accessibility tree structure
    lines = snapshot.split('\n')
    
    current_tweet = None
    for line in lines:
        # Look for tweet text containing keywords
        for keyword in AI_KEYWORDS:
            if keyword.lower() in line.lower():
                if current_tweet:
                    tweets.append(current_tweet)
                
                current_tweet = {
                    "author": f"@{handle}",
                    "text": line.strip(),
                    "url": f"https://x.com/{handle}",
                    "timestamp": TARGET_DATE,
                    "collected_at": datetime.now().isoformat(),
                    "keywords_matched": [keyword]
                }
                break
    
    if current_tweet:
        tweets.append(current_tweet)
    
    return tweets

def collect_from_account(account: Dict) -> List[Dict]:
    """Collect tweets from a single account."""
    handle = account["handle"]
    category = account["category"]
    
    log(f"{'='*60}")
    log(f"Collecting from @{handle} ({category})")
    log(f"{'='*60}")
    
    if not navigate_to_profile(handle):
        log(f"Skipping @{handle} due to navigation failure", "WARNING")
        return []
    
    snapshot = extract_page_snapshot()
    if not snapshot:
        log(f"Skipping @{handle} due to extraction failure", "WARNING")
        return []
    
    tweets = parse_tweets_from_snapshot(snapshot, handle)
    log(f"Found {len(tweets)} relevant tweets from @{handle}", "SUCCESS")
    
    return tweets

def save_results(tweets: List[Dict], output_path: Path):
    """Save collected tweets to JSON file."""
    output_data = {
        "metadata": {
            "collection_date": TARGET_DATE,
            "collected_at": datetime.now().isoformat(),
            "total_accounts_sampled": len(HIGH_SIGNAL_ACCOUNTS),
            "total_tweets_collected": len(tweets),
            "keywords": AI_KEYWORDS,
            "accounts": HIGH_SIGNAL_ACCOUNTS
        },
        "tweets": tweets
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    log(f"Results saved to {output_path}", "SUCCESS")

def main():
    """Main execution function."""
    log("="*60)
    log("X AI Discussion Collection")
    log(f"Target Date: {TARGET_DATE}")
    log("="*60)
    
    # Check prerequisites
    if not Path(AGENT_BROWSER).exists():
        log(f"agent-browser not found at {AGENT_BROWSER}", "ERROR")
        log("Please install agent-browser or update the path", "ERROR")
        return 1
    
    if not check_cookie_validity():
        log("Cookie validation failed", "ERROR")
        log("Please run the following commands to login:", "INFO")
        log("  1. bash /Users/raku/workspace/AWorld/aworld-skills/last_7_days_news/scripts/ensure_x_cookies.sh", "INFO")
        log("  2. Complete login in the browser window", "INFO")
        log("  3. Run: python3 /Users/raku/workspace/AWorld/aworld-skills/last_7_days_news/scripts/export_x_cookies.py --port 9222", "INFO")
        log("  4. Re-run this script", "INFO")
        return 1
    
    # Collect tweets
    all_tweets = []
    accounts_to_sample = HIGH_SIGNAL_ACCOUNTS[:12]  # Sample first 12 accounts
    
    for i, account in enumerate(accounts_to_sample, 1):
        log(f"Progress: {i}/{len(accounts_to_sample)}")
        try:
            tweets = collect_from_account(account)
            all_tweets.extend(tweets)
            
            # Rate limiting
            if i < len(accounts_to_sample):
                time.sleep(2)
        except Exception as e:
            log(f"Error collecting from @{account['handle']}: {e}", "ERROR")
            continue
    
    # Save results
    output_path = Path("/Users/raku/workspace/AWorld/survey/x_discussion_20260421.json")
    save_results(all_tweets, output_path)
    
    # Summary
    log("="*60)
    log("Collection Summary", "SUCCESS")
    log(f"Accounts sampled: {len(accounts_to_sample)}")
    log(f"Total tweets collected: {len(all_tweets)}")
    log(f"Output file: {output_path}")
    log("="*60)
    
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Collection interrupted by user", "WARNING")
        sys.exit(1)
    except Exception as e:
        log(f"Fatal error: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        sys.exit(1)
