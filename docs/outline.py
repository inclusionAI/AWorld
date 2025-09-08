import os
from typing import List

import yaml

root = "docs"
black_keys = ["Index"]
black_values = ["index.md"]


def scan_path(path: str) -> List[dict]:
    items = scan(path)
    res = []
    for k, v in items.items():
        if k in black_keys and v in black_values:
            continue

        res.append({k: v})
    return res


def scan(path: str):
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
            items[key] = os.path.relpath(p, root).replace(os.sep, "/")
    return items


if __name__ == '__main__':
    cfg = {
        "site_name": "AWorld Docs",
        "site_url": "https://github.com/inclusionAI/AWorld",
        "repo_url": "https://github.com/inclusionAI/AWorld",
        "copyright": "\u00A9 Copyright 2025 inclusionAI AWorld Team.",
        "theme": "readthedocs",
        "nav": scan_path(root),
    }

    with open('mkdocs.yml', 'w') as outfile:
        yaml.safe_dump(cfg, outfile, sort_keys=False, allow_unicode=True)
