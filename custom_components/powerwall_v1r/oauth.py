"""OAuth2 implementation for the Powerwall V1R integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import AUTHORIZE_URL, SCOPES, TOKEN_URL


class PowerwallV1RImplementation(
    config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce
):
    """Powerwall V1R OAuth2 (PKCE) implementation against Tesla Fleet."""

    def __init__(self, hass: HomeAssistant, domain: str, client_id: str) -> None:
        """Initialize OAuth2 implementation."""
        super().__init__(
            hass,
            domain,
            client_id,
            AUTHORIZE_URL,
            TOKEN_URL,
        )

    @property
    def name(self) -> str:
        """Name of the implementation."""
        return "Built-in Tesla Fleet API client"

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra data appended to the authorize URL."""
        data: dict[str, Any] = {
            "prompt": "login",
            "prompt_missing_scopes": "true",
            "require_requested_scopes": "true",
            "scope": " ".join(SCOPES),
        }
        data.update(super().extra_authorize_data)
        return data
