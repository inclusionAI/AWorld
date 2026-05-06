# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from collections import OrderedDict
from typing import List

import yaml

docs = "docs"
black_keys = ["Index", "docs_zh", "DESIGN_SYSTEM"]
black_values = ["index.md"]
file_priority = {"Get Start": ["Overview", "Quick Start", "Core Capabilities", "Parallel Tasks", "Streaming Response", "Hitl"],
                 "Agents": ["Build Agent", "Build Multi-Agent System(Mas)", "Build Workflow", "Custom Agent", "Context", "Runtime", "Memory", "Trace", "Parallel Subagents"],
                 "Runtime": ["Overview", "Custom Runner", "Hooks", "Ralph Runner"],
                 "AWorld CLI": ["Overview", "Installation", "Configuration", "Commands", "Hooks", "Plugins", "Recipes"],
                 "Commands": ["Overview", "Memory", "Cron", "Plugins", "Gateway", "Parallel Tasks"],
                 "Hooks": ["Overview", "Examples"],
                 "Plugins": ["Overview", "Plugin Sdk", "Ralph Session Loop"],
                 "Recipes": ["Overview", "Deep Search", "Mini App Build", "Video Creation"],
                 "Environment": ["Overview", "Using Api", "Env Client", "Advanced Capabilities", "Oceanbase"],
                 "Training": ["Trainer", "Trajectory", "Evaluation"]}
file_mapping = {"Hitl": "Human in the Loop (HITL)",
                "Build Multi-Agent System(Mas)": "Multi-Agent System(MAS)",
                "Build Workflow": "Workflow", "Using Api": "Using API",
                "Plugin Sdk": "Plugin SDK", "Oceanbase": "OceanBase",
                "Readme": "README"}
dir_order = ["Get Start", "Agents", "AWorld CLI", "Environment", "Training"]


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

        if not isinstance(v, dict):
            res.append({display_name: v})
            continue

        # files in dir
        final_map = OrderedDict()
        for file in file_priority.get(display_name, []):
            if file in v:
                # sub dir
                if isinstance(v[file], dict):
                    new_dict = OrderedDict()
                    for in_file in file_priority.get(file, []):
                        if in_file in v[file]:
                            new_dict[file_mapping.get(in_file, in_file)] = v[file].pop(in_file)
                    for leftover_key, leftover_value in v[file].items():
                        new_dict[file_mapping.get(leftover_key, leftover_key)] = leftover_value
                    final_map[file_mapping.get(file, file)] = dict(new_dict)
                else:
                    final_map[file_mapping.get(file, file)] = v[file]
                v.pop(file)
        final_map.update(v)

        res.append({display_name: _to_nav(final_map)})
    return res


def _to_nav(value):
    if isinstance(value, dict):
        return [{file_mapping.get(key, key): _to_nav(item)} for key, item in value.items()]
    return value


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
        "logo": "imgs/logo.png",
        "favicon": "imgs/logo.png",
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
            "codehilite",
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
            if isinstance(v, dict):
                index_content.append(f"## {k}")
                for s_k, s_v in v.items():
                    index_content.append(f"[{s_k}]({s_v})")
            else:
                index_content.append(f"[{k}]({v})")

    with open("index.md", 'w') as index_file:
        index_file.write("\n\n".join(index_content))

    with open('mkdocs.yml', 'w') as outfile:
        yaml.safe_dump(cfg, outfile, sort_keys=False, allow_unicode=True)
