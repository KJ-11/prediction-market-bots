"""Bot lifecycle manager — signal handling, alerts, exit codes."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Coroutine
from typing import Any

from shared.alerts.manager import AlertManager
from shared.risk import KillSwitchTriggered

logger = logging.getLogger(__name__)

# Exit codes
EXIT_NORMAL = 0
EXIT_CRASH = 1
EXIT_KILL_SWITCH = 42


class BotRunner:
    """Wraps an async bot function with lifecycle management.

    Handles:
        - SIGINT/SIGTERM → graceful shutdown
        - KillSwitchTriggered → exit code 42
        - Unhandled exceptions → exit code 1
        - Telegram alerts on start/stop/crash/kill

    The bot function is responsible for calling alerts.bot_started()
    once it has mode, coins, and balance info.
    """

    def __init__(
        self,
        bot_name: str,
        alerts: AlertManager,
    ) -> None:
        self._bot_name = bot_name
        self._alerts = alerts
        self._shutdown_event: asyncio.Event | None = None
        self._stop_alerted = False

    def _ensure_event(self) -> asyncio.Event:
        """Create Event lazily inside the running loop (Python 3.9 compat)."""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        return self._shutdown_event

    @property
    def shutdown_requested(self) -> bool:
        return self._ensure_event().is_set()

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._ensure_event()

    def run(
        self,
        async_fn: Callable[..., Coroutine[Any, Any, None]],
        *args: Any,
        **kwargs: Any,
    ) -> int:
        """Run the bot function. Returns exit code."""
        return asyncio.run(self._run_wrapper(async_fn, *args, **kwargs))

    async def _run_wrapper(
        self,
        async_fn: Callable[..., Coroutine[Any, Any, None]],
        *args: Any,
        **kwargs: Any,
    ) -> int:
        """Async wrapper with signal handling and error catching."""
        loop = asyncio.get_running_loop()

        # Install signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_signal, sig)

        exit_code = EXIT_NORMAL

        try:
            logger.info("[%s] Bot started", self._bot_name)
            await async_fn(*args, **kwargs)

            if not self._stop_alerted:
                await self._alerts.bot_stopped("normal shutdown")
            logger.info("[%s] Bot stopped normally", self._bot_name)

        except KillSwitchTriggered as e:
            exit_code = EXIT_KILL_SWITCH
            logger.critical("[%s] Kill switch: %s", self._bot_name, e.reason)
            await self._alerts.kill_switch_triggered(e.reason)

        except asyncio.CancelledError:
            exit_code = EXIT_NORMAL
            logger.info("[%s] Bot cancelled", self._bot_name)
            if not self._stop_alerted:
                await self._alerts.bot_stopped("cancelled")

        except Exception as e:
            exit_code = EXIT_CRASH
            logger.exception("[%s] Bot crashed: %s", self._bot_name, e)
            await self._alerts.bot_stopped("crashed — check logs")

        return exit_code

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Signal handler — sends alert immediately, then requests graceful exit."""
        logger.info("[%s] Received %s, shutting down...", self._bot_name, sig.name)
        self._ensure_event().set()
        # Fire alert now — don't rely on graceful shutdown reaching bot_stopped()
        self._stop_alerted = True
        asyncio.ensure_future(self._alerts.bot_stopped(f"signal {sig.name}"))
