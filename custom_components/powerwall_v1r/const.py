"""Constants for the Powerwall V1R integration."""

from __future__ import annotations

import logging

DOMAIN = "powerwall_v1r"

LOGGER = logging.getLogger(__package__)

CONF_PARENT_ENTRY_ID = "parent_entry_id"
CONF_ENERGY_SITE_ID = "energy_site_id"
CONF_GATEWAY_PASSWORD = "gateway_password"
CONF_GATEWAY_HOST = "gateway_host"

KEY_FILENAME = "powerwall_v1r.key"
KEY_PAIRING_POLL_INTERVAL = 3
KEY_PAIRING_POLL_ATTEMPTS = 5

SCAN_STATUS_SECONDS = 10
SCAN_METERS_SECONDS = 10
SCAN_BATTERY_SOE_SECONDS = 30
SCAN_GRID_STATUS_SECONDS = 30
SCAN_CONFIG_SECONDS = 600

MANUFACTURER = "Tesla"
MODEL = "Powerwall 3 (V1R local)"
