# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from textwrap import wrap

from aworld.config import load_config
from train.evolve.config import EvolutionConfig


def make_box(title: str, body_lines, padding: int = 1, width: int | None = None):
    """Create ASCII box.
    ┌──────────────────┐
    │ TITLE            │
    │ body line 1      │
    │ body line 2      │
    └──────────────────┘
    """
    if isinstance(body_lines, str):
        body_lines = body_lines.splitlines()

    content_lines = [title] + [line for line in body_lines if line is not None]
    max_len = max(len(line) for line in content_lines) if content_lines else len(title)
    box_width = width or (max_len + padding * 2)

    def pad(line: str) -> str:
        spaces = box_width - len(line)
        return line + " " * spaces

    top = "┌" + "─" * box_width + "┐"
    bottom = "└" + "─" * box_width + "┘"
    lines = [top]

    title_line = pad(title.upper())
    lines.append("│" + title_line + "│")

    for line in body_lines:
        if line is None:
            lines.append("│" + " " * box_width + "│")
        else:
            for part in wrap(line, box_width):
                lines.append("│" + pad(part) + "│")

    lines.append(bottom)
    return lines


def merge_columns(*cols, gap: int = 3):
    """Combine multiple boxes (each representing a row list) horizontally to form multiple columns."""
    max_height = max(len(c) for c in cols)
    padded_cols = []
    for col in cols:
        width = len(col[0])
        pad_line = " " * width
        if len(col) < max_height:
            col = col + [pad_line] * (max_height - len(col))
        padded_cols.append(col)

    merged = []
    sep = " " * gap
    for row_idx in range(max_height):
        merged.append(sep.join(col[row_idx] for col in padded_cols))

    return merged


def build_column_branch_arrows(cols, gap: int = 3, parent_center: int = None):
    """Generate a branch arrow in the "total score" style:
        1. A horizontal branch line (from the center of the first box to the center of the last box)
        2. Draw arrows downward from three positions on the horizontal branch line (the center of each box)


    Example：
         │          (Vertical line descending from the parent level)
     ┌───┼───┐      (Horizontal branch line)
     │   │   │      (Descending from the branch line)
     ▼   ▼   ▼      (The arrow points to boxes)
    """
    col_widths = [len(col[0]) for col in cols]
    total_width = sum(col_widths) + gap * (len(cols) - 1)

    centers = []
    cursor = 0
    for w in col_widths:
        center = cursor + w // 2
        centers.append(center)
        cursor += w + gap

    # no parent center is specified, use the center of the total width
    if parent_center is None:
        parent_center = total_width // 2

    # First line: A vertical line descending from the parent level (only at the center of the parent level)
    line_pipe_parent = [" "] * total_width
    if 0 <= parent_center < total_width:
        line_pipe_parent[parent_center] = "│"

    line_arrow_parent = [" "]
    # line_arrow_parent = [" "] * total_width
    # if 0 <= parent_center < total_width:
    #     line_arrow_parent[parent_center] = "▼"

    # Second line: horizontal branch line (From the first center to the last center)
    line_branch = [" "] * total_width
    if len(centers) > 0:
        first_center = centers[0]
        last_center = centers[-1]

        for i in range(first_center, last_center + 1):
            if 0 <= i < total_width:
                line_branch[i] = "─"

        for idx, c in enumerate(centers):
            if idx == 0:
                line_branch[c] = "┌"
            elif idx == len(centers) - 1:
                line_branch[c] = "┐"
            else:
                line_branch[c] = "┼"

    # Third row：vertical line extending downwards from the branch line
    line_pipe_branch = [" "] * total_width
    for c in centers:
        if 0 <= c < total_width:
            line_pipe_branch[c] = "│"

    # Fourth row: the arrow points to boxes
    line_arrow_branch = [" "] * total_width
    for c in centers:
        if 0 <= c < total_width:
            line_arrow_branch[c] = "▼"

    return [
        "".join(line_pipe_parent),
        "".join(line_arrow_parent),
        "".join(line_branch),
        "".join(line_pipe_branch),
        "".join(line_arrow_branch)
    ]


def assemble_centered_layout(
        goal_box,
        loop_box,
        middle_row,
        col_widths,
        branch_rows,
        hooks_box,
        output_box,
        gap: int = 4,
):
    """
    Assemble the full ASCII layout with unified centering and branching.
    This is extracted as a reusable helper for generic multi-branch diagrams.
    """
    # Basic widths
    goal_width = len(goal_box[0])
    loop_width = len(loop_box[0])
    middle_width = sum(col_widths) + gap * (len(col_widths) - 1)
    hooks_width = len(hooks_box[0])
    output_width = len(output_box[0])

    # Global width/center
    final_max_width = max(goal_width, loop_width, middle_width, hooks_width, output_width)
    final_center = final_max_width // 2

    lines = []

    # GOAL
    indent_goal = max(0, (final_max_width - goal_width) // 2)
    for line in goal_box:
        lines.append(" " * indent_goal + line)
    lines.append(" " * final_center + "   │")
    lines.append(" " * final_center + "   ▼")

    # LOOP
    indent_loop = max(0, (final_max_width - loop_width) // 2)
    for line in loop_box:
        lines.append(" " * indent_loop + line)

    # Parent arrow down to branches (skip the parent arrow provided by branch_rows)
    lines.append(" " * final_center + "   │")

    # Branch (use only last 3 lines of branch_rows: horizontal, pipes, arrows)
    branch_lines_to_use = branch_rows[2:] if len(branch_rows) >= 3 else branch_rows
    indent_branch = max(0, (final_max_width - middle_width) // 2)
    for branch_line in branch_lines_to_use:
        lines.append(" " * indent_branch + branch_line)

    # Three columns (already merged as middle_row)
    for line in middle_row:
        lines.append(" " * indent_branch + line)

    # # Downward multi-arrows from each column to a single merge
    centers = []
    cursor = 0
    for w in col_widths:
        centers.append(cursor + w // 2)
        cursor += w + gap
    centers_final = [indent_branch + c for c in centers]

    line_multi_pipe = [" "] * final_max_width
    for c in centers_final:
        if 0 <= c < final_max_width:
            line_multi_pipe[c] = "│"
    lines.append("".join(line_multi_pipe))

    line_multi_join = [" "] * final_max_width
    if centers_final:
        first_center = centers_final[0]
        last_center = centers_final[-1]
        for i in range(first_center, last_center + 1):
            if 0 <= i < final_max_width:
                line_multi_join[i] = "─"
        for c in centers_final:
            if 0 <= c < final_max_width:
                line_multi_join[c] = "┴"
    lines.append("".join(line_multi_join))

    # Single arrow down
    lines.append(" " * final_center + "   │")
    lines.append(" " * final_center + "   ▼")

    # HOOKS
    indent_hooks = max(0, (final_max_width - hooks_width) // 2)
    for line in hooks_box:
        lines.append(" " * indent_hooks + line)

    lines.append(" " * final_center + "   │")
    lines.append(" " * final_center + "   ▼")

    # OUTPUT
    indent_output = max(0, (final_max_width - output_width) // 2)
    for line in output_box:
        lines.append(" " * indent_output + line)

    return lines


def evolution_plan_render(config: dict) -> str:
    max_len = 0

    goal_box = make_box(
        "GOAL",
        [f"\"{config['goal']}\""],
    )
    max_len = max_len if len(goal_box[0]) <= max_len else len(goal_box[0])

    loop_box = make_box(
        "AGENT WORKFLOW",
        [config["agent_loop"]],
    )
    max_len = max_len if len(loop_box[0]) <= max_len else len(loop_box[0])

    # three columns：SUBAGENTS / SKILLS / TOOLS
    subagents_body = config.get("subagents", []) + [None]
    subagents_box = make_box("SUBAGENTS", subagents_body)
    max_len = max_len if len(subagents_box[0]) <= max_len else len(subagents_box[0])

    skills_body = config.get("skills", []) + [None]
    skills_box = make_box("SKILLS", skills_body)

    tools_lines = []
    builtins = config.get("tools", {}).get("built_in", [])
    if builtins:
        tools_lines.append("Built-in:")
        tools_lines.extend(builtins)
        tools_lines.append(None)

    mcp_tools = config.get("tools", {}).get("mcp", [])
    if mcp_tools:
        tools_lines.append("MCP:")
        tools_lines.extend(mcp_tools)
        tools_lines.append(None)

    custom_tools = config.get("tools", {}).get("custom", [])
    if custom_tools:
        tools_lines.append("Custom:")
        tools_lines.extend(custom_tools)

    tools_box = make_box("TOOLS", tools_lines)
    middle_row = merge_columns(subagents_box, skills_box, tools_box, gap=4)

    col_widths = [len(subagents_box[0]), len(skills_box[0]), len(tools_box[0])]
    total_middle_width = sum(col_widths) + 4 * (len(col_widths) - 1)
    loop_width = len(loop_box[0])

    max_width = max(loop_width, total_middle_width)
    middle_center = total_middle_width // 2

    branch_rows = build_column_branch_arrows(
        [subagents_box, skills_box, tools_box],
        gap=4,
        parent_center=middle_center
    )

    hooks_box = make_box("MODEL", config["model"])

    output_box = make_box("PLAN OUTPUT", config["plan_output"])

    lines = assemble_centered_layout(
        goal_box=goal_box,
        loop_box=loop_box,
        hooks_box=hooks_box,
        output_box=output_box,
        middle_row=middle_row,
        col_widths=col_widths,
        branch_rows=branch_rows,
        gap=4,
    )

    return "\n".join(lines)
