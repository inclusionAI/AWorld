# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import math
import re
from collections import Counter


def verl_default_reward_func(data_source, solution_str, ground_truth, extra_info=None):
    """Default reward function."""
    return semantic_similarity(solution_str, ground_truth)


def cosine_similarity(text1: str, text2: str) -> float:
    """No dependency calculate cosine similarity using term frequency vectors."""
    if not text1 and not text2:
        return 1.0
    if not text1 or not text2:
        return 0.0

    words1 = re.split(r'\W+', text1.lower())
    words2 = re.split(r'\W+', text2.lower())

    words1 = [w for w in words1 if w]
    words2 = [w for w in words2 if w]

    freq1 = Counter(words1)
    freq2 = Counter(words2)

    all_words = set(freq1.keys()).union(set(freq2.keys()))

    if not all_words:
        return 1.0 if text1 == text2 else 0.0

    vector1 = [freq1.get(word, 0) for word in all_words]
    vector2 = [freq2.get(word, 0) for word in all_words]

    dot_product = sum(a * b for a, b in zip(vector1, vector2))

    magnitude1 = math.sqrt(sum(a * a for a in vector1))
    magnitude2 = math.sqrt(sum(b * b for b in vector2))

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    cosine_sim = dot_product / (magnitude1 * magnitude2)

    return max(0.0, min(1.0, cosine_sim))


def semantic_similarity(text1: str, text2: str, embedding_func=None) -> float:
    if embedding_func is None:
        return cosine_similarity(text1, text2)

    try:
        emb1 = embedding_func(text1)
        emb2 = embedding_func(text2)

        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        magnitude1 = math.sqrt(sum(a * a for a in emb1))
        magnitude2 = math.sqrt(sum(b * b for b in emb2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        cosine_sim = dot_product / (magnitude1 * magnitude2)

        return max(0.0, min(1.0, (cosine_sim + 1) / 2))
    except Exception:
        return cosine_similarity(text1, text2)
