import anyio
import pytest

from examples.gaia.mcp_collections.base import ActionArguments, ActionCollection

try:
    _EXCEPTION_GROUP_TYPE = ExceptionGroup
except NameError:  # pragma: no cover - exercised on Python < 3.11
    from exceptiongroup import ExceptionGroup as _EXCEPTION_GROUP_TYPE


class _DummyCollection(ActionCollection):
    pass


class _RaisingServer:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def run(self, transport: str) -> None:
        raise self.error


def _collection_with_server(error: BaseException) -> ActionCollection:
    collection = _DummyCollection(ActionArguments(name="dummy", workspace=".", unittest=False))
    collection.server = _RaisingServer(error)
    return collection


def test_run_suppresses_closed_resource_error_from_stdio_disconnect() -> None:
    error = _EXCEPTION_GROUP_TYPE(
        "unhandled errors in a TaskGroup",
        [
            _EXCEPTION_GROUP_TYPE(
                "unhandled errors in a TaskGroup",
                [anyio.ClosedResourceError()],
            )
        ],
    )

    _collection_with_server(error).run()


def test_run_reraises_unexpected_server_errors() -> None:
    error = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _collection_with_server(error).run()
