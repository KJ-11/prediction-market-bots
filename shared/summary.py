"""Midnight-CST summary loop.

The shared loop handles the sleep-until-midnight timing. Each bot supplies
a `summary_fn` that does its own balance fetch, alert dispatch, and stats
reset — those steps differ per bot and don't belong in shared code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CST = ZoneInfo("America/Chicago")


async def midnight_summary_loop(
    summary_fn: Callable[[], Awaitable[None]],
) -> None:
    """Call summary_fn once per midnight CST, forever.

    Any exception from summary_fn propagates — let the task crash so the
    bot runner's FIRST_EXCEPTION gate notices.
    """
    while True:
        now = datetime.now(CST)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        await asyncio.sleep((tomorrow - now).total_seconds())
        await summary_fn()
