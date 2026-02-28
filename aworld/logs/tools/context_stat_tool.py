#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Context Statistics Tool (Digest Logger) 📊

Demonstrates and supports user analysis of digest logger files; parses AWorld digest logs.

Usage:
    python context_stat_tool.py
    python context_stat_tool.py /path/to/digest_logger.log
    python context_stat_tool.py --log-file /path/to/digest_logger.log --list
    python context_stat_tool.py /path/to/digest.log --session-id SESSION_ID --tree --trend --compare --output-dir ./out
    python context_stat_tool.py /path/to/digest.log --list

Environment:
    AWORLD_DIGEST_LOG: default digest log path when --log-file is not set.
"""

import argparse
import os
import sys

# Allow running from repo root or from aworld/logs/tools
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from context_stat import ContextAnalyzer


def stat_log(
    log_file: str,
    list_only: bool = False,
    output_dir: str = ".",
) -> None:
    """
    Run digest log analysis: parse log, then list sessions or analyze first session (tree + trend + compare).

    Args:
        log_file: Path to digest log file.
        list_only: If True, only list sessions; else run full analysis on first session.
        output_dir: Directory to save charts (default: current dir).

    Example:
        stat_log("/path/to/digest_logger.log")
        stat_log("/path/to/digest_logger.log", list_only=True)
    """
    if not log_file or not os.path.isfile(log_file):
        print(f"Log file missing: {log_file}")
        return
    analyzer = ContextAnalyzer(log_file)
    analyzer.parse_log_file()
    if list_only:
        analyzer.list_sessions()
        return
    if not analyzer.sessions:
        print("No session data found.")
        return
    session_id = list(analyzer.sessions.keys())[0]
    if output_dir != "." and not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    analyzer.print_tree_structure(session_id)
    analyzer.plot_context_trends(session_id, os.path.join(output_dir, f"context_trend_{session_id}.png"))
    analyzer.plot_agent_comparison(session_id, os.path.join(output_dir, f"context_comparison_{session_id}.png"))
    print("Done, charts saved.")


def _default_log_file() -> str:
    """Default digest log path: env AWORLD_DIGEST_LOG or fallback path."""
    default = os.getenv("AWORLD_DIGEST_LOG", "")
    if default and os.path.isfile(default):
        return default
    # Fallback for demo (relative to AWorld repo root if exists)
    fallback = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_TOOLS_DIR))),
        "examples", "skill_agent", "logs", "digest_logger.log",
    )
    return fallback if os.path.isfile(fallback) else default or ""


def main() -> None:
    """Run digest logger analysis: list sessions and/or analyze a session with tree/trend/compare."""
    parser = argparse.ArgumentParser(
        description="Analyze AWorld digest logs and show context stats & charts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "log_file_pos",
        nargs="?",
        default=None,
        help="Path to digest log (optional; else use --log-file or AWORLD_DIGEST_LOG)",
    )
    parser.add_argument(
        "--log-file",
        dest="log_file_opt",
        metavar="PATH",
        default=None,
        help="Path to digest log file",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all sessions only, no analysis",
    )
    parser.add_argument(
        "--session-id",
        metavar="ID",
        default=None,
        help="Session ID to analyze (default: first)",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=".",
        help="Directory to save charts (default: .)",
    )
    parser.add_argument(
        "--tree",
        action="store_true",
        help="Print session tree structure",
    )
    parser.add_argument(
        "--trend",
        action="store_true",
        help="Generate context trend chart",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Generate Agent comparison chart",
    )
    parser.add_argument(
        "--model-compare",
        action="store_true",
        help="Generate Model comparison chart",
    )
    args = parser.parse_args()

    log_file = args.log_file_pos or args.log_file_opt or _default_log_file()
    if not log_file or not os.path.isfile(log_file):
        print(f"Log file missing or not specified: {log_file}")
        print("Set a valid path or AWORLD_DIGEST_LOG env var.")
        parser.print_help()
        sys.exit(1)

    print("Digest Logger context stats")
    print("=" * 50)
    print(f"Log file: {log_file}")

    analyzer = ContextAnalyzer(log_file)
    analyzer.parse_log_file()

    if args.list:
        print("\nSessions:")
        analyzer.list_sessions()
        return

    if not analyzer.sessions:
        print("No session data found.")
        sys.exit(1)

    session_id = args.session_id
    if not session_id:
        session_id = list(analyzer.sessions.keys())[0]
        print(f"Using first session (no --session-id): {session_id}")

    if session_id not in analyzer.sessions:
        print(f"Session not found: {session_id}")
        analyzer.list_sessions()
        sys.exit(1)

    do_tree = args.tree
    do_trend = args.trend
    do_compare = args.compare
    do_model_compare = args.model_compare
    # If no analysis action specified, run demo: tree + trend + compare
    if not any([do_tree, do_trend, do_compare, do_model_compare]):
        do_tree = do_trend = do_compare = True

    out_dir = os.path.abspath(args.output_dir)
    if out_dir != "." and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if do_tree:
        print(f"\nSession: {session_id}")
        print("Tree structure:")
        analyzer.print_tree_structure(session_id)

    saved = []
    if do_trend:
        trend_path = os.path.join(out_dir, f"context_trend_{session_id}.png")
        analyzer.plot_context_trends(session_id, trend_path)
        saved.append(("Trend chart", trend_path))
    if do_compare:
        compare_path = os.path.join(out_dir, f"context_comparison_{session_id}.png")
        analyzer.plot_agent_comparison(session_id, compare_path)
        saved.append(("Agent comparison", compare_path))
    if do_model_compare:
        model_path = os.path.join(out_dir, f"context_model_comparison_{session_id}.png")
        analyzer.plot_model_comparison(session_id, model_path)
        saved.append(("Model comparison", model_path))

    if saved:
        print("\nDone, charts saved:")
        for name, path in saved:
            print(f"   {name}: {path}")


if __name__ == "__main__":
    main()
