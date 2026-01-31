# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from collections import OrderedDict
from typing import List

import yaml

docs = "docs"
black_keys = ["Index", "docs_zh", "DESIGN_SYSTEM"]
black_values = ["index.md"]
file_priority = {"Guides": ["Overview", "Quick Start", "Core Capabilities", "Parallel Tasks", "Streaming Response", "Human in the Loop (HITL)"],
                 "Agents": ["Build Agent", "Custom Agent", "Multi-Agent System(MAS)", "Workflow", "Context", "Memory", "Runtime", "Trace"],
                 "Runtime": ["Overview", "Custom Runner", "Hooks"],
                 "Environment": ["Overview", "Env Client", "Advanced Capabilities"],
                 "Training": ["Evaluation", "Trainer"],
                 "Deployment": ["OceanBase Setup"]}
file_mapping = {"Hitl": "Human in the Loop (HITL)", "Get Start": "Guides",
                "Build Multi-Agent System(Mas)": "Multi-Agent System(MAS)",
                "Build Workflow": "Workflow"}
dir_order = ["Get Start", "Agents", "Environment", "Training", "Deployment"]


def scan_path(path: str) -> List[dict]:
    items = scan(path)
    res = []
    order_items = OrderedDict()
    for d in dir_order:
        if d in items:
            order_items[d] = items[d]
            items.pop(d)
    order_items.update(items)

    for k, v in order_items.items():
        # root path
        if k in black_keys and v in black_values:
            continue

        # Skip docs_zh directory entirely
        if k == "docs_zh":
            continue

        # Apply directory name mapping
        display_name = file_mapping.get(k, k)

        # files in dir
        final_map = OrderedDict()
        for file in file_priority.get(display_name, []):
            if file in v:
                final_map[file_mapping.get(file, file)] = v[file]
                v.pop(file)
        final_map.update(v)

        res.append({display_name: dict(final_map)})
    return res


def scan(path: str) -> dict:
    items = {}
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if name.startswith("."):
            continue

        # Skip blacklisted items
        if name in black_keys or name.startswith("DESIGN_"):
            continue

        if os.path.isdir(p):
            # Skip docs_zh directory
            if name == "docs_zh":
                continue
            children = scan(p)
            if children:
                items[name] = children
        elif name.endswith(".md"):
            words = os.path.splitext(name)[0].split('_')
            key = ' '.join([w.title() if w else w for w in words])
            items[key] = os.path.relpath(p, docs).replace(os.sep, "/")
    return items


if __name__ == '__main__':
    outline = scan_path(docs)

    theme_cfg = {
        "name": "material",
        "language": "en",
        "logo": "img/aworld.png",
        "favicon": "img/aworld.png",
        "features": [
            "navigation.instant",
            "navigation.tracking",
            "navigation.tabs",
            "toc.follow",
            "content.code.copy",
            "content.code.annotate",
            "content.action.edit",
            "header.autohide",
        ],
        "palette": [
            {
                "media": "(prefers-color-scheme: light)",
                "scheme": "default",
                "primary": "blue",
                "accent": "deep purple",
            },
            {
                "media": "(prefers-color-scheme: dark)",
                "scheme": "slate",
                "primary": "blue",
                "accent": "deep purple",
            },
        ],
        "font": {
            "text": "Inter",
            "code": "JetBrains Mono",
        },
    }

    cfg = {
        "site_name": "AWorld (Agent World) harness",
        "site_url": "https://github.com/inclusionAI/AWorld",
        "repo_url": "https://github.com/inclusionAI/AWorld",
        "repo_name": "inclusionAI/AWorld",
        "edit_uri": "tree/main/docs/",
        "copyright": "\u00A9 Copyright 2025 inclusionAI AWorld Team.",
        "extra_css": ["css/aworld.css"],
        "extra_javascript": [
            "js/hide-home-edit.js",
            "js/aworld-enhancements.js",
            "js/github-stars.js",
            "js/github-stats-fallback.js",
        ],
        "markdown_extensions": [
            {
                "pymdownx.highlight": {
                    "anchor_linenums": True,
                    "line_spans": "__span",
                    "pygments_lang_class": True,
                }
            },
            "pymdownx.inlinehilite",
            "pymdownx.snippets",
            "pymdownx.superfences",
        ],
        "theme": theme_cfg,
        "nav": outline,
    }

    index_content = ["# Welcome to AWorld’s Documentation!"]
    # standard structure
    for line in outline:
        for k, v in line.items():
            index_content.append(f"## {k}")
            for s_k, s_v in v.items():
                index_content.append(f"[{s_k}]({s_v})")

    with open("index.md", 'w') as index_file:
        index_file.write("\n\n".join(index_content))

    with open('mkdocs.yml', 'w') as outfile:
        yaml.safe_dump(cfg, outfile, sort_keys=False, allow_unicode=True)
