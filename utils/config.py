from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    bot_token: str = Field(..., env="BOT_TOKEN")
    main_admin_id: int = Field(..., env="MAIN_ADMIN_ID")
    database_path: Path = Field(default=BASE_DIR / "data" / "bot.db")
    download_proxy: Optional[str] = Field(default=None, env="DOWNLOAD_PROXY")
    download_socket_timeout: int = Field(default=25, env="DOWNLOAD_SOCKET_TIMEOUT")
    download_retries: int = Field(default=3, env="DOWNLOAD_RETRIES")
    download_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        env="DOWNLOAD_USER_AGENT",
    )

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
    )


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
