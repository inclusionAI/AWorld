"""
服务模块

提供各种业务服务
"""

__all__ = [
    "AftsService",
]


def __getattr__(name: str):
    if name == "AftsService":
        from .afts_service import AftsService

        return AftsService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
