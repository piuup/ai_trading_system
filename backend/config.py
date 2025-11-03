from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml
from dotenv import load_dotenv


@dataclass
class Settings:
    deepseek_api_key: Optional[str]
    deepseek_api_base: str
    tushare_token: Optional[str]


# Default credentials provided in project brief for quick start (override via .env).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "defaults.yaml"


def load_settings() -> Settings:
    """Load application settings from environment variables (with .env support)."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    default_settings = {}
    if DEFAULT_CONFIG_PATH.exists():
        with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            default_settings = yaml.safe_load(fh) or {}

    app_cfg = default_settings.get("app", {})
    llm_cfg = default_settings.get("llm", {})
    data_cfg = default_settings.get("data", {})

    return Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", llm_cfg.get("api_key")),
        deepseek_api_base=os.getenv("DEEPSEEK_API_BASE", app_cfg.get("deepseek_api_base", llm_cfg.get("api_base"))),
        tushare_token=os.getenv("TUSHARE_TOKEN", data_cfg.get("tushare_token")),
    )


settings = load_settings()
