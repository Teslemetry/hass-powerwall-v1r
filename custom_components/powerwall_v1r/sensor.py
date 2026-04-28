"""Sensor platform for Powerwall V1R."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_ENERGY_SITE_ID, DOMAIN


@dataclass(frozen=True, kw_only=True)
class PowerwallV1RSensorDescription(SensorEntityDescription):
    """Describes a Powerwall V1R sensor sourced from a Teslemetry entity."""

    source_suffix: str


SENSORS: tuple[PowerwallV1RSensorDescription, ...] = (
    PowerwallV1RSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        source_suffix="battery_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PowerwallV1RSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        source_suffix="solar_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PowerwallV1RSensorDescription(
        key="load_power",
        translation_key="load_power",
        source_suffix="load_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PowerwallV1RSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        source_suffix="grid_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PowerwallV1RSensorDescription(
        key="percentage_charged",
        translation_key="percentage_charged",
        source_suffix="percentage_charged",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    PowerwallV1RSensorDescription(
        key="energy_left",
        translation_key="energy_left",
        source_suffix="energy_left",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    ),
    PowerwallV1RSensorDescription(
        key="total_pack_energy",
        translation_key="total_pack_energy",
        source_suffix="total_pack_energy",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Powerwall V1R sensors."""
    site_id = entry.data[CONF_ENERGY_SITE_ID]
    async_add_entities(
        PowerwallV1RSensor(entry, site_id, description) for description in SENSORS
    )


class PowerwallV1RSensor(SensorEntity):
    """A Powerwall V1R sensor mirroring a Teslemetry energy site sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        site_id: str,
        description: PowerwallV1RSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._source_entity_id = (
            f"sensor.energy_site_{site_id}_{description.source_suffix}"
        )
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Powerwall V1R ({site_id})",
            manufacturer="Teslemetry",
            model="Powerwall V1R",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to source entity state changes."""
        self._update_from_source()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._handle_source_event
            )
        )

    @callback
    def _handle_source_event(self, _event) -> None:
        self._update_from_source()
        self.async_write_ha_state()

    @callback
    def _update_from_source(self) -> None:
        state = self.hass.states.get(self._source_entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            self._attr_available = False
            self._attr_native_value = None
            return
        self._attr_available = True
        try:
            self._attr_native_value = float(state.state)
        except (TypeError, ValueError):
            self._attr_native_value = state.state
