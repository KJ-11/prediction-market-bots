"""Alert manager — formatted alerts via Telegram + file logging."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
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


def _short_ticker(ticker: str) -> str:
    """Shorten Kalshi ticker for display.

    KXATPCHALLENGERMATCH-26APR05RIBNEU-NEU → ATP-C RIBNEU NEU
    KXNBAGAME-26APR05INDCLE-CLE → NBA INDCLE CLE
    """
    parts = ticker.split("-")
    if len(parts) < 3:
        return ticker

    series = parts[0]
    # Strip KX prefix
    raw = series.removeprefix("KX")
    # Known series abbreviations — check full form first, then stripped
    abbrevs = {
        "ATPCHALLENGERMATCH": "ATP-C",
        "ATPMATCH": "ATP",
        "WTAMATCH": "WTA",
        "NBAGAME": "NBA",
        "NBASPREAD": "NBA-S",
        "MLBGAME": "MLB",
        "NHLGAME": "NHL",
        "LALIGAGAME": "LaLiga",
        "LALIGA2GAME": "LaLiga2",
        "LIGUE1GAME": "Ligue1",
        "SERIEAGAME": "SerieA",
        "IPLGAME": "IPL",
        "NCAAMBTOTAL": "NCAAM",
    }
    short = abbrevs.get(raw, raw)

    # Last part is the outcome/team
    matchup = parts[-2] if len(parts) >= 3 else ""
    # Strip date prefix (26APR05) and optional time prefix (1435)
    if len(matchup) > 7:
        matchup = matchup[7:]  # Remove 26APR05 etc
        # If remaining starts with digits (time like 1435), strip those too
        while matchup and matchup[0].isdigit():
            matchup = matchup[1:]

    team = parts[-1]
    return f"{short} {matchup} {team}".strip()


class AlertManager:
    """Sends formatted alerts to Telegram and logs all messages to file.

    Every message is written to data/alerts/{bot_name}-YYYY-MM-DD.log
    regardless of whether Telegram delivery succeeds.
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
        """Append message to daily log file, namespaced by bot."""
        now = datetime.now(CST)
        filename = f"{self._bot_name}-{now.strftime('%Y-%m-%d')}.log"
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
            f"Mode: {mode} | Balance: <b>${balance:.2f}</b>\n"
            f"Markets: {coins_str}"
        )
        return await self._send("BOT STARTED", "\U0001f916", body)

    async def bot_stopped(self, reason: str, balance: Decimal | None = None) -> bool:
        bal_line = f" | Balance: <b>${balance:.2f}</b>" if balance is not None else ""
        body = f"{reason}{bal_line}"
        return await self._send("BOT STOPPED", "\U0001f6d1", body)

    # ---- Round --------------------------------------------------------

    async def round_start(
        self,
        window: str,
        coins: list[str],
        balance: Decimal,
    ) -> bool:
        # Log to file only — no Telegram spam for round starts
        coins_str = ", ".join(coins)
        self._file_log(f"ROUND {window}", f"{coins_str} | Balance: ${balance:.2f}")
        logger.info("Log-only [ROUND %s]: %s | Balance: $%.2f", window, coins_str, balance)
        return True

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
        # No Telegram for empty rounds — just file log
        if total_trades == 0 and total_signals == 0:
            self._file_log(
                f"ROUND {window}",
                f"No signals | Balance: ${balance:.2f}",
            )
            logger.info(
                "Log-only [ROUND %s]: No signals | Balance: $%.2f",
                window, balance,
            )
            return True

        if total_trades > 0:
            result_icon = "\u2705" if pnl and pnl > 0 else "\u274c"
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "$0.00"
            lines = "\n".join(coin_lines)
            body = (
                f"{lines}\n"
                f"P&L: <b>{pnl_str}</b> | Balance: <b>${balance:.2f}</b>"
            )
            header = f"{result_icon} {total_trades} trade(s)"
        else:
            lines = "\n".join(coin_lines)
            body = (
                f"{lines}\n"
                f"Balance: <b>${balance:.2f}</b>"
            )
            header = f"{total_signals} signal(s), 0 fills"

        if shadow_summary:
            body += f"\n<i>Shadow: {shadow_summary}</i>"

        return await self._send(f"ROUND {window} — {header}", "\U0001f4ca", body)

    # ---- Whale-bot trades (combined signal + fill) --------------------

    async def whale_entry(
        self,
        ticker: str,
        side: str,
        price: Decimal,
        size: int,
        cost: Decimal,
        fee: Decimal,
        whale_count: int,
        consensus_pct: float,
        balance: Decimal,
        equity: Decimal,
        whale_avg_price: Decimal = Decimal("0"),
        signal_ask: Decimal = Decimal("0"),
    ) -> bool:
        """Single message for whale signal entry (replaces separate signal + fill)."""
        short = _short_ticker(ticker)
        # Compare our fill to whale VWAP and signal ask
        slip_parts = []
        if whale_avg_price > 0:
            diff_whale = (price - whale_avg_price) * 100
            slip_parts.append(
                f"whale ${whale_avg_price:.2f} ({diff_whale:+.1f}¢)"
            )
        if signal_ask > 0 and signal_ask != price:
            diff_ask = (price - signal_ask) * 100
            slip_parts.append(f"ask ${signal_ask:.2f} ({diff_ask:+.1f}¢)")
        slip_line = f"vs {' / '.join(slip_parts)}\n" if slip_parts else ""
        body = (
            f"<b>{short}</b> {side.upper()} @ ${price:.2f} x{size}\n"
            f"{slip_line}"
            f"Cost: ${cost:.2f} + ${fee:.2f} fee\n"
            f"Whales: {whale_count} ({consensus_pct:.0%})\n"
            f"Bal: ${balance:.2f} | Eq: ${equity:.2f}"
        )
        return await self._send("ENTRY", "\U0001f433", body)

    async def whale_settled(
        self,
        ticker: str,
        side: str,
        outcome: str,
        won: bool,
        entry_price: Decimal,
        size: int,
        pnl: Decimal,
        entry_fee: Decimal,
        exit_fee: Decimal,
        balance: Decimal,
        equity: Decimal,
    ) -> bool:
        """Settlement message with accurate fee-inclusive PnL."""
        short = _short_ticker(ticker)
        icon = "\u2705" if won else "\u274c"
        result = "WIN" if won else "LOSS"
        body = (
            f"{icon} <b>{short}</b> {side.upper()} \u2192 {outcome.upper()}\n"
            f"Entry: ${entry_price:.2f} x{size} | P&L: <b>${pnl:+.2f}</b>\n"
            f"Fees: ${entry_fee:.2f} + ${exit_fee:.2f}\n"
            f"Bal: ${balance:.2f} | Equity: ${equity:.2f}"
        )
        return await self._send(f"SETTLED — {result}", "\u2696\ufe0f", body)

    async def whale_stop_loss(
        self,
        ticker: str,
        side: str,
        entry_price: Decimal,
        exit_price: Decimal,
        stop_threshold: Decimal,
        trigger_bid: Decimal,
        size: int,
        pnl: Decimal,
        entry_fee: Decimal,
        exit_fee: Decimal,
        balance: Decimal,
        equity: Decimal,
    ) -> bool:
        """Stop-loss exit alert with trigger details."""
        short = _short_ticker(ticker)
        body = (
            f"\U0001f6d1 <b>{short}</b> {side.upper()} STOPPED\n"
            f"Entry: ${entry_price:.2f} \u2192 Exit: ${exit_price:.2f} x{size}\n"
            f"Stop: ${stop_threshold:.2f} | Trigger: ${trigger_bid:.2f}\n"
            f"P&L: <b>${pnl:+.2f}</b>\n"
            f"Fees: ${entry_fee:.2f} + ${exit_fee:.2f}\n"
            f"Bal: ${balance:.2f} | Equity: ${equity:.2f}"
        )
        return await self._send("STOP LOSS", "\U0001f6d1", body)

    # ---- Legacy trade_filled (for crypto bot) -------------------------

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
            f"<b>{coin}</b>: {side} {outcome} @ ${price:.2f} x{size}\n"
            f"Cost: ${cost:.2f} | Bal: ${balance:.2f}"
        )
        return await self._send("FILL", "\U0001f4b5", body)

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
        if trades == 0:
            body = f"No trades | Balance: <b>${balance:.2f}</b>"
        else:
            win_rate = wins / trades * 100
            body = (
                f"{trades} trades ({wins}W/{trades - wins}L — {win_rate:.0f}%)\n"
                f"P&L: <b>${pnl:+.2f}</b> | Balance: <b>${balance:.2f}</b>"
            )
            if total_signals > 0:
                body += f"\nSignals: {total_signals} | Fill rate: {trades}/{total_signals}"
        if shadow_line:
            body += f"\n<i>Shadow: {shadow_line}</i>"
        return await self._send(f"DAILY — {date_str}", "\U0001f4c8", body)

    # ---- Errors -------------------------------------------------------

    async def kill_switch_triggered(self, reason: str) -> bool:
        return await self._send("KILL SWITCH", "\U0001f6a8", reason)

    async def circuit_breaker(self, reason: str) -> bool:
        return await self._send("CIRCUIT BREAKER", "\u26a0\ufe0f", reason)
