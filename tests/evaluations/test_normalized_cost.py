from __future__ import annotations

import pytest

from aworld.evaluations.normalized_cost import (
    NormalizedCostPolicy,
    compute_normalized_cost,
)


def test_normalized_cost_policy_round_trip_and_cache_adjustment():
    policy = NormalizedCostPolicy()
    assert NormalizedCostPolicy.from_dict(policy.to_dict()) == policy

    receipt = compute_normalized_cost(
        policy=policy,
        input_tokens=1_000,
        cache_read_tokens=800,
        output_tokens=100,
    )

    assert receipt.normalized_cost == 380.0
    assert receipt.policy_hash == policy.policy_hash
    assert receipt.to_dict()["total_microunits"] == 380_000_000


def test_normalized_cost_rejects_tampered_policy_and_impossible_usage():
    payload = NormalizedCostPolicy().to_dict()
    payload["cache_read_microunits_per_token"] = 1
    with pytest.raises(ValueError, match="hash mismatch"):
        NormalizedCostPolicy.from_dict(payload)

    with pytest.raises(ValueError, match="cannot exceed"):
        compute_normalized_cost(
            policy=NormalizedCostPolicy(),
            input_tokens=10,
            cache_read_tokens=11,
            output_tokens=0,
        )
