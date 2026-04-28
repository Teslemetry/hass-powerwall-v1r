"""Constants for the Powerwall V1R integration."""

from __future__ import annotations

import logging

from tesla_fleet_api.const import Scope

DOMAIN = "powerwall_v1r"

LOGGER = logging.getLogger(__package__)

CLIENT_ID = "71b813eb-4a2e-483a-b831-4dec5cb9bf0d"

AUTHORIZE_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/authorize"
TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"

SCOPES: list[Scope] = [
    Scope.OPENID,
    Scope.OFFLINE_ACCESS,
    Scope.ENERGY_DEVICE_DATA,
    Scope.ENERGY_CMDS,
]

CONF_ENERGY_SITE_ID = "energy_site_id"
CONF_GATEWAY_PASSWORD = "gateway_password"
CONF_GATEWAY_HOST = "gateway_host"
CONF_TOKEN = "token"

KEY_FILENAME = "powerwall_v1r.key"
KEY_PAIRING_POLL_INTERVAL = 3
KEY_PAIRING_POLL_ATTEMPTS = 5
