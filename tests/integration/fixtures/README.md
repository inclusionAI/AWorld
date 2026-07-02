# ACP Stdio Host Validation Fixtures

These files are validation-layer assets for exercising the generic ACP stdio host contract.

Templates:

- `acp_stdio_host_contract.template.json`
  - generic baseline config
- `acp_stdio_host_contract.same_host.template.json`
  - same-host smoke starting point
- `acp_stdio_host_contract.distributed.template.json`
  - distributed worker-host smoke starting point

Usage:

1. Render a template from CLI, or copy the matching template file.
2. Export the workspace variables referenced by the template.
   - same-host: `AWORLD_WORKSPACE`
   - distributed: `AWORLD_WORKER_WORKSPACE`
3. Replace `command` only if the worker-host launch command differs from the default template. A checked-in config file may also set `"topology": "same-host"` or `"distributed"` and override only the fields that differ from the built-in template.
4. Keep `profile` aligned with `aworld-cli acp describe-validation`.
5. Optionally add `startupTimeoutSeconds` / `startupRetries` when the target host needs a wider startup window or retry-safe bring-up validation.
6. Run:

Render example:

```bash
export AWORLD_WORKSPACE=/path/to/aworld
python -m aworld_cli.main --no-banner acp render-validation-config \
  --topology same-host \
  --expand-placeholders \
  --env AWORLD_WORKSPACE=$AWORLD_WORKSPACE \
  --output-file /tmp/acp-same-host.json
```

Schema discovery:

```bash
python -m aworld_cli.main --no-banner acp describe-validation
```

Use `configFileFields`, `configAllowedFields`, `topologies`, and `configSchemaPath` from that payload as the source of truth for config authoring. `configSchemaPath` points at the checked-in JSON Schema file for offline validation and editor integration. The schema accepts either a full explicit config or a topology-driven partial config whose required base fields come from the selected built-in template.

Direct topology smoke without a config file:

```bash
export AWORLD_WORKSPACE=/path/to/aworld
AWORLD_ACP_VALIDATION_TOPOLOGY=same-host \
python -m pytest tests/integration/test_acp_stdio_host_contract.py -q
```

This path reuses the built-in topology templates through `validate-stdio-host --topology ...`.

```bash
export AWORLD_WORKSPACE=/path/to/aworld
AWORLD_ACP_VALIDATION_CONFIG_FILE=/path/to/config.json \
python -m pytest tests/integration/test_acp_stdio_host_contract.py -q
```

Or invoke the same contract directly:

```bash
python -m aworld_cli.main --no-banner acp validate-stdio-host --config-file /path/to/config.json
```
