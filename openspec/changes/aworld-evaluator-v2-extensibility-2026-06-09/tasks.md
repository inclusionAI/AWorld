## 1. Execution Extensibility

- [x] 1.0 Add a lightweight `EvalHarnessDef` boundary and compatibility lowering from direct `suite.execution`.
- [x] 1.1 Add a `PROGRAM` execution mode to the framework-owned evaluation execution model.
- [x] 1.2 Introduce an internal execution adapter boundary under `aworld/evaluations/` for static, agent, task, and program-backed execution.
- [x] 1.3 Keep existing `static`, `agent`, and `task` suite-backed flows working through compatibility paths.
- [x] 1.4 Normalize program-backed execution results into the same `EvalState` shape used by current execution-backed evaluation.
- [x] 1.5 Validate `PROGRAM` specs up front, including required importable `target_ref` and unsupported command/workflow forms.
- [x] 1.6 Keep importable callable execution as a trusted in-memory framework contract and reject executable refs in declared JSON manifests.

## 2. Typed Judge Contracts

- [x] 2.1 Add typed judge-output model support as the primary suite-backed validation contract.
- [x] 2.2 Preserve compatibility for current required-field-based judge schema definitions during migration.
- [x] 2.3 Expose judge-model-derived schema metadata once at the report level for docs or downstream tooling.

## 3. Composite Gate Policies

- [x] 3.1 Expand gate definitions from single-threshold checks to structured composite metric conditions.
- [x] 3.2 Support `pass`, `fail`, and `needs_approval` outcomes from composite gate evaluation.
- [x] 3.3 Keep current threshold-style gate definitions valid as compatibility sugar over the richer gate model.
- [x] 3.4 Support `>=`, `<=`, `>`, `<`, `==`, and `!=` gate operators.
- [x] 3.5 Fail structured gates closed when a condition references a missing metric while preserving the completed report payload.

## 4. Trajectory Evaluation

- [x] 4.1 Add suite-declared trajectory scorer definitions that lower into normal evaluator criteria.
- [x] 4.2 Keep existing trajectory scorer/extractor behavior and report metric shapes compatible.
- [x] 4.3 Add coverage for trajectory metrics participating in reports and gate evaluation.

## 5. Verification

- [x] 5.1 Add regression coverage for adapter-backed execution across static, agent, task, and program-backed suites.
- [x] 5.2 Add coverage for typed judge validation success, failure, and legacy compatibility paths.
- [x] 5.3 Add coverage for composite gate evaluation, all supported operators, missing metrics, and legacy threshold compatibility.
- [x] 5.4 Add error-path coverage for program exceptions and malformed program output where applicable.
- [x] 5.5 Validate the OpenSpec change and keep it aligned with the framework-owned evaluator scope.
