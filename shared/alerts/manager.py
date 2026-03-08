"""Alert manager — formatted alerts via Telegram + file logging."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from shared.alerts.telegram import send_telegram

logger = logging.getLogger(__name__)

CST = ZoneInfo("America/Chicago")


def _ts() -> str:
    """Current timestamp in CST."""
    return datetime.now(CST).strftime("%H:%M %Z")


def _strip_html(text: str) -> str:
    """Remove HTML tags for file logging."""
    return re.sub(r"<[^>]+>", "", text)


class AlertManager:
    """Sends formatted alerts to Telegram and logs all messages to file.

    Every message is written to data/alerts/YYYY-MM-DD.log regardless of
    whether Telegram delivery succeeds.
    """

    def __init__(
        self,
        bot_name: str,
        telegram_token: str = "",
        telegram_chat_id: str = "",
    ) -> None:
        self._bot_name = bot_name
        self._telegram_token = telegram_token
        self._telegram_chat_id = telegram_chat_id
        self._log_dir = os.path.join("data", "alerts")
        os.makedirs(self._log_dir, exist_ok=True)

    # ---- Core send ----------------------------------------------------

    async def _send(self, alert_type: str, icon: str, body: str) -> bool:
        """Format, file-log, and send a Telegram message."""
        formatted = f"{icon} <b>{alert_type}</b>\n{body}"

        # File log (always, regardless of Telegram success)
        self._file_log(alert_type, body)

        sent = await send_telegram(
            self._telegram_token, self._telegram_chat_id, formatted,
        )
        logger.info("Alert [%s]: %s", alert_type, _strip_html(body))
        return sent

    def _file_log(self, alert_type: str, body: str) -> None:
        """Append message to daily log file."""
        now = datetime.now(CST)
        filename = now.strftime("%Y-%m-%d") + ".log"
        path = os.path.join(self._log_dir, filename)
        timestamp = now.strftime("%H:%M:%S %Z")
        clean = _strip_html(body).replace("\n", "\n  ")
        try:
            with open(path, "a") as f:
                f.write(f"[{timestamp}] {alert_type}: {clean}\n")
        except OSError as e:
            logger.warning("Failed to write alert log: %s", e)

    # ---- File-log only (no Telegram) ----------------------------------

    def log_only(self, alert_type: str, message: str) -> None:
        """Write to file log without sending to Telegram."""
        self._file_log(alert_type, message)
        logger.info("Log-only [%s]: %s", alert_type, message)

    # ---- Lifecycle ----------------------------------------------------

    async def bot_started(
        self,
        mode: str,
        coins: list[str],
        balance: Decimal,
    ) -> bool:
        coins_str = ", ".join(coins)
        body = (
            f"Mode: {mode} | Coins: {coins_str}\n"
            f"Balance: <b>${balance:.2f}</b>\n"
            f"{_ts()}"
        )
        return await self._send("BOT STARTED", "\U0001f916", body)

    async def bot_stopped(self, reason: str, balance: Decimal | None = None) -> bool:
        bal_line = f"\nBalance: <b>${balance:.2f}</b>" if balance is not None else ""
        body = f"Reason: {reason}{bal_line}\n{_ts()}"
        return await self._send("BOT STOPPED", "\U0001f916", body)

    # ---- Round --------------------------------------------------------

    async def round_start(
        self,
        window: str,
        coins: list[str],
        balance: Decimal,
    ) -> bool:
        coins_str = ", ".join(coins)
        body = f"{coins_str} | Balance: <b>${balance:.2f}</b>"
        return await self._send(f"ROUND {window}", "\U0001f514", body)

    async def round_summary(
        self,
        window: str,
        coin_lines: list[str],
        total_signals: int,
        total_trades: int,
        pnl: Decimal | None,
        balance: Decimal,
        shadow_summary: str | None = None,
    ) -> bool:
        if total_trades > 0:
            result_icon = "\u2705" if pnl and pnl > 0 else "\u274c"
            header = f"{result_icon} {total_trades} trade(s)"
            lines = "\n".join(coin_lines)
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "$0.00"
            body = (
                f"{header}\n"
                f"{lines}\n\n"
                f"P&L: <b>{pnl_str}</b> | Balance: <b>${balance:.2f}</b>"
            )
        elif total_signals > 0:
            lines = "\n".join(coin_lines)
            body = (
                f"{total_signals} signal(s), 0 trades:\n"
                f"{lines}\n\n"
                f"Balance: <b>${balance:.2f}</b>"
            )
        else:
            body = f"No signals | Balance: <b>${balance:.2f}</b>"

        if shadow_summary:
            body += f"\n\n<i>Shadow: {shadow_summary}</i>"

        return await self._send(f"ROUND {window}", "\U0001f4ca", body)

    # ---- Trades -------------------------------------------------------

    async def trade_filled(
        self,
        coin: str,
        side: str,
        outcome: str,
        price: Decimal,
        size: int,
        cost: Decimal,
        balance: Decimal,
    ) -> bool:
        body = (
            f"{coin}: {side} {outcome} @ ${price} x{size}\n"
            f"Cost: <b>${cost:.2f}</b> | Balance: <b>${balance:.2f}</b>"
        )
        return await self._send("TRADE FILLED", "\U0001f4b5", body)

    # ---- Daily summary ------------------------------------------------

    async def daily_summary(
        self,
        date_str: str,
        trades: int,
        wins: int,
        pnl: Decimal,
        balance: Decimal,
        total_signals: int = 0,
        shadow_line: str | None = None,
    ) -> bool:
        win_rate = wins / trades * 100 if trades > 0 else 0
        fill_rate = f"{trades}/{total_signals}" if total_signals > 0 else "—"
        body = (
            f"Trades: {trades} ({wins}W/{trades - wins}L — {win_rate:.0f}%)\n"
            f"Signals: {total_signals} | Fills: {fill_rate}\n"
            f"P&L: <b>${pnl:+.2f}</b> | Balance: <b>${balance:.2f}</b>"
        )
        if shadow_line:
            body += f"\n\n<i>Shadow: {shadow_line}</i>"
        return await self._send(f"DAILY SUMMARY — {date_str}", "\U0001f4c8", body)

    # ---- Errors -------------------------------------------------------

    async def kill_switch_triggered(self, reason: str) -> bool:
        body = f"{reason}\n{_ts()}"
        return await self._send("KILL SWITCH", "\U0001f6a8", body)

    async def circuit_breaker(self, reason: str) -> bool:
        body = f"{reason}\n{_ts()}"
        return await self._send("CIRCUIT BREAKER", "\u26a0\ufe0f", body)
