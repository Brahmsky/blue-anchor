# pyright: reportMissingImports=false

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minirag.utils import limit_async_func_call


def test_limit_async_func_call_releases_slot_after_exception():
    calls = {"count": 0}

    async def flaky(value: str):
        calls["count"] += 1
        if value == "boom":
            raise RuntimeError("boom")
        return value

    wrapped = limit_async_func_call(1)(flaky)

    async def scenario():
        try:
            await wrapped("boom")
        except RuntimeError:
            pass

        return await asyncio.wait_for(wrapped("ok"), timeout=1)

    result = asyncio.run(scenario())

    assert result == "ok"
    assert calls["count"] == 2
