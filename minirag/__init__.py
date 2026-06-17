"""MiniRAG package."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import (
        BaseGraphStorage,
        BaseKVStorage,
        BaseVectorStorage,
        QueryParam,
        TextChunkSchema,
    )
    from .minirag import MiniRAG
    from .utils import logger

sys.modules.setdefault("minirag", sys.modules[__name__])

__all__ = [
    "MiniRAG",
    "BaseGraphStorage",
    "BaseKVStorage",
    "BaseVectorStorage",
    "TextChunkSchema",
    "QueryParam",
    "logger",
]


def __getattr__(name: str):
    if name in {
        "BaseGraphStorage",
        "BaseKVStorage",
        "BaseVectorStorage",
        "QueryParam",
        "TextChunkSchema",
    }:
        from .base import (
            BaseGraphStorage,
            BaseKVStorage,
            BaseVectorStorage,
            QueryParam,
            TextChunkSchema,
        )

        exports = {
            "BaseGraphStorage": BaseGraphStorage,
            "BaseKVStorage": BaseKVStorage,
            "BaseVectorStorage": BaseVectorStorage,
            "QueryParam": QueryParam,
            "TextChunkSchema": TextChunkSchema,
        }
        value = exports[name]
        globals()[name] = value
        return value

    if name == "MiniRAG":
        from .minirag import MiniRAG

        globals()[name] = MiniRAG
        return MiniRAG

    if name == "logger":
        from .utils import logger

        globals()[name] = logger
        return logger

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
