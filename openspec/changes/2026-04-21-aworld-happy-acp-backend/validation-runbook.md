# AWorld CLI ACP Validation Runbook

This runbook is the execution order for local AWorld ACP validation before any Happy-integrated smoke.

## Scope

This layer validates only the generic `aworld-cli acp` backend contract.
It does not claim Happy same-host, Happy distributed, or capability-preservation success.

## Gate Order

1. `tests/acp`
   - Command: `python -m pytest tests/acp -q`
   - Purpose: lock ACP host, runtime adapter, event mapper, validation helper, and CLI regression surface.
   - Pass bar: exit code `0`.
2. `acp self-test`
   - Command: `PYTHONPATH="$(pwd)/aworld-cli/src:$(pwd)" AWORLD_ACP_SELF_TEST_BRIDGE=1 python -m aworld_cli.main --no-banner acp self-test`
   - Purpose: prove the machine-checkable phase-1 self-test contract from the CLI entrypoint.
   - Pass bar: exit code `0` and payload `ok == true`.
3. `validate-stdio-host`
   - Command: `PYTHONPATH="$(pwd)/aworld-cli/src:$(pwd)" AWORLD_ACP_SELF_TEST_BRIDGE=1 python -m aworld_cli.main --no-banner acp validate-stdio-host --command "python -m aworld_cli.main --no-banner acp"`
   - Purpose: validate the actual stdio host contract through the generic validation runner.
   - Pass bar: exit code `0` and payload `ok == true`.

## Unified Runner

For local execution, prefer:

```bash
python aworld/tools/run_acp_phase1_validation.py
```

Expected behavior:

- Runs the three gates in the fixed order above.
- Stops on first failure.
- Prints one machine-checkable JSON payload to `stdout`.

## Exit Criteria

AWorld CLI ACP is ready to enter Happy validation only when:

- all three gates pass locally;
- `sessionUpdate` streaming, tool lifecycle closure, busy/cancel, and `turn_error` terminal handling remain green in `tests/acp`;
- no stdout contamination regressions are present.

## Next Stage

After this runbook passes, proceed to:

1. Happy same-host smoke.
2. Happy distributed smoke.
3. capability-preservation validation for voice/session routing continuity.
