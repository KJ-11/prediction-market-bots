"""Telegram alert sender — single httpx POST, no external lib."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def send_telegram(
    bot_token: str,
    chat_id: str,
    message: str,
    parse_mode: str = "HTML",
) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not bot_token or not chat_id:
        logger.debug("Telegram: skipping (no token/chat_id configured)")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.debug("Telegram: sent message to %s", chat_id)
            return True
    except Exception as e:
        logger.error("Telegram: failed to send: %s", e)
        return False
