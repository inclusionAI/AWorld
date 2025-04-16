# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import inspect
import os
import pkgutil
import re
import sys
import threading
from types import FunctionType
from typing import Callable, Any, Tuple, List, Iterator


def convert_to_snake(name: str) -> str:
    """Class name convert to snake."""
    if '_' not in name:
        name = re.sub(r'([a-z])([A-Z])', r'\1_\2', name)
    return name.lower()


def is_abstract_method(cls, method_name):
    method = getattr(cls, method_name)
    return (hasattr(method, '__isabstractmethod__') and method.__isabstractmethod__) or (
            isinstance(method, FunctionType) and hasattr(
        method, '__abstractmethods__') and method in method.__abstractmethods__)


def _walk_to_root(path: str) -> Iterator[str]:
    """Yield directories starting from the given directory up to the root."""
    if not os.path.exists(path):
        yield ''

    if os.path.isfile(path):
        path = os.path.dirname(path)

    last_dir = None
    current_dir = os.path.abspath(path)
    while last_dir != current_dir:
        yield current_dir
        parent_dir = os.path.abspath(os.path.join(current_dir, os.path.pardir))
        last_dir, current_dir = current_dir, parent_dir


def find_file(filename: str) -> str:
    """Find file from the folders for the given file.

    NOTE: Current running path priority, followed by the execution file path, and finally the aworld package path.

    Args:
        filename: The file name that you want to search.
    """

    def run_dir():
        try:
            main = __import__('__main__', None, None, fromlist=['__file__'])
            return os.path.dirname(main.__file__)
        except ModuleNotFoundError:
            return os.getcwd()

    path = os.getcwd()
    if os.path.exists(os.path.join(path, filename)):
        path = os.getcwd()
    elif os.path.exists(os.path.join(run_dir(), filename)):
        path = run_dir()
    else:
        frame = inspect.currentframe()
        current_file = __file__

        while frame.f_code.co_filename == current_file or not os.path.exists(
                frame.f_code.co_filename
        ):
            assert frame.f_back is not None
            frame = frame.f_back
        frame_filename = frame.f_code.co_filename
        path = os.path.dirname(os.path.abspath(frame_filename))

    for dirname in _walk_to_root(path):
        if not dirname:
            continue
        check_path = os.path.join(dirname, filename)
        if os.path.isfile(check_path):
            return check_path

    return ''


def search_in_module(module: object, base_classes: List[type]) -> List[Tuple[str, type]]:
    """Find all classes that inherit from a specific base class in the module."""
    results = []
    for name, obj in inspect.getmembers(module, inspect.isclass):
        for base_class in base_classes:
            if issubclass(obj, base_class) and obj is not base_class:
                results.append((name, obj))
    return results


def _scan_package(package_name: str, base_classes: List[type], results: List[Tuple[str, type]] = []):
    try:
        package = sys.modules[package_name]
    except:
        return

    try:
        for sub_package, name, is_pkg in pkgutil.walk_packages(package.__path__):
            if is_pkg:
                _scan_package(package_name + "." + name, base_classes, results)
            try:
                module = __import__(f"{package_name}.{name}", fromlist=[name])
                results.extend(search_in_module(module, base_classes))
            except:
                continue
    except:
        pass


def scan_packages(package: str, base_classes: List[type]) -> List[Tuple[str, type]]:
    results = []
    _scan_package(package, base_classes, results)
    return results


class ReturnThread(threading.Thread):
    def __init__(self, func, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.result = None
        super().__init__()

    def run(self):
        self.result = asyncio.run(self.func(*self.args, **self.kwargs))


def asyncio_loop():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    return loop


def sync_exec(async_func: Callable[..., Any], *args, **kwargs):
    """Async function to sync execution."""
    if not asyncio.iscoroutinefunction(async_func):
        return async_func(*args, **kwargs)

    loop = asyncio_loop()
    if loop and loop.is_running():
        thread = ReturnThread(async_func, *args, **kwargs)
        thread.setDaemon(True)
        thread.start()
        thread.join()
        result = thread.result

    else:
        result = asyncio.run(async_func(*args, **kwargs))
    return result
