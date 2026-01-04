# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
from collections import OrderedDict
from typing import List

import yaml

docs = "."
black_keys = ["Index"]
black_values = ["index.md"]
file_priority = {"Get Start": ["Overview", "Quick start", "Core capabilities"],
                 "Runtime": ["Overview"]}
file_mapping = {"Hitl": "HITL"}
dir_order = ["Get Start", "Agents", "Environment", "Training", "Key Components"]

zh_v = ["开始", "智能体", "环境", "训练", "关键组件"]


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

        # files in dir
        final_map = OrderedDict()
        for file in file_priority.get(k, []):
            if file in v:
                final_map[file_mapping.get(file, file)] = v[file]
                v.pop(file)
        final_map.update(v)

        final_v = OrderedDict()
        if k == 'docs_zh':
            for d in zh_v:
                final_v[d] = v[d]
        if final_v:
            final_map = final_v

        res.append({k: dict(final_map)})
    return res


def scan(path: str) -> dict:
    items = {}
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if name.startswith("."):
            continue

        if os.path.isdir(p):
            children = scan(p)
            if children:
                items[name] = children
        elif name.endswith(".md"):
            words = os.path.splitext(name)[0].split('_')
            key = ' '.join([w.capitalize() for w in words])
            items[key] = os.path.relpath(p, docs).replace(os.sep, "/")
    return items


if __name__ == '__main__':
    outline = scan_path(docs)

    theme_cfg = {
        "name": "material",
        "language": "en",
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
        "site_name": "AWorld Docs",
        "site_url": "https://github.com/inclusionAI/AWorld",
        "repo_url": "https://github.com/inclusionAI/AWorld",
        "edit_uri": "tree/main/docs/",
        "copyright": "\u00A9 Copyright 2025 inclusionAI AWorld Team.",
        "extra_css": ["css/aworld.css"],
        "extra_javascript": [
            "js/hide-home-edit.js",
            "js/aworld-enhancements.js",
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
