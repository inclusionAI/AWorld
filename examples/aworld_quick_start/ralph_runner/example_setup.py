# coding: utf-8
# Copyright (c) 2026 inclusionAI.
from dataclasses import dataclass
from pathlib import Path
import shutil

from aworld.runners.ralph.config import RalphConfig, RalphVerifyConfig
from aworld.runners.ralph.types import CompletionCriteria


EXAMPLE_ROOT_MARKER = ".ralph_runner_example_root"
RULES_TEXT = """# Order Pricing Business Rules

Implement order pricing for an internal sales team.

The pricing function must:

1. Accept a list of item dictionaries with `sku`, `unit_price`, and `quantity`.
2. Reject empty orders with `ValueError`.
3. Compute the subtotal as the sum of `unit_price * quantity`.
4. Apply a tier discount:
   - `standard`: 0%
   - `silver`: 5%
   - `gold`: 10%
5. Apply an additional 5% bulk discount when total quantity is 10 units or more.
6. Return a dictionary with `subtotal`, `discount`, and `total`, rounded to 2 decimals.
"""
MODULE_TEXT = """def calculate_quote(items, customer_tier):
    raise NotImplementedError("Implement me")
"""
TEST_TEXT = """import pytest

from src.order_pricing import calculate_quote


def test_calculate_quote_applies_tier_discount():
    quote = calculate_quote(
        [
            {"sku": "A-100", "unit_price": 20.0, "quantity": 2},
            {"sku": "B-200", "unit_price": 15.0, "quantity": 1},
        ],
        customer_tier="silver",
    )

    assert quote == {
        "subtotal": 55.0,
        "discount": 2.75,
        "total": 52.25,
    }


def test_calculate_quote_stacks_bulk_discount_with_tier_discount():
    quote = calculate_quote(
        [
            {"sku": "A-100", "unit_price": 10.0, "quantity": 5},
            {"sku": "B-200", "unit_price": 5.0, "quantity": 5},
        ],
        customer_tier="gold",
    )

    assert quote == {
        "subtotal": 75.0,
        "discount": 11.25,
        "total": 63.75,
    }


def test_calculate_quote_rejects_empty_orders():
    with pytest.raises(ValueError, match="at least one item"):
        calculate_quote([], customer_tier="standard")
"""


def _validate_example_path_invariants(paths: "RalphExamplePaths") -> None:
    root = paths.root.resolve()
    src_dir = paths.src_dir.resolve()
    tests_dir = paths.tests_dir.resolve()
    rules_file = paths.rules_file.resolve()
    module_file = paths.module_file.resolve()
    test_file = paths.test_file.resolve()
    marker_file = paths.marker_file.resolve()

    if src_dir != root / "src":
        raise ValueError("src_dir must equal root/src")
    if tests_dir != root / "tests":
        raise ValueError("tests_dir must equal root/tests")
    if rules_file != root / "business_rules.md":
        raise ValueError("rules_file must equal root/business_rules.md")
    if module_file != src_dir / "order_pricing.py":
        raise ValueError("module_file must equal src_dir/order_pricing.py")
    if test_file != tests_dir / "test_order_pricing.py":
        raise ValueError("test_file must equal tests_dir/test_order_pricing.py")
    if marker_file != root / EXAMPLE_ROOT_MARKER:
        raise ValueError("marker_file must equal root/.ralph_runner_example_root")


@dataclass(frozen=True)
class RalphExamplePaths:
    root: Path
    src_dir: Path
    tests_dir: Path
    rules_file: Path
    module_file: Path
    test_file: Path
    marker_file: Path

    @classmethod
    def from_root(cls, root: Path | str) -> "RalphExamplePaths":
        root_path = Path(root).expanduser().resolve()
        return cls(
            root=root_path,
            src_dir=root_path / "src",
            tests_dir=root_path / "tests",
            rules_file=root_path / "business_rules.md",
            module_file=root_path / "src" / "order_pricing.py",
            test_file=root_path / "tests" / "test_order_pricing.py",
            marker_file=root_path / EXAMPLE_ROOT_MARKER,
        )


def ensure_ralph_runner_example_workspace(paths: RalphExamplePaths, reset: bool = False) -> None:
    _validate_example_path_invariants(paths)

    if reset and paths.root.exists():
        if not paths.marker_file.is_file():
            raise ValueError("reset=True requires an initialized example root marker")
        shutil.rmtree(paths.root)

    paths.root.mkdir(parents=True, exist_ok=True)
    paths.src_dir.mkdir(parents=True, exist_ok=True)
    paths.tests_dir.mkdir(parents=True, exist_ok=True)

    paths.marker_file.write_text("aworld-ralph-runner-example\n", encoding="utf-8")
    (paths.src_dir / "__init__.py").write_text("", encoding="utf-8")
    paths.rules_file.write_text(RULES_TEXT, encoding="utf-8")
    paths.module_file.write_text(MODULE_TEXT, encoding="utf-8")
    paths.test_file.write_text(TEST_TEXT, encoding="utf-8")


def build_ralph_runner_example_config(workspace: str, model_config=None) -> RalphConfig:
    config = RalphConfig.create(model_config=model_config)
    config.workspace = workspace
    config.execution_mode = "fresh_context"
    config.verify = RalphVerifyConfig(
        enabled=True,
        commands=["PYTHONPATH=. pytest -q"],
        run_on_each_iteration=True,
        run_before_completion=True,
    )
    return config


def build_ralph_runner_example_criteria(task_id: str | None = None) -> CompletionCriteria:
    criteria = CompletionCriteria(max_iterations=5)

    if not task_id:
        return criteria

    async def stop_when_latest_verify_passes(state) -> bool:
        latest_completed_iteration = state.loop_state.iteration - 1
        if latest_completed_iteration <= 0:
            return False

        verify_result = await state.memory.read_verify_result(task_id, latest_completed_iteration)
        return bool(verify_result and verify_result.get("passed"))

    criteria.custom_stop = stop_when_latest_verify_passes
    return criteria
