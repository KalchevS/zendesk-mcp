"""
Configuration module for the Zendesk MCP Server.

Uses pydantic-settings to load environment variables from a .env file.
All Zendesk credentials and operational limits are managed here as a
single Settings instance shared across the application.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables and .env file.

    Authentication modes (pick one):
      Mode 1 — API Token (simple):
        ZD_SUBDOMAIN, ZD_EMAIL, ZD_API_TOKEN

      Mode 2 — OAuth Bearer Token:
        ZD_SUBDOMAIN, ZD_OAUTH_ACCESS_TOKEN
        Optionally: ZD_OAUTH_REFRESH_TOKEN, ZD_OAUTH_CLIENT_ID, ZD_OAUTH_CLIENT_SECRET
        (for automatic token refresh when access token expires)

    Optional env vars (with defaults):
        LOG_LEVEL           - Logging verbosity (default: INFO)
        TOOLS_MAX_PER_PAGE  - Maximum results per page for paginated tools (default: 100)
        TOOLS_MAX_PAGES     - Maximum page number allowed in pagination (default: 100)
        MASK_ERRORS         - If true, hide internal error details from clients (default: false)
    """

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # --- Required ---
    zd_subdomain: str

    # --- API Token auth (Mode 1) ---
    zd_email: str = ""
    zd_api_token: str = ""

    # --- OAuth auth (Mode 2) ---
    zd_oauth_access_token: str = ""
    zd_oauth_refresh_token: str = ""
    zd_oauth_client_id: str = ""
    zd_oauth_client_secret: str = ""

    # --- Optional operational settings ---
    log_level: str = "INFO"
    tools_max_per_page: int = 100
    tools_max_pages: int = 100
    mask_errors: bool = False

    @property
    def zendesk_base_url(self) -> str:
        """Construct the full Zendesk API base URL from the subdomain."""
        return f"https://{self.zd_subdomain}.zendesk.com"

    @property
    def auth_mode(self) -> str:
        """Determine which authentication mode is configured: 'api_token' or 'oauth'."""
        if self.zd_oauth_access_token:
            return "oauth"
        if self.zd_email and self.zd_api_token:
            return "api_token"
        raise ValueError(
            "No valid auth configured. Set either ZD_EMAIL + ZD_API_TOKEN "
            "or ZD_OAUTH_ACCESS_TOKEN in your .env file."
        )

    @property
    def can_refresh_oauth(self) -> bool:
        """Check if OAuth token refresh is possible (all required fields present)."""
        return bool(
            self.zd_oauth_refresh_token
            and self.zd_oauth_client_id
            and self.zd_oauth_client_secret
        )


# Singleton settings instance used throughout the application
settings = Settings()
