"""Number platform for Powerwall V1R."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PowerwallV1RConfigEntry
from .entity import PowerwallV1REntity, config_path


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerwallV1RConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Powerwall V1R number entities."""
    runtime = entry.runtime_data
    async_add_entities([BackupReserveNumber(runtime)])


class BackupReserveNumber(PowerwallV1REntity, NumberEntity):
    """Backup reserve percentage."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, runtime) -> None:
        super().__init__(
            runtime,
            runtime.config,
            NumberEntityDescription(
                key="backup_reserve_percent",
                translation_key="backup_reserve_percent",
            ),
        )

    @property
    def native_value(self) -> float | None:
        value = config_path(
            self.coordinator.data, "site_info", "backup_reserve_percent"
        )
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime.client.write_config(
            {"site_info.backup_reserve_percent": int(value)}
        )
        await self.coordinator.async_request_refresh()
