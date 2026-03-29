"""
Mira — Application Settings

Type-safe configuration via Pydantic BaseSettings.
Loads from .env file and environment variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # --- Meta / WhatsApp Cloud API ---
    ACCESS_TOKEN: str
    APP_ID: str = ""
    APP_SECRET: str
    VERSION: str = "v21.0"
    PHONE_NUMBER_ID: str
    VERIFY_TOKEN: str

    # --- Anthropic (Claude AI) ---
    ANTHROPIC_API_KEY: str

    # --- Supabase ---
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""

    # --- Redis (Upstash) ---
    REDIS_URL: str = ""

    # --- Google Maps ---
    GOOGLE_MAPS_API_KEY: str = ""

    # --- Firecrawl ---
    FIRECRAWL_API_KEY: str = ""

    # --- Open Exchange Rates ---
    OPEN_EXCHANGE_RATES_APP_ID: str = ""

    # --- Stripe Payment Links ---
    STRIPE_FEATURED_LINK: str = ""
    STRIPE_PREMIUM_LINK: str = ""

    # --- App Config ---
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    @property
    def whatsapp_api_url(self) -> str:
        return f"https://graph.facebook.com/{self.VERSION}/{self.PHONE_NUMBER_ID}/messages"


def get_settings() -> Settings:
    """Factory function for dependency injection."""
    return Settings()
