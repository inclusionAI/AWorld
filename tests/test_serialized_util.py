from aworld.utils.serialized_util import to_serializable


def test_to_serializable_preserves_shared_compound_evidence():
    attribution = {"status": "available", "entries": [{"ordinal": 0}]}
    payload = {
        "provider_lowering": {"attribution": attribution},
        "provider_attribution": {"attribution": attribution},
    }

    serialized = to_serializable(payload)

    assert serialized["provider_lowering"]["attribution"] == attribution
    assert serialized["provider_attribution"]["attribution"] == attribution
    assert isinstance(serialized["provider_attribution"]["attribution"], dict)


def test_to_serializable_still_terminates_real_cycles():
    payload = {}
    payload["cycle"] = payload

    serialized = to_serializable(payload)

    assert isinstance(serialized["cycle"], str)
