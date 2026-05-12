"""
Async Zendesk HTTP Client module.

Provides a secure, retry-capable async HTTP client for communicating with the
Zendesk REST API. Key features:
  - Path allowlisting: Only pre-approved API endpoints can be called.
  - Automatic retries: Transient errors and rate limits are retried with backoff.
  - Consistent error handling: All errors are wrapped in ZendeskError.
  - Async-first: Uses httpx.AsyncClient for non-blocking I/O.
  - Lifespan management: Client must be explicitly opened/closed.
"""

import re
from contextlib import asynccontextmanager
from typing import Any, Dict

import httpx
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

from config import settings

logger = structlog.get_logger()

# Explicit allowlist of Zendesk API paths this client is permitted to call.
# Paths with {id} are matched via regex (any integer).
# This acts as a security boundary — even if a tool constructs a bad path,
# the client will refuse to send the request.
ALLOWED_PATHS = {
    "/api/v2/search.json",
    "/api/v2/search/count.json",
    "/api/v2/tickets.json",
    "/api/v2/tickets/{id}.json",
    "/api/v2/tickets/{id}/audits.json",
    "/api/v2/tickets/{id}/comments.json",
    "/api/v2/tickets/{id}/tags.json",
    "/api/v2/users/{id}.json",
    "/api/v2/users/search.json",
    "/api/v2/users/show_many.json",
    "/api/v2/organizations/{id}.json",
    "/api/v2/organizations/search.json",
    "/api/v2/views/{id}.json",
    "/api/v2/views/{id}/count.json",
    "/api/v2/views/{id}/tickets.json",
    "/api/v2/ticket_fields.json",
    "/api/v2/triggers.json",
    "/api/v2/triggers/active.json",
    "/api/v2/triggers/search.json",
    "/api/v2/triggers/{id}.json",
    "/api/v2/automations.json",
    "/api/v2/automations/{id}.json",
    "/api/v2/macros.json",
    "/api/v2/macros/active.json",
    "/api/v2/macros/{id}.json",
    "/api/v2/groups.json",
}


class ZendeskError(Exception):
    """
    Custom exception for Zendesk API errors.

    Attributes:
        status  - HTTP status code (e.g. 401, 403, 404, 429, 500)
        message - Human-readable error description
        hint    - Actionable suggestion for resolving the error
    """

    def __init__(self, status: int, message: str, hint: str = ""):
        self.status = status
        self.message = message
        self.hint = hint
        super().__init__(message)


def _is_retryable(exc: BaseException) -> bool:
    """Determine if an exception should trigger a retry (429 rate limit or 5xx)."""
    if isinstance(exc, ZendeskError):
        return exc.status == 429 or exc.status >= 500
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class ZendeskClient:
    """
    Async HTTP client for the Zendesk API supporting two auth modes:

    Mode 1 — API Token (simple, no expiry):
        Uses HTTP Basic Auth with {email}/token as username and API token as password.

    Mode 2 — OAuth Bearer Token (recommended for production):
        Uses Authorization: Bearer {access_token} header.
        Supports automatic token refresh on 401 if refresh credentials are configured.

    The auth mode is auto-detected from config: if ZD_OAUTH_ACCESS_TOKEN is set,
    OAuth is used. Otherwise falls back to API token auth.
    """

    def __init__(self):
        self.base_url = settings.zendesk_base_url
        self._client: httpx.AsyncClient | None = None
        self._auth_mode = settings.auth_mode
        self._access_token = settings.zd_oauth_access_token
        self._refresh_token = settings.zd_oauth_refresh_token

    async def open(self):
        """Initialize the async HTTP client with the appropriate auth."""
        if self._auth_mode == "api_token":
            email = settings.zd_email
            token = settings.zd_api_token
            auth = (f"{email}/token", token)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=auth,
                timeout=30.0,
                headers={"Accept": "application/json"},
            )
        else:
            # OAuth mode — use Bearer token in headers (no httpx auth param)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                },
            )

    async def close(self):
        """Close the underlying HTTP connection pool. Call on shutdown."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _refresh_oauth_token(self) -> bool:
        """
        Attempt to refresh the OAuth access token using the refresh token.

        Returns True if refresh succeeded, False otherwise.
        On success, updates the client's Authorization header with the new token.
        """
        if not settings.can_refresh_oauth:
            return False

        logger.info("oauth_refresh", status="attempting")
        try:
            async with httpx.AsyncClient(timeout=15.0) as refresh_client:
                response = await refresh_client.post(
                    f"{self.base_url}/oauth/tokens",
                    json={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": settings.zd_oauth_client_id,
                        "client_secret": settings.zd_oauth_client_secret,
                    },
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code != 200:
                    logger.warning("oauth_refresh", status="failed", code=response.status_code)
                    return False

                data = response.json()
                self._access_token = data["access_token"]
                self._refresh_token = data.get("refresh_token", self._refresh_token)

                # Update the client's default Authorization header
                self._client.headers["Authorization"] = f"Bearer {self._access_token}"
                logger.info("oauth_refresh", status="success")
                return True
        except Exception as e:
            logger.error("oauth_refresh", status="error", error=str(e))
            return False

    def _validate_path(self, path: str) -> None:
        """
        Check that the requested API path is in the allowlist.

        Strips query parameters before matching. Paths containing {id}
        in the allowlist are matched with end-anchored regex to prevent
        suffix injection or path traversal attacks.

        Raises:
            ZendeskError(403) if the path is not permitted.
        """
        normalized = path.split("?")[0]
        for allowed in ALLOWED_PATHS:
            if "{id}" in allowed:
                pattern = allowed.replace("{id}", r"\d+")
                if re.match(pattern.replace(".", r"\.") + "$", normalized):
                    return
            elif normalized == allowed:
                return
        raise ZendeskError(403, f"Path not allowed: {path}", "Only permitted endpoints are allowed")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def get(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """
        Send a GET request to the Zendesk API.

        If using OAuth and a 401 is received, attempts to refresh the token
        and retry the request once before raising an error.

        Retry behavior:
            Retries up to 3 times with exponential backoff on 429 and 5xx.
        """
        self._validate_path(path)
        try:
            response = await self._client.get(path, params=params)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                raise ZendeskError(429, "Rate limit exceeded", f"Retry after {retry_after} seconds")
            # On 401 with OAuth, attempt token refresh and retry once
            if response.status_code == 401 and self._auth_mode == "oauth":
                if await self._refresh_oauth_token():
                    response = await self._client.get(path, params=params)
                    response.raise_for_status()
                    return response.json()
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e)
        except httpx.RequestError as e:
            raise ZendeskError(500, f"Request failed: {str(e)}", "Check network connectivity")

    async def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a POST request to the Zendesk API (used for creating resources).
        Handles OAuth token refresh on 401.
        Not retried automatically to avoid creating duplicate resources.
        """
        self._validate_path(path)
        try:
            response = await self._client.post(
                path,
                json=body,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                raise ZendeskError(429, "Rate limit exceeded", f"Retry after {retry_after} seconds")
            if response.status_code == 401 and self._auth_mode == "oauth":
                if await self._refresh_oauth_token():
                    response = await self._client.post(path, json=body, headers={"Content-Type": "application/json"})
                    response.raise_for_status()
                    return response.json()
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e)
        except httpx.RequestError as e:
            raise ZendeskError(500, f"Request failed: {str(e)}", "Check network connectivity")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def put(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a PUT request to the Zendesk API (used for ticket updates).

        Args:
            path - API endpoint path (e.g. '/api/v2/tickets/123.json')
            body - JSON body to send (e.g. {"ticket": {"status": "solved"}})

        Returns:
            Dictionary containing the updated ticket data.

        Raises:
            ZendeskError on authentication, permission, or network errors.

        Retry behavior:
            Retries on 429 (rate limit) and 5xx only. This is safe for PUT
            because Zendesk ticket updates are idempotent (setting status to
            'solved' twice has the same effect as once).
        """
        self._validate_path(path)
        try:
            response = await self._client.put(
                path,
                json=body,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                raise ZendeskError(429, "Rate limit exceeded", f"Retry after {retry_after} seconds")
            if response.status_code == 401 and self._auth_mode == "oauth":
                if await self._refresh_oauth_token():
                    response = await self._client.put(path, json=body, headers={"Content-Type": "application/json"})
                    response.raise_for_status()
                    data = response.json()
                    return {"ticket": data.get("ticket", {})}
            response.raise_for_status()
            data = response.json()
            return {"ticket": data.get("ticket", {})}
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e)
        except httpx.RequestError as e:
            raise ZendeskError(500, f"Request failed: {str(e)}", "Check network connectivity")

    @staticmethod
    def _map_http_error(e: httpx.HTTPStatusError) -> ZendeskError:
        """Map httpx HTTP errors to ZendeskError with appropriate messages."""
        status = e.response.status_code
        if status == 401:
            return ZendeskError(401, "Authentication failed", "Check your API token")
        elif status == 403:
            return ZendeskError(403, "Access denied", "Token lacks required permissions")
        elif status == 404:
            return ZendeskError(404, "Resource not found", "Check ticket ID or query")
        else:
            return ZendeskError(status, str(e), "")


# Module-level singleton client instance, shared across all tool functions.
# Must be opened via lifespan before use.
client = ZendeskClient()
