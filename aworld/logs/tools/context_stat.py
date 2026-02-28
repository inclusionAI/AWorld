#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Context Statistics Analysis Tool 📊

This tool analyzes context usage from amnicontext digest logs and provides:
1. Tree structure visualization of context usage by agent and subtasks
2. Time-series charts showing context usage trends

Author: AI Assistant
Date: 2025-01-15
"""

import re
import json
import argparse
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict, OrderedDict
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
import pandas as pd
import numpy as np


@dataclass
class ContextRecord:
    """Context usage record from log"""
    timestamp: datetime
    agent_id: str
    task_id: str
    user_id: str
    session_id: str
    model_name: str
    total_context: int
    token_breakdown: Dict[str, int]


@dataclass
class AgentStats:
    """Statistics for a specific agent"""
    agent_id: str
    total_context: int = 0
    max_context: int = 0
    min_context: int = float('inf')
    avg_context: float = 0.0
    context_records: List[ContextRecord] = field(default_factory=list)
    subtask_stats: Dict[str, 'SubtaskStats'] = field(default_factory=dict)


@dataclass
class SubtaskStats:
    """Statistics for a subtask within an agent"""
    subtask_id: str
    total_context: int = 0
    max_context: int = 0
    min_context: int = float('inf')
    avg_context: float = 0.0
    context_records: List[ContextRecord] = field(default_factory=list)


@dataclass
class ModelStats:
    """Statistics for a specific model"""
    model_name: str
    total_context: int = 0
    max_context: int = 0
    min_context: int = float('inf')
    avg_context: float = 0.0
    context_records: List[ContextRecord] = field(default_factory=list)
    agent_usage: Dict[str, int] = field(default_factory=dict)  # agent_id -> usage_count


@dataclass
class SessionStats:
    """Overall session statistics"""
    session_id: str
    total_context: int = 0
    max_context: int = 0
    min_context: int = float('inf')
    avg_context: float = 0.0
    agent_stats: Dict[str, AgentStats] = field(default_factory=dict)
    model_stats: Dict[str, ModelStats] = field(default_factory=dict)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class ContextAnalyzer:
    """Context usage analyzer for amnicontext digest logs"""
    
    def __init__(self, log_file_path: str):
        """
        Initialize the analyzer with log file path
        
        Args:
            log_file_path: Path to the amnicontext digest log file
        """
        self.log_file_path = log_file_path
        self.sessions: Dict[str, SessionStats] = {}
        
    def parse_log_file(self) -> None:
        """
        Parse the log file and extract context usage data
        
        Supports two log formats:
        1. Old format: timestamp - context_length|agent_id|task_id|user_id|session_id|model_name|total_context|token_breakdown_json|optional_number
        2. New format: timestamp | digest | INFO | context_length|agent_id|task_id|user_id|session_id|model_name|total_context|token_breakdown_json|turn_number
        """
        print("Parsing log file...")
        
        # Regex pattern to match log entries
        # Support two formats:
        # 1. Old format: timestamp - context_length|agent_id|task_id|user_id|session_id|model_name|total_context|json|optional_number
        # 2. New format: timestamp | digest | INFO | context_length|agent_id|task_id|user_id|session_id|model_name|total_context|json|turn_number
        pattern_old = (
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[\.,]\d{3})\s-\scontext_length\|'
            r'([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|(\d+)\|'
            r'(\{.*\})(?:\|(\d+))?$'
        )
        pattern_new = (
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[\.,]\d{3})\s*\|\s*digest\s*\|\s*\S*\s*\|\s*context_length\|'
            r'([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|(\d+)\|'
            r'(\{.*\})(?:\|(\d+))?$'
        )
        
        with open(self.log_file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or 'context_length' not in line:
                    continue
                    
                try:
                    # Try new format first, then fall back to old format
                    match = re.match(pattern_new, line)
                    if not match:
                        match = re.match(pattern_old, line)
                    
                    if not match:
                        print(f"Warning: line {line_num} format mismatch: {line[:100]}...")
                        continue
                        
                    groups = match.groups()
                    timestamp_str = groups[0]
                    agent_id = groups[1]
                    task_id = groups[2]
                    user_id = groups[3]
                    session_id = groups[4]
                    model_name = groups[5]
                    total_context_str = groups[6]
                    token_breakdown_str = groups[7]
                    # groups[8] may be an optional trailing integer; ignore if present
                    
                    # Parse timestamp (support both comma and dot as millisecond separator)
                    try:
                        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                    except ValueError:
                        # Normalize '.' to ',' and try again
                        timestamp = datetime.strptime(timestamp_str.replace('.', ','), '%Y-%m-%d %H:%M:%S,%f')
                    
                    # Parse total context
                    total_context = int(total_context_str)
                    
                    # Parse token breakdown JSON
                    try:
                        token_breakdown = json.loads(token_breakdown_str)
                    except json.JSONDecodeError:
                        print(f"Warning: line {line_num} JSON parse failed: {token_breakdown_str}")
                        token_breakdown = {}
                    
                    # Create context record
                    record = ContextRecord(
                        timestamp=timestamp,
                        agent_id=agent_id,
                        task_id=task_id,
                        user_id=user_id,
                        session_id=session_id,
                        model_name=model_name,
                        total_context=total_context,
                        token_breakdown=token_breakdown
                    )
                    
                    # Add to session stats
                    self._add_record_to_session(record)
                    
                except Exception as e:
                    print(f"Error: line {line_num} parse failed: {e}")
                    continue
        
        # Calculate statistics
        self._calculate_statistics()
        # print(f"Parsed {len(self.sessions)} sessions.")
    
    def _add_record_to_session(self, record: ContextRecord) -> None:
        """Add a context record to the appropriate session"""
        session_id = record.session_id
        
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionStats(session_id=session_id)
        
        session = self.sessions[session_id]
        
        # Update session time range
        if session.start_time is None or record.timestamp < session.start_time:
            session.start_time = record.timestamp
        if session.end_time is None or record.timestamp > session.end_time:
            session.end_time = record.timestamp
        
        # Add to agent stats
        agent_id = record.agent_id
        if agent_id not in session.agent_stats:
            session.agent_stats[agent_id] = AgentStats(agent_id=agent_id)
        
        agent = session.agent_stats[agent_id]
        agent.context_records.append(record)
        
        # Add to subtask stats (using task_id as subtask identifier)
        subtask_id = record.task_id
        if subtask_id not in agent.subtask_stats:
            agent.subtask_stats[subtask_id] = SubtaskStats(subtask_id=subtask_id)
        
        subtask = agent.subtask_stats[subtask_id]
        subtask.context_records.append(record)
        
        # Add to model stats
        model_name = record.model_name
        if model_name not in session.model_stats:
            session.model_stats[model_name] = ModelStats(model_name=model_name)
        
        model = session.model_stats[model_name]
        model.context_records.append(record)
        
        # Track agent usage for this model
        if agent_id not in model.agent_usage:
            model.agent_usage[agent_id] = 0
        model.agent_usage[agent_id] += 1
    
    def _calculate_statistics(self) -> None:
        """Calculate statistics for all sessions, agents, and subtasks"""
        for session in self.sessions.values():
            session.total_context = 0
            session.max_context = 0
            session.min_context = float('inf')
            
            for agent in session.agent_stats.values():
                agent.total_context = 0
                agent.max_context = 0
                agent.min_context = float('inf')
                
                for subtask in agent.subtask_stats.values():
                    if not subtask.context_records:
                        continue
                        
                    contexts = [r.total_context for r in subtask.context_records]
                    subtask.total_context = sum(contexts)
                    subtask.max_context = max(contexts)
                    subtask.min_context = min(contexts)
                    subtask.avg_context = subtask.total_context / len(contexts)
                    
                    # Update agent stats
                    agent.total_context += subtask.total_context
                    agent.max_context = max(agent.max_context, subtask.max_context)
                    agent.min_context = min(agent.min_context, subtask.min_context)
                
                # Calculate agent averages
                if agent.context_records:
                    agent.avg_context = agent.total_context / len(agent.context_records)
                
                # Update session stats
                session.total_context += agent.total_context
                session.max_context = max(session.max_context, agent.max_context)
                session.min_context = min(session.min_context, agent.min_context)
            
            # Calculate model statistics
            for model in session.model_stats.values():
                if not model.context_records:
                    continue
                    
                contexts = [r.total_context for r in model.context_records]
                model.total_context = sum(contexts)
                model.max_context = max(contexts)
                model.min_context = min(contexts)
                model.avg_context = model.total_context / len(contexts)
            
            # Calculate session averages
            total_records = sum(len(agent.context_records) for agent in session.agent_stats.values())
            if total_records > 0:
                session.avg_context = session.total_context / total_records
    
    def analyze_session(self, session_id: str) -> Optional[SessionStats]:
        """
        Analyze context usage for a specific session
        
        Args:
            session_id: The session ID to analyze
            
        Returns:
            SessionStats object if session exists, None otherwise
        """
        if session_id not in self.sessions:
            print(f"Session not found: {session_id}")
            if self.sessions:
                print(f"\nAvailable sessions in log ({len(self.sessions)}):")
                print("=" * 80)
                for sid in sorted(self.sessions.keys()):
                    session = self.sessions[sid]
                    duration = "N/A"
                    if session.start_time and session.end_time:
                        duration = str(session.end_time - session.start_time)
                    print(f"  {sid}")
                    print(f"     {session.start_time} ~ {session.end_time}")
                    print(f"     {duration} | {session.total_context:,} tokens | {len(session.agent_stats)} agents")
                print()
            else:
                print("No session data found in log file.")
            return None
        
        return self.sessions[session_id]
    
    def print_tree_structure(self, session_id: str) -> None:
        """
        Print tree structure of context usage for a session
        
        Args:
            session_id: The session ID to analyze
        """
        session = self.analyze_session(session_id)
        if not session:
            return
        
        print(f"\nSession {session_id} context usage tree")
        print("=" * 80)
        
        # Session summary
        duration = "N/A"
        if session.start_time and session.end_time:
            duration = str(session.end_time - session.start_time)
        
        print("Session overview:")
        print(f"   Time: {session.start_time} ~ {session.end_time}")
        print(f"   Duration: {duration}")
        print(f"   Total context: {session.total_context:,} tokens")
        print(f"   Avg context: {session.avg_context:.1f} tokens")
        print(f"   Min context: {session.min_context:,} tokens")
        print(f"   Max context: {session.max_context:,} tokens")
        print()
        
        # Agent breakdown
        print("Agent breakdown:")
        for agent_id, agent in session.agent_stats.items():
            print(f"  ├─ {agent_id}")
            print(f"  │  ├─ Total: {agent.total_context:,} tokens")
            print(f"  │  ├─ Avg: {agent.avg_context:.1f} tokens")
            print(f"  │  ├─ Min: {agent.min_context:,} tokens")
            print(f"  │  ├─ Max: {agent.max_context:,} tokens")
            print(f"  │  └─ Records: {len(agent.context_records)}")
            
            # Subtask breakdown
            if agent.subtask_stats:
                print(f"  │  └─ Subtasks:")
                for i, (subtask_id, subtask) in enumerate(agent.subtask_stats.items()):
                    is_last = i == len(agent.subtask_stats) - 1
                    prefix = "  │     └─" if is_last else "  │     ├─"
                    print(f"  {prefix} {subtask_id}")
                    print(f"  │     {'  ' if is_last else '│  '} ├─ Total: {subtask.total_context:,} tokens")
                    print(f"  │     {'  ' if is_last else '│  '} ├─ Avg: {subtask.avg_context:.1f} tokens")
                    print(f"  │     {'  ' if is_last else '│  '} ├─ Min: {subtask.min_context:,} tokens")
                    print(f"  │     {'  ' if is_last else '│  '} ├─ Max: {subtask.max_context:,} tokens")
                    print(f"  │     {'  ' if is_last else '│  '} └─ Records: {len(subtask.context_records)}")
            print()
        
        # Model breakdown
        if session.model_stats:
            print("Model breakdown:")
            for model_name, model in session.model_stats.items():
                print(f"  ├─ {model_name}")
                print(f"  │  ├─ Total: {model.total_context:,} tokens")
                print(f"  │  ├─ Avg: {model.avg_context:.1f} tokens")
                print(f"  │  ├─ Min: {model.min_context:,} tokens")
                print(f"  │  ├─ Max: {model.max_context:,} tokens")
                print(f"  │  ├─ Records: {len(model.context_records)}")
                
                # Agent usage for this model
                if model.agent_usage:
                    print(f"  │  └─ Agent usage:")
                    for agent_id, usage_count in model.agent_usage.items():
                        print(f"  │     └─ {agent_id}: {usage_count} calls")
                print()
    
    def plot_context_trends(self, session_id: str, save_path: Optional[str] = None) -> None:
        """
        Plot context usage trends over time for a session
        
        Args:
            session_id: The session ID to analyze
            save_path: Optional path to save the plot
        """
        session = self.analyze_session(session_id)
        if not session:
            return
        
        print(f"Generating context trend chart for session {session_id}...")
        
        # Set up the plot with full width
        fig, ax1 = plt.subplots(1, 1, figsize=(20, 8))
        
        # Plot: Context usage by turn number for each agent-task combination
        # Group records by agent_id and task_id combination
        agent_task_groups = {}
        for agent_id, agent in session.agent_stats.items():
            for subtask_id, subtask in agent.subtask_stats.items():
                if not subtask.context_records:
                    continue
                key = f"{agent_id}_{subtask_id}"
                agent_task_groups[key] = {
                    'agent_id': agent_id,
                    'task_id': subtask_id,
                    'records': subtask.context_records
                }
        
        # Use dark color palette
        dark_colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
                      '#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5',
                      '#c49c94', '#f7b6d3', '#c7c7c7', '#dbdb8d', '#9edae5']
        
        for i, (key, group) in enumerate(agent_task_groups.items()):
            records = group['records']
            # Sort by timestamp to ensure proper order
            sorted_records = sorted(records, key=lambda x: x.timestamp)
            turn_numbers = list(range(1, len(sorted_records) + 1))
            contexts_k = [r.total_context / 1000.0 for r in sorted_records]
            
            # Each point represents one turn/round
            num_turns = len(records)
            total_k = sum(r.total_context for r in records) / 1000.0
            color = dark_colors[i % len(dark_colors)]
            ax1.plot(turn_numbers, contexts_k, 
                    marker='o', markersize=6, linewidth=2.5,
                    color=color, 
                    label=f'{group["agent_id"]}_{group["task_id"]} ({num_turns} turns, Total: {total_k:.1f}K)',
                    alpha=0.9, markeredgewidth=1.2, markeredgecolor='white')
        
        # Set title and labels
        ax1.set_title(f'Session {session_id} - Agent-Task Context Usage Trend\n(Each point represents one turn)', 
                     fontsize=16, fontweight='bold')
        ax1.set_xlabel('Turn Number', fontsize=12)
        ax1.set_ylabel('Context Length (K tokens)', fontsize=12)
        
        # Place legend at the bottom
        ax1.legend(bbox_to_anchor=(0.5, -0.15), loc='upper center', ncol=3, 
                  frameon=True, fancybox=True, shadow=True)
        ax1.grid(True, alpha=0.3)
        
        
        plt.tight_layout()
        
        # Save or show plot
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"Chart saved to: {save_path}")
        else:
            plt.show()
    
    def plot_session_comparison(self, session_id1: str, session_id2: str, save_path: Optional[str] = None) -> None:
        """
        Plot context usage trends comparison between two sessions
        
        Args:
            session_id1: The first session ID to compare
            session_id2: The second session ID to compare
            save_path: Optional path to save the plot
        """
        session1 = self.analyze_session(session_id1)
        session2 = self.analyze_session(session_id2)
        
        if not session1 or not session2:
            return
        
        print(f"Generating session comparison chart: {session_id1} vs {session_id2}...")
        
        # Set up the plot with full width
        fig, ax = plt.subplots(1, 1, figsize=(20, 10))
        
        # Collect all records from both sessions and sort by timestamp
        # Use turn number (1, 2, 3, ...) as X-axis
        def prepare_turn_data(records: List[ContextRecord]) -> Tuple[List[int], List[float]]:
            """Prepare data with turn numbers as X-axis and context in K tokens"""
            if not records:
                return [], []
            # Sort by timestamp
            sorted_records = sorted(records, key=lambda x: x.timestamp)
            turn_numbers = list(range(1, len(sorted_records) + 1))
            contexts_k = [r.total_context / 1000.0 for r in sorted_records]
            return turn_numbers, contexts_k
        
        # Plot session 1 - aggregate all agent-task combinations
        # Each point represents one turn/round
        all_records1 = []
        for agent in session1.agent_stats.values():
            for subtask in agent.subtask_stats.values():
                all_records1.extend(subtask.context_records)
        
        num_turns1 = len(all_records1)
        if all_records1:
            turn_numbers1, contexts1_k = prepare_turn_data(all_records1)
            if turn_numbers1:
                total_k1 = session1.total_context / 1000.0
                avg_k1 = session1.avg_context / 1000.0
                ax.plot(turn_numbers1, contexts1_k, 
                       marker='o', markersize=8, linewidth=2.5,
                       color='#1f77b4', alpha=0.8,
                       label=f'{session_id1} ({num_turns1} turns, Total: {total_k1:.1f}K, Avg: {avg_k1:.1f}K)',
                       linestyle='-', markeredgewidth=1.5, markeredgecolor='white')
        
        # Plot session 2 - aggregate all agent-task combinations
        # Each point represents one turn/round
        all_records2 = []
        for agent in session2.agent_stats.values():
            for subtask in agent.subtask_stats.values():
                all_records2.extend(subtask.context_records)
        
        num_turns2 = len(all_records2)
        if all_records2:
            turn_numbers2, contexts2_k = prepare_turn_data(all_records2)
            if turn_numbers2:
                total_k2 = session2.total_context / 1000.0
                avg_k2 = session2.avg_context / 1000.0
                ax.plot(turn_numbers2, contexts2_k, 
                       marker='s', markersize=8, linewidth=2.5,
                       color='#ff7f0e', alpha=0.8,
                       label=f'{session_id2} ({num_turns2} turns, Total: {total_k2:.1f}K, Avg: {avg_k2:.1f}K)',
                       linestyle='--', markeredgewidth=1.5, markeredgecolor='white')
        
        # Add summary statistics as text
        total_k1 = session1.total_context / 1000.0
        avg_k1 = session1.avg_context / 1000.0
        max_k1 = session1.max_context / 1000.0
        min_k1 = session1.min_context / 1000.0
        total_k2 = session2.total_context / 1000.0
        avg_k2 = session2.avg_context / 1000.0
        max_k2 = session2.max_context / 1000.0
        min_k2 = session2.min_context / 1000.0
        
        # Format duration to show only up to seconds
        def format_duration(start_time: Optional[datetime], end_time: Optional[datetime]) -> str:
            """Format duration as HH:MM:SS"""
            if not start_time or not end_time:
                return 'N/A'
            delta = end_time - start_time
            total_seconds = int(delta.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        duration1 = format_duration(session1.start_time, session1.end_time)
        duration2 = format_duration(session2.start_time, session2.end_time)
        
        # Calculate comparisons
        # Token comparison
        token_diff = total_k2 - total_k1
        token_diff_pct = (token_diff / total_k1 * 100) if total_k1 > 0 else 0
        token_comparison = f"{token_diff:+.1f}K ({token_diff_pct:+.1f}%)"
        
        # Turn comparison
        turn_diff = num_turns2 - num_turns1
        turn_diff_pct = (turn_diff / num_turns1 * 100) if num_turns1 > 0 else 0
        turn_comparison = f"{turn_diff:+d} ({turn_diff_pct:+.1f}%)"
        
        # Duration comparison
        def duration_to_seconds(start_time: Optional[datetime], end_time: Optional[datetime]) -> int:
            """Convert duration to total seconds"""
            if not start_time or not end_time:
                return 0
            return int((end_time - start_time).total_seconds())
        
        duration1_sec = duration_to_seconds(session1.start_time, session1.end_time)
        duration2_sec = duration_to_seconds(session2.start_time, session2.end_time)
        duration_diff_sec = duration2_sec - duration1_sec
        duration_diff_pct = (duration_diff_sec / duration1_sec * 100) if duration1_sec > 0 else 0
        
        # Format duration difference
        def format_duration_diff(seconds: int) -> str:
            """Format duration difference as HH:MM:SS"""
            abs_seconds = abs(seconds)
            hours = abs_seconds // 3600
            minutes = (abs_seconds % 3600) // 60
            secs = abs_seconds % 60
            sign = "+" if seconds >= 0 else "-"
            return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"
        
        duration_comparison = f"{format_duration_diff(duration_diff_sec)} ({duration_diff_pct:+.1f}%)"
        
        stats_text = (
            f"Session 1 ({session_id1}):\n"
            f"  Turns: {num_turns1}\n"
            f"  Total: {total_k1:.1f}K tokens\n"
            # f"  Avg: {avg_k1:.1f}K tokens\n"
            # f"  Max: {max_k1:.1f}K tokens\n"
            # f"  Min: {min_k1:.1f}K tokens\n"
            f"  Agents: {len(session1.agent_stats)}\n"
            f"  Duration: {duration1}\n\n"
            f"Session 2 ({session_id2}):\n"
            f"  Turns: {num_turns2}\n"
            f"  Total: {total_k2:.1f}K tokens\n"
            # f"  Avg: {avg_k2:.1f}K tokens\n"
            # f"  Max: {max_k2:.1f}K tokens\n"
            # f"  Min: {min_k2:.1f}K tokens\n"
            f"  Agents: {len(session2.agent_stats)}\n"
            f"  Duration: {duration2}\n\n"
            f"Comparison (S2 - S1):\n"
            f"  Tokens: {token_comparison}\n"
            f"  Turns: {turn_comparison}\n"
            f"  Duration: {duration_comparison}"
        )
        
        # Set title and labels
        ax.set_title(f'Context Window Comparison: \n {session_id1} vs {session_id2}', 
                    fontsize=16, fontweight='bold')
        ax.set_xlabel('Turn Number', fontsize=12)
        ax.set_ylabel('Context Length (K tokens)', fontsize=12)
        
        # Place stats text in upper left corner (bold)
        ax.text(0.02, 0.98, stats_text,
               transform=ax.transAxes,
               fontsize=8.5,
               verticalalignment='top',
               horizontalalignment='left',
               weight='bold',
               bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8, linewidth=2))
        
        # Place legend in upper right corner
        ax.legend(loc='upper right', frameon=True, fancybox=True, shadow=True, fontsize=11)
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Save or show plot
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"Comparison chart saved to: {save_path}")
        else:
            plt.show()
    
    def plot_agent_comparison(self, session_id: str, save_path: Optional[str] = None) -> None:
        """
        Plot agent comparison charts
        
        Args:
            session_id: The session ID to analyze
            save_path: Optional path to save the plot
        """
        session = self.analyze_session(session_id)
        if not session:
            return
        
        print(f"Generating Agent comparison chart for session {session_id}...")
        
        # Set up the plot with full width
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(20, 12))
        
        try:
            plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
        except Exception:
            pass
        
        # Prepare data
        agent_names = list(session.agent_stats.keys())
        total_contexts = [agent.total_context for agent in session.agent_stats.values()]
        avg_contexts = [agent.avg_context for agent in session.agent_stats.values()]
        max_contexts = [agent.max_context for agent in session.agent_stats.values()]
        record_counts = [len(agent.context_records) for agent in session.agent_stats.values()]
        
        # Use dark color palette
        dark_colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
        
        # Plot 1: Average context usage by agent
        bars1 = ax1.bar(range(len(agent_names)), avg_contexts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(agent_names))], 
                       alpha=0.8)
        ax1.set_title('Average Context Usage by Agent', fontsize=14, fontweight='bold')
        ax1.set_xlabel('Agent')
        ax1.set_ylabel('Average Context (tokens)')
        ax1.set_xticks(range(len(agent_names)))
        ax1.set_xticklabels(agent_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars1):
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{avg_contexts[i]:.1f}', ha='center', va='bottom', fontsize=10)
        
        # Plot 2: Max context usage by agent
        bars2 = ax2.bar(range(len(agent_names)), max_contexts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(agent_names))], 
                       alpha=0.8)
        ax2.set_title('Max Context Usage by Agent', fontsize=14, fontweight='bold')
        ax2.set_xlabel('Agent')
        ax2.set_ylabel('Max Context (tokens)')
        ax2.set_xticks(range(len(agent_names)))
        ax2.set_xticklabels(agent_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars2):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{max_contexts[i]:,}', ha='center', va='bottom', fontsize=10)
        
        # Plot 3: Total context usage by agent
        bars3 = ax3.bar(range(len(agent_names)), total_contexts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(agent_names))], 
                       alpha=0.8)
        ax3.set_title('Total Context Usage by Agent', fontsize=14, fontweight='bold')
        ax3.set_xlabel('Agent')
        ax3.set_ylabel('Total Context (tokens)')
        ax3.set_xticks(range(len(agent_names)))
        ax3.set_xticklabels(agent_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars3):
            height = bar.get_height()
            ax3.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{total_contexts[i]:,}', ha='center', va='bottom', fontsize=10)
        
        # Plot 4: Record count by agent
        bars4 = ax4.bar(range(len(agent_names)), record_counts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(agent_names))], 
                       alpha=0.8)
        ax4.set_title('Record Count by Agent', fontsize=14, fontweight='bold')
        ax4.set_xlabel('Agent')
        ax4.set_ylabel('Record Count')
        ax4.set_xticks(range(len(agent_names)))
        ax4.set_xticklabels(agent_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars4):
            height = bar.get_height()
            ax4.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{record_counts[i]}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        
        # Save or show plot
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"Chart saved: {save_path}")
        else:
            plt.show()
    
    def plot_model_comparison(self, session_id: str, save_path: Optional[str] = None) -> None:
        """
        Plot model comparison charts
        
        Args:
            session_id: The session ID to analyze
            save_path: Optional path to save the plot
        """
        session = self.analyze_session(session_id)
        if not session:
            return
        
        print(f"Generating Model comparison chart for session {session_id}...")
        
        # Set up the plot with full width
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(20, 12))
        
        try:
            plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
        except Exception:
            pass
        
        # Prepare data
        model_names = list(session.model_stats.keys())
        total_contexts = [model.total_context for model in session.model_stats.values()]
        avg_contexts = [model.avg_context for model in session.model_stats.values()]
        max_contexts = [model.max_context for model in session.model_stats.values()]
        record_counts = [len(model.context_records) for model in session.model_stats.values()]
        
        # Use dark color palette
        dark_colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
        
        # Plot 1: Average context usage by model
        bars1 = ax1.bar(range(len(model_names)), avg_contexts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(model_names))], 
                       alpha=0.8)
        ax1.set_title('Average Context Usage by Model', fontsize=14, fontweight='bold')
        ax1.set_xlabel('Model')
        ax1.set_ylabel('Average Context (tokens)')
        ax1.set_xticks(range(len(model_names)))
        ax1.set_xticklabels(model_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars1):
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{avg_contexts[i]:.1f}', ha='center', va='bottom', fontsize=10)
        
        # Plot 2: Max context usage by model
        bars2 = ax2.bar(range(len(model_names)), max_contexts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(model_names))], 
                       alpha=0.8)
        ax2.set_title('Max Context Usage by Model', fontsize=14, fontweight='bold')
        ax2.set_xlabel('Model')
        ax2.set_ylabel('Max Context (tokens)')
        ax2.set_xticks(range(len(model_names)))
        ax2.set_xticklabels(model_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars2):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{max_contexts[i]:,}', ha='center', va='bottom', fontsize=10)
        
        # Plot 3: Total context usage by model
        bars3 = ax3.bar(range(len(model_names)), total_contexts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(model_names))], 
                       alpha=0.8)
        ax3.set_title('Total Context Usage by Model', fontsize=14, fontweight='bold')
        ax3.set_xlabel('Model')
        ax3.set_ylabel('Total Context (tokens)')
        ax3.set_xticks(range(len(model_names)))
        ax3.set_xticklabels(model_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars3):
            height = bar.get_height()
            ax3.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{total_contexts[i]:,}', ha='center', va='bottom', fontsize=10)
        
        # Plot 4: Record count by model
        bars4 = ax4.bar(range(len(model_names)), record_counts, 
                       color=[dark_colors[i % len(dark_colors)] for i in range(len(model_names))], 
                       alpha=0.8)
        ax4.set_title('Record Count by Model', fontsize=14, fontweight='bold')
        ax4.set_xlabel('Model')
        ax4.set_ylabel('Record Count')
        ax4.set_xticks(range(len(model_names)))
        ax4.set_xticklabels(model_names, rotation=45, ha='right')
        
        # Add value labels on bars
        for i, bar in enumerate(bars4):
            height = bar.get_height()
            ax4.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{record_counts[i]}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        
        # Save or show plot
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"Chart saved: {save_path}")
        else:
            plt.show()
    
    def list_sessions(self) -> None:
        """List all available sessions"""
        if not self.sessions:
            print("No session data found.")
            return
        
        print("Sessions:")
        print("=" * 80)
        
        for session_id, session in self.sessions.items():
            duration = "N/A"
            if session.start_time and session.end_time:
                duration = str(session.end_time - session.start_time)
            
            print(f"  Session: {session_id}")
            print(f"   Time: {session.start_time} ~ {session.end_time}")
            print(f"   Duration: {duration}")
            print(f"   Total context: {session.total_context:,} tokens")
            print(f"   Agents: {len(session.agent_stats)}")
            print()


def main():
    """Main function for command line usage"""
    parser = argparse.ArgumentParser(description='Context usage statistics tool')
    parser.add_argument('log_file', help='Path to log file')
    parser.add_argument('session_id', nargs='?', help='Session ID to analyze (optional when using --compare-sessions)')
    parser.add_argument('--tree', action='store_true', help='Print tree structure')
    parser.add_argument('--trend', action='store_true', help='Plot trend chart')
    parser.add_argument('--compare', action='store_true', help='Plot Agent comparison chart')
    parser.add_argument('--model-compare', action='store_true', help='Plot Model comparison chart')
    parser.add_argument('--compare-sessions', nargs=2, metavar=('SESSION1', 'SESSION2'), 
                       help='Compare trend of two sessions (provide two session_id)')
    parser.add_argument('--list', action='store_true', help='List all sessions')
    parser.add_argument('--save-trend', help='Save trend chart to path')
    parser.add_argument('--save-compare', help='Save Agent comparison chart to path')
    parser.add_argument('--save-model-compare', help='Save Model comparison chart to path')
    parser.add_argument('--save-session-compare', help='Save session comparison chart to path')
    
    args = parser.parse_args()
    
    # Initialize analyzer
    analyzer = ContextAnalyzer(args.log_file)
    
    # Parse log file
    analyzer.parse_log_file()
    
    # List sessions if requested
    if args.list:
        analyzer.list_sessions()
        return
    
    # Handle session comparison
    if args.compare_sessions:
        session_id1, session_id2 = args.compare_sessions
        analyzer.plot_session_comparison(session_id1, session_id2, args.save_session_compare)
        return
    
    # Check if session_id is provided for other operations
    if not args.session_id:
        print("Error: provide session_id or use --compare-sessions")
        print("Use --list to see available sessions.")
        return
    
    # Analyze specific session
    if args.tree:
        analyzer.print_tree_structure(args.session_id)
    
    if args.trend:
        analyzer.plot_context_trends(args.session_id, args.save_trend)
    
    if args.compare:
        analyzer.plot_agent_comparison(args.session_id, args.save_compare)
    
    if args.model_compare:
        analyzer.plot_model_comparison(args.session_id, args.save_model_compare)
    
    # If no specific action requested, show tree structure by default
    if not any([args.tree, args.trend, args.compare, args.model_compare]):
        analyzer.print_tree_structure(args.session_id)


if __name__ == "__main__":
    main()
