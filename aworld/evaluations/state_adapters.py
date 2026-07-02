# coding: utf-8
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from aworld.evaluations.runtime_composition import RolloutState
from aworld.evaluations.sources import EvalSourceRecord


class EvalStateAdapter(Protocol):
    def adapt(self, *, record: EvalSourceRecord, case: Any, target: Mapping[str, Any]) -> RolloutState:
        ...


@dataclass(frozen=True)
class AnswerStateAdapter:
    def adapt(self, *, record: EvalSourceRecord, case: Any, target: Mapping[str, Any]) -> RolloutState:
        return RolloutState(
            case_id=str(getattr(case, "case_id", record.case_id)),
            status="success",
            answer=record.answer,
            outcome={"has_answer": record.answer is not None},
            metadata={
                **dict(record.metadata or {}),
                "source_case_id": record.case_id,
            },
        )


@dataclass(frozen=True)
class TrajectoryLogStateAdapter:
    extraction_dir: str | Path | None = None

    def adapt(self, *, record: EvalSourceRecord, case: Any, target: Mapping[str, Any]) -> RolloutState:
        extracted = dict(record.raw_payload or {})
        final_answer = extracted.get("final_answer") or ""
        steps = list(extracted.get("steps") or [])
        is_finished = any(bool(step.get("is_agent_finished")) for step in steps if isinstance(step, Mapping))
        tool_calls = [
            dict(tool_call)
            for step in steps
            if isinstance(step, Mapping)
            for tool_call in step.get("tool_calls", [])
            if isinstance(tool_call, Mapping)
        ]
        usage = {"total_tokens": 0}
        timing = {"duration_ms": 0}
        standard_metrics = {
            "n_turns": len(steps),
            "n_tool_calls": len(tool_calls),
            "n_tokens": usage["total_tokens"],
            "duration_ms": timing["duration_ms"],
        }
        extracted_path = self._write_extracted(record, extracted)
        metadata = {
            **dict(record.metadata or {}),
            "source_case_id": record.case_id,
        }
        if extracted_path is not None:
            metadata["extracted_path"] = str(extracted_path)
        return RolloutState(
            case_id=str(getattr(case, "case_id", record.case_id)),
            status="success" if is_finished and final_answer else "failed",
            answer=final_answer,
            trajectory=steps,
            tool_calls=tool_calls,
            usage=usage,
            timing=timing,
            standard_metrics=standard_metrics,
            outcome={
                "task_id": record.case_id,
                "question": extracted.get("question"),
                "evidence_blocks": len(extracted.get("evidence") or []),
                "num_steps": extracted.get("num_steps", len(steps)),
                "is_finished": is_finished,
                "final_answer_len": len(final_answer),
                **({"extracted_path": str(extracted_path)} if extracted_path is not None else {}),
            },
            metadata=metadata,
        )

    def _write_extracted(self, record: EvalSourceRecord, extracted: Mapping[str, Any]) -> Path | None:
        extraction_dir = self.extraction_dir or record.metadata.get("extraction_dir")
        if not extraction_dir:
            return None
        out_dir = Path(str(extraction_dir)).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"extracted_{record.case_id}.json"
        path.write_text(json.dumps(dict(extracted), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


@dataclass(frozen=True)
class ReplayRuntimeHarness:
    adapter: EvalStateAdapter
    records: tuple[EvalSourceRecord, ...] = tuple()

    async def run_rollout(self, *, case: Any, target: Mapping[str, Any]) -> RolloutState:
        metadata = getattr(case, "metadata", {}) or {}
        record_payload = metadata.get("source_record")
        if not isinstance(record_payload, Mapping):
            record_payload = (getattr(case, "input", {}) or {}).get("_source_record")
        if not isinstance(record_payload, Mapping):
            raise ValueError("replay source case is missing source_record metadata")
        record = self._resolve_record(record_payload)
        return self.adapter.adapt(record=record, case=case, target=target)

    def _resolve_record(self, record_payload: Mapping[str, Any]) -> EvalSourceRecord:
        case_id = str(record_payload.get("case_id"))
        for record in self.records:
            if record.case_id == case_id:
                return record
        return EvalSourceRecord.from_dict(record_payload)
