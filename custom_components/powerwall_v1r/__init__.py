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

    master_blocks = _master_blocks(config.data, status.data, components.data, din)

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


def _block_din(block: dict) -> str | None:
    """Return the physical DIN/VIN carried by a battery block."""
    for key in ("din", "vin"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _status_battery_dins(status_payload: dict) -> tuple[str, ...]:
    """Return Powerwall DINs reported by the live status payload."""
    blocks = (
        status_payload.get("control", {}).get("batteryBlocks", [])
        if isinstance(status_payload.get("control"), dict)
        else []
    )
    return tuple(
        block["din"]
        for block in blocks
        if isinstance(block, dict)
        and isinstance(block.get("din"), str)
        and block["din"]
    )


def _path(data: object, *keys: object) -> object:
    """Walk nested mappings/lists; return None if a step is missing."""
    for key in keys:
        if isinstance(key, int):
            if not isinstance(data, list) or not -len(data) <= key < len(data):
                return None
            data = data[key]
        else:
            if not isinstance(data, dict):
                return None
            data = data.get(key)
    return data


def _signal_value(component: object, name: str) -> object:
    """Return a component signal value/text/bool by name."""
    if not isinstance(component, dict):
        return None
    for signal in component.get("signals") or ():
        if not isinstance(signal, dict) or signal.get("name") != name:
            continue
        if signal.get("value") is not None:
            return signal["value"]
        if signal.get("textValue") is not None:
            return signal["textValue"]
        return signal.get("boolValue")
    return None


def _component_serial(components_payload: dict, slot: int) -> str | None:
    """Return the best serial number for a component slot."""
    for kind in ("hvp", "bms", "pch", "baggr"):
        value = _path(components_payload, "components", kind, slot, "serialNumber")
        if isinstance(value, str) and value:
            return value
    return None


def _inferred_expansion_dins(
    components_payload: dict,
    powerwall_count: int,
    existing_expansion_count: int,
    gateway_din: str,
) -> tuple[str, ...]:
    """Infer expansion slots from BMS components when config omits them."""
    bms_items = _path(components_payload, "components", "bms")
    if not isinstance(bms_items, list) or len(bms_items) <= powerwall_count:
        return ()

    expansion_dins: list[str] = []
    for slot in range(powerwall_count, len(bms_items)):
        component = bms_items[slot]
        full = _signal_value(component, "BMS_nominalFullPackEnergy")
        if not isinstance(full, (int, float)) or full <= 0:
            continue
        serial = _component_serial(components_payload, slot)
        expansion_dins.append(
            f"inferred-expansion--{serial}"
            if serial
            else f"{gateway_din}_expansion_{slot}"
        )

    return tuple(expansion_dins[existing_expansion_count:])


def _master_blocks(
    config_payload: dict,
    status_payload: dict,
    components_payload: dict,
    gateway_din: str,
) -> tuple[MasterBlock, ...]:
    """Build per-Powerwall block metadata from config/status payloads.

    Each entry in ``battery_blocks`` is one Powerwall master with its own
    optional ``battery_expansions[]``. Block 0's HA device identifier is
    derived from the gateway DIN for backwards compatibility with existing
    deployments; later blocks prefer their physical DIN when available.
    """
    blocks = [
        block
        for block in (config_payload.get("battery_blocks") or [])
        if isinstance(block, dict)
    ]
    known_dins = {_block_din(block) for block in blocks}
    for din in _status_battery_dins(status_payload):
        if din not in known_dins:
            blocks.append({"din": din})
            known_dins.add(din)

    powerwall_count = len(blocks)
    existing_expansion_count = sum(
        len(block.get("battery_expansions") or []) for block in blocks
    )
    inferred_expansions = _inferred_expansion_dins(
        components_payload,
        powerwall_count,
        existing_expansion_count,
        gateway_din,
    )
    if inferred_expansions:
        if not blocks:
            blocks.append({"din": gateway_din})
            powerwall_count = 1
        first_block = blocks[0]
        existing = list(first_block.get("battery_expansions") or [])
        first_block["battery_expansions"] = [
            *existing,
            *({"din": din} for din in inferred_expansions),
        ]

    next_expansion_slot = powerwall_count
    next_expansion_display_index = 1
    out: list[MasterBlock] = []
    for i, block in enumerate(blocks):
        expansion_dins = tuple(
            expansion["din"]
            for expansion in block.get("battery_expansions") or []
            if isinstance(expansion, dict)
            and isinstance(expansion.get("din"), str)
            and expansion["din"]
        )
        physical_din = _block_din(block)
        device_din = (
            f"{gateway_din}{MASTER_BATTERY_DIN_SUFFIX}"
            if i == 0
            else physical_din or f"{gateway_din}_battery_{i}"
        )
        out.append(
            MasterBlock(
                block_index=i,
                component_slot=i,
                device_din=device_din,
                physical_din=physical_din,
                role="leader" if i == 0 else "follower",
                expansion_dins=expansion_dins,
                first_expansion_slot=next_expansion_slot,
                first_expansion_display_index=next_expansion_display_index,
            )
        )
        next_expansion_slot += len(expansion_dins)
        next_expansion_display_index += len(expansion_dins)
    return tuple(out)


async def async_unload_entry(
    hass: HomeAssistant, entry: PowerwallV1RConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
