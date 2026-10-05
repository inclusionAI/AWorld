# Runtime compatibility packaging

The Runtime integration intentionally publishes the full legacy AWorld tree as
`aworld` and the full CLI as `aworld-cli`. These artifacts are independent of
the minimal, synchronized `1.x` distributions declared by the repository-root
and `aworld-cli/pyproject.toml` files.

For a clean source archive, remove the repository-root `pyproject.toml` so the
core build selects `setup.py`, then copy `aworld-cli.pyproject.toml` over
`aworld-cli/pyproject.toml` before building the two wheels. The core version is
owned by `aworld/version_gen.py`; this compatibility CLI manifest owns the CLI
version and must pin the exact core version. Packaging tests enforce that the
two sources remain mutually consistent.
