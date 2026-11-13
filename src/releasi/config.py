"""Application configuration using Pydantic Settings."""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


def _find_settings_file() -> Path | None:
    """Look for settings.yaml in common locations."""
    candidates = [
        Path("config/settings.yaml"),
        Path("/app/config/settings.yaml"),  # Docker
        Path.home() / ".config" / "releasi" / "settings.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


class Settings(BaseSettings):
    # Database
    db_url: str = "sqlite+aiosqlite:///data/releasi.db"

    # Scheduling — work hours
    work_days: list[int] = [0, 1, 2, 3, 4]  # Mon-Fri
    work_start_hour: int = 9
    work_end_hour: int = 17

    # Work hour daily variation (minutes offset from base start/end)
    work_start_variation: list[int] = Field(default=[-30, 45])
    work_end_variation: list[int] = Field(default=[-45, 30])

    # Clustered timing — sessions (bursts) per day
    sessions_per_day: list[int] = Field(default=[3, 6])          # [min, max]
    actions_per_session: list[int] = Field(default=[2, 6])       # [min, max]
    intra_session_delay: list[int] = Field(default=[120, 480])   # seconds between actions in same session
    inter_session_delay: list[int] = Field(default=[2700, 7200]) # seconds between sessions (45min-2hr)

    # Continuous dispatch mode (dispatch_mode='continuous' on Account)
    # Gap between sessions is calculated dynamically from daily_limit; batch size is randomized.
    continuous_batch_size: list[int] = Field(default=[6, 10])    # leads per session [min, max]

    # Per-account limits (set on Account model; these are fallback defaults)
    default_daily_limit: int = 20
    default_weekly_limit: int = 80

    # Delays between actions (seconds) — used by execute-once / legacy
    min_action_delay_seconds: int = 720
    max_action_delay_seconds: int = 2700

    # Page interaction delays (seconds)
    page_load_delay_min: float = 1.0
    page_load_delay_max: float = 3.0

    # Typing speed (ms per character)
    typing_delay_min_ms: int = 50
    typing_delay_max_ms: int = 150

    # Cooldown
    cooldown_resume_hour_min: int = 8
    cooldown_resume_hour_max: int = 11

    # Weekend activity
    weekend_enabled: bool = True
    weekend_profile_views: list[int] = Field(default=[3, 8])
    weekend_likes: list[int] = Field(default=[1, 4])

    # Browsing noise (between connection request sessions)
    noise_profile_views_per_session: list[int] = Field(default=[1, 3])
    noise_feed_likes_per_session: list[int] = Field(default=[0, 2])

    # Anti-detection
    stealth_enabled: bool = True
    default_timezone: str = "Europe/Berlin"

    # Residential proxy (IPRoyal) — auto-generates per-account proxy URLs
    # Set these globally, then just set proxy_country on each account
    proxy_provider: str = "iproyal"
    proxy_username: str = ""
    proxy_password: str = ""       # Base password (without _country-xx params)
    proxy_hostname: str = "geo.iproyal.com"
    proxy_port: int = 12321
    proxy_lifetime: str = "168h"   # Sticky session duration (168h = 7 days)

    # Browser pool (persistent browsers for session keep-alive)
    pool_max_browsers: int = 3
    pool_keepalive_interval_hours: float = 2.5

    # Browser
    browser_headless: bool = True
    browser_viewport_width: int = 1920
    browser_viewport_height: int = 1080
    browser_locale: str = "en-US"

    # Acceptance checking
    acceptance_check_interval_hours: int = 3  # Kept for backward compatibility (unused)
    max_profiles_per_acceptance_check: int = 30  # Kept for backward compatibility (unused)
    acceptance_check_hour: int = 10  # Run once daily at this hour (new daily diff approach)

    # Bandwidth guard — max estimated proxy MB per account per day (safety net, not a hard cap)
    # Typical usage is ~90-100 MB/day; 300 MB is ~3x normal and catches runaway behaviour.
    daily_bandwidth_limit_mb: float = 300.0

    # Follow-up
    default_followup_delay_hours: int = 24
    # Hard cap on automated follow-up messages sent per account per day.
    # Prevents burst sending and reduces CAPTCHA triggers.
    # Each message takes ~35s, so 15 = ~9 min of browser time spread across day.
    followup_daily_cap: int = 15

    # Logging
    log_level: str = "INFO"
    log_file: str = "data/logs/releasi.log"

    # Slack notifications
    slack_bot_token: str = ""
    slack_user_id: str = "U08RKK348P4"

    # API
    api_enabled: bool = False
    api_key: str = ""
    api_port: int = 8000
    cors_origins: list[str] = Field(default=[])

    # Telegram enrichment (Telethon)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    twitterapi_key: str = ""     # twitterapi.io key for X handle lookup (RELEASI_TWITTERAPI_KEY)
    x_sweep_limit: int = 0       # max leads the X sweep may search in total; 0 = no cap (RELEASI_X_SWEEP_LIMIT)
    telegram_session: str = ""   # StringSession string, generated once

    # Apollo.io enrichment (phone lookup)
    apollo_api_key: str = ""

    model_config = {"env_prefix": "RELEASI_", "extra": "ignore"}


def _load_yaml_overrides(settings_dict: dict) -> dict:
    """Load settings from YAML file and merge with defaults."""
    yaml_path = _find_settings_file()
    if yaml_path is None:
        return settings_dict
    with open(yaml_path) as f:
        yaml_data = yaml.safe_load(f) or {}
    settings_dict.update(yaml_data)
    return settings_dict


@lru_cache
def get_settings() -> Settings:
    """Get the application settings singleton. YAML -> env vars -> defaults."""
    yaml_path = _find_settings_file()
    overrides = {}
    if yaml_path:
        with open(yaml_path) as f:
            overrides = yaml.safe_load(f) or {}
    return Settings(**overrides)
