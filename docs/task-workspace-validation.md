# Executed artifact validation and API probes

`aworld.core.task_workspace.validation.validate_candidate` is the shared async
validator for the trusted task session and candidate store. It receives
`candidate_files: Mapping[str, Path]`, `inputs: Mapping[str, Path]`, a nonempty
list of check definitions, and caller-bound `scope`, `working_dir`, optional
`ValidationLimits`, and `env` mapping. File keys are opaque logical names: an absolute public output
path is a valid key, but a check cannot invent another path outside the supplied
mapping. The store must invoke this function itself and register/sign the result;
a receipt supplied by the model is not trusted execution evidence.

`describe_validation()` returns a compact machine-readable check catalog and
`CHECK_SCHEMA` for the workbench inspect operation. Each check has a unique `id`,
`kind`, and (except a command) a `path` key. Public source quotations/spans or an
explicit caller's provenance can be included in `source`; they are part of the
definition hash. Mandatory public checks and optional agent self-checks are
separated by the calling session, not overwritten by this library.

```python
checks = [
    {"id": "shape", "kind": "csv", "path": "result.csv",
     "required_columns": ["id", "original", "p", "q"],
     "rows_equal": 2, "primary_key": ["id"]},
    {"id": "probability", "kind": "numeric", "path": "result.csv",
     "columns": ["p", "q"], "min_value": 0, "max_value": 1,
     "sum_equals": 1, "abs_tolerance": 1e-8},
    {"id": "preserved", "kind": "preserve", "path": "result.csv",
     "input": "source.csv", "columns": ["original"], "primary_key": ["id"]},
    {"id": "reference", "kind": "compare", "path": "result.csv",
     "input": "reference.csv", "columns": ["p", "q"],
     "primary_key": ["id"], "abs_tolerance": 1e-6, "rel_tolerance": 1e-5},
]
receipt = await validate_candidate(candidate_files, inputs, checks,
                                   scope=scope_id, working_dir=stage)
```

CSV uses an explicit header and rejects duplicate columns, nonrectangular records
and malformed quoting. Empty CSV/JSON structures are not automatically declared
incorrect by parsing alone; explicit row/nonempty constraints or numeric checks
make that decision. JSON rejects duplicate object keys and NaN/Infinity literals.
`format="json"` supports records, a scalar vector (`columns=["value"]`) or a
matrix (`columns=["0", "1", ...]`), with an optional `records_pointer`.
References can specify their own `input_format` and `input_records_pointer`.

`numeric` requires finite numeric values and checks optional min/max bounds and
the sum of selected columns independently for every row. `compare` reads input
bytes separately and applies `abs(actual-reference) <= abs_tolerance +
rel_tolerance * abs(reference)`. `preserve` compares fields exactly. Rows align
by the explicit primary key, or position when no key is declared; duplicates and
missing references fail. Extra output rows fail unless `allow_extra_rows=true`
was declared. Keys preserve JSON types by default; `key_mode="string"` is an
explicit cross-format alternative. A normalization test is not a substitute for
an independent reference comparison.

File checks cover `exists`, `regular_file`, `nonempty`, `file_size`, `sha256`,
and decodable `text`. Python source syntax uses the validator's Python parser
without executing the program; JSON syntax is also supported. The library does
not claim another language or Python version was compiled. Use an explicitly
selected interpreter/argv probe to inspect other installed runtimes.

## Receipts and measured objectives

The receipt schema is `aworld.validation/v1`. Each result has `id`, `kind`,
`path`, `status` (`passed`, `failed`, `error`, or `unknown`), `success`, measured
`metrics`, `evidence`, and `definition_sha256`. Only `passed` sets success. The
top-level `metrics` flatten values using `<check.id>.<metric>`:

| Check | Main metric suffixes |
|---|---|
| File | `size_bytes`, `character_count` |
| Table | `row_count`, `column_count`, `duplicate_key_count` |
| Numeric | `finite_count`, `invalid_value_count`, `min_value`, `max_value`, `max_sum_error` |
| Compare | `compared_count`, `max_abs_error`, `max_rel_error`, `mismatch_count` |
| Preserve/reference alignment | `matched_rows`, `missing_keys`, `extra_keys`, `changed_values` |
| Calibrated command | Keys actually emitted in its valid structured metrics report |

For a zero reference, relative error is not defined; absolute error and the
absolute tolerance still decide the comparison. `max_rel_error` summarizes only
nonzero references. Nonfinite metrics are never emitted. These metrics can feed
the store's declared objective, but the receipt does not determine that objective
or task reward.

`bindings` contains `artifacts` and `inputs`, mapping every logical key to
`{state: "regular", size, sha256}`, plus `check_definitions_sha256`. Missing,
nonregular, symlink or unreadable files cannot produce a successful receipt.
Fingerprints always stream the complete regular file in bounded chunks. Metadata,
hash and command checks do not retain file contents, so a valid large binary can
pass existence/nonempty/size/hash checks without fitting in the parser cache.
`max_file_bytes` and `max_total_bytes` bound only the bytes retained for content
parsing; a JSON/CSV/text/numeric/source check exceeding them reports an explicit
content parsing limit while preserving the real file's regular/size/hash binding.
Explicit `file_size.max_bytes` remains a separate caller-specified constraint.
Every supplied file is re-read after execution; mutation invalidates
the receipt and removes top-level metrics. Hashes describe actual bytes rather
than a filename, model claim or stale earlier candidate. Check definitions use
sorted-key compact UTF-8 JSON with nonfinite numbers forbidden. Scope, hashes,
policy and these measured results are available for the store's own receipt MAC.

## Command self-check quality

The new `kind="command"` is independent of existing caller validation command
contracts. It runs an argv list without shell interpolation, retains its real
return code, bounded stdout/stderr, full stream SHA-256 and byte counts, and
fingerprints its executable, file arguments and explicit `checker_files`.
Imported checker helpers should be listed in `checker_files`; an arbitrary
language's import closure is not inferred.

Command environment is the minimum OS execution defaults plus the trusted `env`
argument and any per-check declared `env` overrides. A session can inject its
bound task environment with `partial(validate_candidate, env=task_env)`; do not
forward the full Runtime/AWorld process environment. This preserves legitimate
task PYTHONPATH/LD_LIBRARY_PATH without exposing unrelated host credentials.
`execution_environment_sha256` binds the explicit base `env` mapping (or `{}`),
using the same canonical JSON hash as check definitions. The selection policy
should bind this hash so a changed task environment invalidates an older receipt.
Each process also records `environment_sha256` for its actual merged environment.

Use `{artifact:logical-key}` and `{input:logical-key}` placeholders in argv.
The checker must emit one complete JSON document on stdout:

```json
{"schema_version":"aworld.check-report/v1",
 "checks":[{"id":"actual-assertion","passed":true}],
 "metrics":{"measured_error":0.002}}
```

Exit 0, an empty assertion list, malformed/truncated output, or a caller-provided
`success`/`metrics` field is not accepted as semantic evidence. A successful
baseline also needs declared negative controls, each mapping an artifact to a
deliberately wrong input fixture:

```json
{"negative_controls":[
  {"replacements":{"result.csv":"wrong-result-fixture.csv"},
   "expected_return_codes":[0,1]}]}
```

The same checker must report an actual failed assertion on each negative fixture,
with an expected ordinary return code. A crash or timeout alone is not detection.
An always-pass checker therefore remains `unknown` and publishes no trusted
metrics. Successful calibration covers these declared controls; it is not proof
of arbitrary test correctness or a substitute for the external verifier/reward.

## Installed API probes

`probe_python(interpreter, module, object_path=None, *, working_dir=None,
env=None, call=None, limits=None)` executes the selected task interpreter in a
fresh process. It returns the actual interpreter/version/prefix, installed module
path/version/distributions, inspectable signature and bounded documentation.
An optional minimal call requires explicit JSON `args` and `kwargs` and can
request a structural result or a bounded JSON result. No expression is evaluated
to select the object: module/object names must be dotted identifiers. Import or
call failures retain their real stack, error type and process return code.
Awaitable return values are awaited inside the child process under the same
operation bounds, so an async function body and its errors are actually observed.

`probe_argv` supports other installed languages/tools. Process success is a
diagnostic fact; `task_correctness` remains `not_assessed`. `describe_probes()`
provides the native tool's request and limit documentation.

Default cwd is a private temporary directory; an explicit caller-bound cwd is
used when task-local imports/files are needed. The child receives minimal OS
execution environment plus explicitly supplied task values, not the Runtime's
entire environment. Pass task PYTHONPATH/LD_LIBRARY_PATH explicitly when relevant.
Import side effects affect the child process state, not the parent's Python
modules/environment. Files and network available to the child can still be
affected: these probes are not a sandbox against arbitrary task code.

Each operation has configurable wall, output, CPU, file-size and file-descriptor
bounds. Linux additionally enforces address-space limits; resource capabilities
are reported instead of claiming unsupported macOS/Windows limits. Cancellation
propagates and terminates the owned process group, including ordinary spawned
children. Process groups do not contain a deliberately escaping daemon/session.
There is no task-wide time or token limit.
