from __future__ import annotations

import pytest

from aworld.core.context.compiler import (
    CandidateRequestForbidden,
    CandidateRequestRequired,
    ContextCompilerMode,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    select_rollout_request,
)


def _snapshot(content: str) -> ProviderRequestSnapshot:
    return ProviderRequestSnapshot(
        request_id=f"request-{content}",
        provider_name="test-provider",
        payload={
            "messages": [{"role": "user", "content": content}],
            "tools": [],
            "params": {},
        },
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )


@pytest.mark.parametrize(
    "mode",
    [ContextCompilerMode.OFF, ContextCompilerMode.OBSERVE],
)
def test_off_and_observe_preserve_exact_legacy_snapshot(mode) -> None:
    legacy = _snapshot("legacy")

    selection = select_rollout_request(mode=mode, legacy_request=legacy)

    assert selection.provider_request is legacy
    assert selection.candidate_request is None
    assert selection.comparison is None
    assert selection.additional_external_actions == 0


def test_shadow_compares_candidate_but_keeps_exact_legacy_provider_request() -> None:
    legacy = _snapshot("legacy")
    candidate = _snapshot("candidate")

    selection = select_rollout_request(
        mode=ContextCompilerMode.SHADOW,
        legacy_request=legacy,
        candidate_request=candidate,
    )

    assert selection.provider_request is legacy
    assert selection.candidate_request is candidate
    assert selection.candidate_applied is False
    assert selection.additional_external_actions == 0
    assert selection.comparison is not None
    assert selection.comparison.exact is False
    assert selection.comparison.mismatch_paths == ("/messages/0/content",)


def test_enforce_selects_exact_candidate_snapshot_for_single_existing_call() -> None:
    legacy = _snapshot("legacy")
    candidate = _snapshot("candidate")

    selection = select_rollout_request(
        mode=ContextCompilerMode.ENFORCE,
        legacy_request=legacy,
        candidate_request=candidate,
    )

    assert selection.provider_request is candidate
    assert selection.candidate_applied is True
    assert selection.additional_external_actions == 0
    assert selection.comparison is not None


def test_rollout_modes_reject_candidate_computation_contract_mismatches() -> None:
    legacy = _snapshot("legacy")

    with pytest.raises(CandidateRequestForbidden) as forbidden:
        select_rollout_request(
            mode=ContextCompilerMode.OFF,
            legacy_request=legacy,
            candidate_request=_snapshot("candidate"),
        )
    assert forbidden.value.code == "candidate_request_forbidden"

    with pytest.raises(CandidateRequestRequired) as required:
        select_rollout_request(
            mode=ContextCompilerMode.SHADOW,
            legacy_request=legacy,
        )
    assert required.value.code == "candidate_request_required"


def test_shadow_comparison_hashes_secret_like_dynamic_mapping_keys() -> None:
    legacy = _snapshot("same")
    candidate = ProviderRequestSnapshot(
        request_id="candidate-secret-key",
        provider_name="test-provider",
        payload={
            "messages": [{"role": "user", "content": "same"}],
            "tools": [],
            "params": {"api-key-raw-secret": "value-secret"},
        },
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )

    selection = select_rollout_request(
        mode=ContextCompilerMode.SHADOW,
        legacy_request=legacy,
        candidate_request=candidate,
    )
    rendered = str(selection.comparison.to_dict())

    assert "api-key-raw-secret" not in rendered
    assert "value-secret" not in rendered
    assert selection.comparison.mismatch_paths[0].startswith(
        "/params/key:sha256:"
    )
