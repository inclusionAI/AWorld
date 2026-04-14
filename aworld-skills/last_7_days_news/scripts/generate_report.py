#!/usr/bin/env python3
"""HTML report generator for the latest 7 days of news and X discussions."""

import json
import sys
from datetime import datetime
from pathlib import Path


def normalize_items(report_data):
    """Normalize legacy tweet payloads into the current items structure."""
    if report_data.get("items"):
        return report_data["items"]

    items = []
    for tweet in report_data.get("tweets", []):
        likes = int(tweet.get("likes", 0) or 0)
        retweets = int(tweet.get("retweets", 0) or 0)
        replies = int(tweet.get("replies", 0) or 0)
        items.append(
            {
                "title": tweet.get("displayName") or tweet.get("username") or "X discussion",
                "text": tweet.get("text", ""),
                "source": tweet.get("source", "X"),
                "url": tweet.get("url", ""),
                "author": tweet.get("displayName") or tweet.get("username") or "",
                "published_at": tweet.get("published_at") or tweet.get("time") or "",
                "timeStr": tweet.get("timeStr") or tweet.get("time") or "",
                "score": tweet.get("score", likes + retweets + replies),
                "views": int(tweet.get("views", 0) or 0),
                "likes": likes,
                "comments": replies,
                "shares": retweets,
            }
        )
    return items


def generate_html_report(report_data, output_path=None):
    """Generate an HTML report."""

    # Locate the current script directory and skill root.
    script_dir = Path(__file__).parent
    skill_dir = script_dir.parent
    template_path = script_dir / "template.html"

    # Read the HTML template.
    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()

    items = normalize_items(report_data)
    config = report_data.get("config", {})

    # Prepare replacement data.
    generation_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    title = report_data.get('title', 'Last 7 Days News and Discussion Brief')
    total_count = report_data.get('collected', len(items))
    keywords = config.get('keywords', [])
    keyword_count = len(keywords) if isinstance(keywords, list) else int(keywords or 0)
    days_ago = int(config.get('daysAgo', 7) or 7)
    sources = config.get('sources', [])
    source_count = len(sources) if isinstance(sources, list) else int(sources or 0)
    if not source_count:
        source_count = len({item.get("source", "Unknown") for item in items})

    normalized_report = dict(report_data)
    normalized_report["items"] = items

    report_json = json.dumps(normalized_report, ensure_ascii=False, indent=None)

    # Replace placeholders in the template.
    html = template.replace('{{REPORT_JSON}}', report_json)
    html = html.replace('{{REPORT_TITLE}}', title)
    html = html.replace('{{GENERATION_TIME}}', generation_time)
    html = html.replace('{{TOTAL_COUNT}}', str(total_count))
    html = html.replace('{{KEYWORD_COUNT}}', str(keyword_count))
    html = html.replace('{{TIME_RANGE}}', f'Last {days_ago} days')
    html = html.replace('{{SOURCE_COUNT}}', str(source_count))

    # Resolve the output path.
    if output_path is None:
        reports_dir = skill_dir / "reports"
        reports_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
        output_path = reports_dir / f"last-7-days-news-{timestamp}.html"
    else:
        output_path = Path(output_path)

    # Write the HTML file.
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    return str(output_path)


def main():
    """Command-line entry point."""
    if len(sys.argv) < 2:
        print("Usage: python generate_report.py <report_data.json> [output.html]")
        print("Example: python generate_report.py report_data.json")
        sys.exit(1)

    # Read the report data.
    input_file = sys.argv[1]
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: file '{input_file}' does not exist")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: failed to parse JSON - {e}")
        sys.exit(1)

    # Generate the HTML report.
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        output_path = generate_html_report(report_data, output_file)
        print(f"HTML report generated successfully: {output_path}")
        print(f"Total items: {len(normalize_items(report_data))}")
    except Exception as e:
        print(f"Error: failed to generate report - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
