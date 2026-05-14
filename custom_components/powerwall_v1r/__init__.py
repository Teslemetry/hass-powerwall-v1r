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

from .const import (
    CONF_GATEWAY_HOST,
    CONF_GATEWAY_PASSWORD,
    KEY_FILENAME,
    LOGGER,
    MASTER_BATTERY_DIN_SUFFIX,
)
from .coordinator import (
    BackupEventsCoordinator,
    BatterySoeCoordinator,
    ComponentsCoordinator,
    ConfigCoordinator,
    GridStatusCoordinator,
    MasterBlock,
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
        firmware_details = await client.get_firmware_details()
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
    components = ComponentsCoordinator(hass, entry, client)

    await asyncio.gather(
        status.async_config_entry_first_refresh(),
        meters.async_config_entry_first_refresh(),
        battery_soe.async_config_entry_first_refresh(),
        grid_status.async_config_entry_first_refresh(),
        config.async_config_entry_first_refresh(),
        backup_events.async_config_entry_first_refresh(),
        components.async_config_entry_first_refresh(),
    )

    master_blocks = _master_blocks(config.data, din)
    if len(master_blocks) > 1:
        LOGGER.warning(
            "Site has %d Powerwall masters but only the first will be exposed "
            "as per-battery devices — components-array indexing for multi-master "
            "sites isn't confirmed from real captures yet. Please share a "
            "get_components capture if you see this.",
            len(master_blocks),
        )
        master_blocks = master_blocks[:1]

    entry.runtime_data = PowerwallRuntimeData(
        client=client,
        din=din,
        firmware_version=firmware_details["system"]["version"]["text"] or None,
        status=status,
        meters=meters,
        battery_soe=battery_soe,
        grid_status=grid_status,
        config=config,
        backup_events=backup_events,
        components=components,
        master_blocks=master_blocks,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _master_blocks(config_payload: dict, gateway_din: str) -> tuple[MasterBlock, ...]:
    """Build per-master block metadata from get_config.battery_blocks.

    Each entry in ``battery_blocks`` is one Powerwall master with its own
    optional ``battery_expansions[]``. Block 0's HA device identifier is
    derived from the gateway DIN for backwards compatibility with existing
    deployments; later blocks use a numeric suffix.
    """
    blocks = config_payload.get("battery_blocks") or []
    out: list[MasterBlock] = []
    for i, block in enumerate(blocks):
        expansion_dins = tuple(
            expansion["din"]
            for expansion in block.get("battery_expansions") or []
            if isinstance(expansion, dict)
            and isinstance(expansion.get("din"), str)
            and expansion["din"]
        )
        device_din = (
            f"{gateway_din}{MASTER_BATTERY_DIN_SUFFIX}"
            if i == 0
            else f"{gateway_din}_battery_{i}"
        )
        out.append(
            MasterBlock(
                block_index=i,
                device_din=device_din,
                expansion_dins=expansion_dins,
            )
        )
    return tuple(out)


async def async_unload_entry(
    hass: HomeAssistant, entry: PowerwallV1RConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
