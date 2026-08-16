"""Settings loaded from .env via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- Kalshi credentials ----
    kalshi_api_key_id: str = ""
    kalshi_private_key: str = ""  # Inline PEM, \n decoded by dotenv

    # ---- Telegram alerts ----
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ---- Risk thresholds ----
    max_capital_loss_pct: float = 40.0  # Kill switch at X% total loss
    max_consecutive_errors: int = 10
    max_position_pct: float = 30.0  # Max % of balance per position
    max_exposure_pct: float = 50.0  # Max % of balance across all positions
    max_orders_per_min: int = 30
    max_loss_per_trade_pct: float = 25.0  # Max % of balance per trade

    # ---- Execution ----
    price_cushion_cents: int = 2  # Bid above ask to absorb price movement (IOC fills at best)

    # ---- Operational ----
    kalshi_vm_host: str = ""  # rsync/ssh target for the deployed VM, e.g. "user@1.2.3.4"
    paper_trading: bool = True  # Start in paper mode by default
    log_format: str = "human"  # "human" or "json"
    log_level: str = "INFO"
    db_path: str = "data"  # Directory for SQLite DBs

    # ---- Kill switch ----
    global_kill_file: str = "KILL"  # Touch this file in project root to stop
