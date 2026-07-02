# coding: utf-8

import hashlib
import json
from typing import Any


def compute_stable_prefix_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
