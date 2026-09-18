"""Inspect the interpreter's installed API; imports occur only in this process."""

import importlib
import asyncio
import importlib.metadata
import inspect
import itertools
import json
import math
import os
from pathlib import Path
import sys
import traceback


def shape(value, depth=0, budget=None):
    budget = [128] if budget is None else budget
    result = {"type": type(value).__module__ + "." + type(value).__qualname__}
    budget[0] -= 1
    if budget[0] < 0:
        return {**result, "truncated": True}
    if value is None or isinstance(value, (bool, int, str, float)):
        result["value"] = (
            value
            if not isinstance(value, float) or math.isfinite(value)
            else repr(value)
        )
        if isinstance(value, str) and len(value) > 512:
            result["value"] = value[:512]
            result["truncated"] = True
    elif isinstance(value, dict):
        result["length"] = len(value)
        if depth < 3:
            result["fields"] = {
                str(key)[:256]: shape(item, depth + 1, budget)
                for key, item in itertools.islice(value.items(), 32)
            }
        result["truncated"] = len(value) > 32
    elif isinstance(value, (list, tuple)):
        result["length"] = len(value)
        if depth < 3:
            result["items"] = [shape(item, depth + 1, budget) for item in value[:16]]
        result["truncated"] = len(value) > 16
    else:
        dimensions = getattr(value, "shape", None)
        if isinstance(dimensions, tuple) and all(
            isinstance(i, int) for i in dimensions
        ):
            result["shape"] = list(dimensions)
        result["repr"] = repr(value)[:1024]
    return result


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    result_file = Path(sys.argv[2])
    receipt = {
        "success": False,
        "interpreter": {
            "executable": sys.executable,
            "version": sys.version,
            "prefix": sys.prefix,
        },
        "module": request["module"],
        "object_path": request.get("object_path"),
    }
    try:
        # Use the task working directory just as a task-local Python invocation
        # would, while all probe helpers themselves came from trusted paths.
        sys.path.insert(0, os.getcwd())
        module = importlib.import_module(request["module"])
        target = module
        for component in (request.get("object_path") or "").split("."):
            if component:
                target = getattr(target, component)
        version = getattr(module, "__version__", None)
        distributions = {}
        package_distributions = getattr(
            importlib.metadata, "packages_distributions", lambda: {}
        )()
        for name in package_distributions.get(request["module"].split(".")[0], [])[:16]:
            distributions[name] = importlib.metadata.version(name)
        receipt.update(
            module_file=getattr(module, "__file__", None),
            module_version=str(version) if version is not None else None,
            distributions=distributions,
            target_type=type(target).__module__ + "." + type(target).__qualname__,
        )
        try:
            receipt["signature"] = str(inspect.signature(target))[:8192]
        except (TypeError, ValueError) as error:
            receipt["signature"] = None
            receipt["signature_unavailable"] = str(error)[:1024]
        receipt["doc"] = (inspect.getdoc(target) or "")[: request["doc_chars"]]
        if request.get("call") is not None:
            call = request["call"]
            value = target(*call["args"], **call["kwargs"])
            receipt["call_awaited"] = inspect.isawaitable(value)
            if receipt["call_awaited"]:

                async def resolve():
                    return await value

                value = asyncio.run(resolve())
            receipt["call_result"] = shape(value)
            if call.get("result", "structure") == "json":
                encoded = json.dumps(value, allow_nan=False)
                if len(encoded.encode()) > request["result_bytes"]:
                    receipt["json_result_truncated"] = True
                else:
                    receipt["json_result"] = value
        receipt["success"] = True
    except BaseException as error:
        receipt.update(
            error_type=type(error).__name__,
            error=str(error)[:4096],
            traceback=traceback.format_exc()[-16384:],
        )
    result_file.write_text(json.dumps(receipt, allow_nan=False))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
