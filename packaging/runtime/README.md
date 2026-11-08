# Runtime compatibility packaging

The Runtime integration intentionally publishes the full legacy AWorld tree as
`aworld` and the full CLI as `aworld-cli`. These artifacts are independent of
the minimal, synchronized `1.x` distributions declared by the repository-root
and `aworld-cli/pyproject.toml` files.

Build from a clean source archive with the checked-in wrapper. It stages the
legacy `setup.py` core and compatibility CLI manifest without modifying the
source, requires one explicit `SOURCE_DATE_EPOCH`, and rejects wheels whose ZIP
entries were not normalized to that epoch:

```sh
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" \
  python3.12 scripts/build_runtime_compatibility_wheels.py \
  --python /path/to/python3.12 \
  --build-constraints /path/to/build-constraints.txt \
  --outdir /path/to/wheels
```

For release verification, run the command twice into separate empty output
directories with the same epoch and compare the two SHA-256 values. Delaying
the second build is intentional; the wrapper also validates every ZIP entry,
so two builds performed inside ZIP's two-second timestamp window cannot create
a false reproducibility result.

The core version is owned by `aworld/version_gen.py`; the compatibility CLI
manifest owns the CLI version and must pin the exact core version. Packaging
tests enforce that the two sources remain mutually consistent.
