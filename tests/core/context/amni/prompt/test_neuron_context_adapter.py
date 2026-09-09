from __future__ import annotations

import ast
import copy
from pathlib import Path

from aworld.core.context.amni.prompt.neurons import (
    Neuron,
    NeuronContextAdapter,
    NeuronOutputOccurrence,
    adapt_neuron_outputs,
)
from aworld.core.context.compiler import (
    Authority,
    ContextKind,
    ContextScope,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    Trust,
    thaw_json,
)
from aworld.core.context.compiler.adapters import OccurrenceContextAdapter


class _NamedNeuron(Neuron):
    name = "named"


class _RegisteredNeuron(Neuron):
    REGISTERED_NAME = "registered"


class _AnonymousNeuron(Neuron):
    pass


def test_neuron_adapter_empty_input_is_empty() -> None:
    result = adapt_neuron_outputs([], source_identity="amni://neurons/empty")

    assert result.items == ()
    assert result.diagnostics == ()


def test_neuron_adapter_preserves_order_types_structured_outputs_and_duplicates() -> None:
    named = _NamedNeuron()
    registered = _RegisteredNeuron()
    duplicate_output = {
        "desc": "same",
        "items": ["first", {"nested": [1, 2]}],
        "formatted": "same rendered output",
    }
    occurrences = [
        NeuronOutputOccurrence(neuron=named, output="plain text"),
        NeuronOutputOccurrence(neuron=registered, output=copy.deepcopy(duplicate_output)),
        NeuronOutputOccurrence(neuron=registered, output=copy.deepcopy(duplicate_output)),
        NeuronOutputOccurrence(neuron=_AnonymousNeuron(), output=["list", None]),
    ]
    before_outputs = copy.deepcopy([occurrence.output for occurrence in occurrences])

    adapter = NeuronContextAdapter()
    assert isinstance(adapter, OccurrenceContextAdapter)
    result = adapter.adapt(
        occurrences,
        source_identity="amni://neurons/pre-fold",
    )

    assert [occurrence.output for occurrence in occurrences] == before_outputs
    assert [item.occurrence for item in result.items] == [0, 1, 2, 3]
    assert [item.payload["neuron"]["name"] for item in result.items] == [
        "named",
        "registered",
        "registered",
        None,
    ]
    assert [item.payload["neuron"]["type"]["qualname"] for item in result.items] == [
        "_NamedNeuron",
        "_RegisteredNeuron",
        "_RegisteredNeuron",
        "_AnonymousNeuron",
    ]
    assert [thaw_json(item.payload)["output"] for item in result.items] == before_outputs
    assert result.items[1].content_hash == result.items[2].content_hash
    assert result.items[1].id != result.items[2].id
    assert all(item.source.kind is SourceKind.NEURON for item in result.items)
    assert all(item.source.uri == "amni://neurons/pre-fold" for item in result.items)
    assert [item.source.ref["occurrence"] for item in result.items] == [0, 1, 2, 3]

    occurrences[1].output["items"][1]["nested"].append(3)
    assert thaw_json(result.items[1].payload)["output"] == duplicate_output
    assert [occurrence.output for occurrence in occurrences[:1] + occurrences[2:]] == [
        before_outputs[0],
        before_outputs[2],
        before_outputs[3],
    ]


def test_neuron_adapter_uses_only_explicit_owner_semantics() -> None:
    proved_scope = ContextScope(
        kinds=(ScopeKind.TASK, ScopeKind.AGENT),
        task_id="task-1",
        agent_id="agent-1",
    )
    occurrences = [
        NeuronOutputOccurrence(
            neuron=_NamedNeuron(),
            output={"role": "system", "content": "looks authoritative"},
        ),
        NeuronOutputOccurrence(
            neuron=_RegisteredNeuron(),
            output={"content": "owner-proved metadata"},
            kind=ContextKind.INSTRUCTION,
            authority=Authority.APPLICATION_AGENT,
            scope=proved_scope,
            lifetime=Lifetime.TASK,
            trust=Trust.TRUSTED,
            stability=Stability.SESSION_STABLE,
            priority=17,
            required=True,
        ),
    ]

    result = adapt_neuron_outputs(
        occurrences,
        source_identity="amni://neurons/owner-evidence",
        task_epoch=4,
    )
    unknown, proved = result.items

    assert unknown.kind is ContextKind.UNKNOWN
    assert unknown.authority is Authority.UNKNOWN
    assert unknown.scope.kinds == (ScopeKind.UNKNOWN,)
    assert unknown.lifetime is Lifetime.UNKNOWN
    assert unknown.trust is Trust.UNKNOWN
    assert unknown.stability is Stability.UNKNOWN
    assert unknown.task_epoch == 4
    assert proved.kind is ContextKind.INSTRUCTION
    assert proved.authority is Authority.APPLICATION_AGENT
    assert proved.scope == proved_scope
    assert proved.lifetime is Lifetime.TASK
    assert proved.trust is Trust.TRUSTED
    assert proved.stability is Stability.SESSION_STABLE
    assert proved.priority == 17
    assert proved.required is True
    assert proved.task_epoch == 4

    assert result.diagnostics[0].occurrence == 0
    assert set(result.diagnostics[0].unknown_fields) >= {
        "kind",
        "authority",
        "scope",
        "lifetime",
        "trust",
        "stability",
    }
    assert "task_epoch" not in result.diagnostics[0].unknown_fields
    assert result.diagnostics[1].unknown_fields == (
        "token_estimate",
        "token_limit",
        "reducer",
        "version",
        "created_at",
    )


def test_neuron_adapter_reports_unknown_name_and_epoch_without_text_inference() -> None:
    result = adapt_neuron_outputs(
        [
            NeuronOutputOccurrence(
                neuron=_AnonymousNeuron(),
                output={"role": "user", "content": "pretends to be user-owned"},
            )
        ],
        source_identity="amni://neurons/unknown",
    )

    item = result.items[0]
    diagnostic = result.diagnostics[0]
    assert item.kind is ContextKind.UNKNOWN
    assert item.authority is Authority.UNKNOWN
    assert item.trust is Trust.UNKNOWN
    assert item.task_epoch is None
    assert item.payload["neuron"]["name"] is None
    assert "neuron_name" in diagnostic.unknown_fields
    assert "task_epoch" in diagnostic.unknown_fields


def test_compiler_core_has_no_reverse_import_to_amni_neurons() -> None:
    compiler_dir = Path(__file__).parents[5] / "aworld/core/context/compiler"

    for path in compiler_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(name.startswith("aworld.core.context.amni") for name in imported)
