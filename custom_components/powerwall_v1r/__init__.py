"""The Powerwall V1R integration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiopowerwall import (
    PowerwallAuthenticationError,
    PowerwallClient,
    PowerwallConnectionError,
    PowerwallError,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_GATEWAY_HOST, CONF_GATEWAY_PASSWORD, KEY_FILENAME
from .coordinator import (
    BackupEventsCoordinator,
    BatterySoeCoordinator,
    ConfigCoordinator,
    GridStatusCoordinator,
    MetersCoordinator,
    PowerwallRuntimeData,
    PowerwallV1RConfigEntry,
    StatusCoordinator,
)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: PowerwallV1RConfigEntry
) -> bool:
    """Set up Powerwall V1R from a config entry."""
    key_path = Path(hass.config.path(KEY_FILENAME))
    try:
        key_pem = await hass.async_add_executor_job(key_path.read_bytes)
    except OSError as err:
        raise ConfigEntryNotReady(
            f"RSA key file {key_path} is unavailable: {err}"
        ) from err

    client = PowerwallClient(
        host=entry.data[CONF_GATEWAY_HOST],
        gateway_password=entry.data[CONF_GATEWAY_PASSWORD],
        rsa_private_key_pem=key_pem,
        session=async_get_clientsession(hass),
    )

    try:
        din = await client.connect()
        firmware = await client.get_firmware_version()
    except PowerwallAuthenticationError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (PowerwallConnectionError, PowerwallError) as err:
        raise ConfigEntryNotReady(f"Gateway unreachable: {err}") from err

    status = StatusCoordinator(hass, entry, client)
    meters = MetersCoordinator(hass, entry, client)
    battery_soe = BatterySoeCoordinator(hass, entry, client)
    grid_status = GridStatusCoordinator(hass, entry, client)
    config = ConfigCoordinator(hass, entry, client)
    backup_events = BackupEventsCoordinator(hass, entry, client)

    await asyncio.gather(
        status.async_config_entry_first_refresh(),
        meters.async_config_entry_first_refresh(),
        battery_soe.async_config_entry_first_refresh(),
        grid_status.async_config_entry_first_refresh(),
        config.async_config_entry_first_refresh(),
        backup_events.async_config_entry_first_refresh(),
    )

    entry.runtime_data = PowerwallRuntimeData(
        client=client,
        din=din,
        firmware_version=firmware if isinstance(firmware, str) else None,
        status=status,
        meters=meters,
        battery_soe=battery_soe,
        grid_status=grid_status,
        config=config,
        backup_events=backup_events,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: PowerwallV1RConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
