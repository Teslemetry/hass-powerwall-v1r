"""Helpers for the Powerwall V1R integration."""

from __future__ import annotations

from typing import Any

import jwt
from tesla_fleet_api.const import Region, is_valid_region


def region_from_token(access_token: str) -> Region:
    """Extract the Tesla Fleet API region from an access token's `ou_code`."""
    decoded: dict[str, Any] = jwt.decode(
        access_token, options={"verify_signature": False}
    )
    code = str(decoded["ou_code"]).lower()
    if not is_valid_region(code):
        raise ValueError(f"Unsupported region: {code}")
    return code
