"""Sensor platform for Powerwall V1R.

Entities are split across five coordinators (status, meters, battery SoC,
grid status, config) — each polled on its own cadence. The
``coordinator_attr`` field on a description names the runtime-data field
the entity binds to.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import PowerwallRuntimeData, PowerwallV1RConfigEntry


def _path(data: Any, *keys: Any) -> Any:
    """Walk nested mappings/lists; return None if a step is missing."""
    for key in keys:
        if isinstance(key, int):
            if not isinstance(data, list) or not -len(data) <= key < len(data):
                return None
            data = data[key]
        else:
            if not isinstance(data, Mapping):
                return None
            data = data.get(key)
    return data


@dataclass(frozen=True, kw_only=True)
class PowerwallV1RSensorDescription(SensorEntityDescription):
    """Describes a Powerwall V1R sensor + the coordinator it reads from."""

    coordinator_attr: str
    value_fn: Callable[[Any], StateType]


POWER = SensorDeviceClass.POWER
ENERGY_STORE = SensorDeviceClass.ENERGY_STORAGE
ENERGY = SensorDeviceClass.ENERGY
MEAS = SensorStateClass.MEASUREMENT
TOTAL_INC = SensorStateClass.TOTAL_INCREASING
DIAG = EntityCategory.DIAGNOSTIC


# ── Helpers per coordinator payload ─────────────────────────────────────────


def _status_meter_power(location: str) -> Callable[[dict[str, Any]], StateType]:
    """`control.meterAggregates[location].realPowerW` from get_status."""

    def _fn(status: dict[str, Any]) -> StateType:
        meters = _path(status, "control", "meterAggregates")
        if not isinstance(meters, list):
            return None
        for meter in meters:
            if (
                isinstance(meter, Mapping)
                and str(meter.get("location", "")).upper() == location
            ):
                value = meter.get("realPowerW")
                return float(value) if isinstance(value, (int, float)) else None
        return None

    return _fn


def _percentage_charged(status: dict[str, Any]) -> StateType:
    remaining = _path(status, "control", "systemStatus", "nominalEnergyRemainingWh")
    full = _path(status, "control", "systemStatus", "nominalFullPackEnergyWh")
    if not isinstance(remaining, (int, float)) or not isinstance(full, (int, float)):
        return None
    if not full:
        return None
    return round(float(remaining) / float(full) * 100, 2)


def _system_time(status: dict[str, Any]) -> StateType:
    raw = _path(status, "system", "time")
    if not isinstance(raw, str):
        return None
    return dt_util.parse_datetime(raw)


def _alerts(status: dict[str, Any]) -> StateType:
    alerts = _path(status, "control", "alerts", "active")
    if not isinstance(alerts, list):
        return None
    return ", ".join(str(a) for a in alerts) or "none"


def _islander(field: str) -> Callable[[dict[str, Any]], StateType]:
    def _fn(status: dict[str, Any]) -> StateType:
        return _path(status, "esCan", "bus", "ISLANDER", "ISLAND_AcMeasurements", field)

    return _fn


def _islander_grid_connection(status: dict[str, Any]) -> StateType:
    return _path(
        status,
        "esCan",
        "bus",
        "ISLANDER",
        "ISLAND_GridConnection",
        "ISLAND_GridConnected",
    )


def _sync_meter(meter: str, field: str) -> Callable[[dict[str, Any]], StateType]:
    section = f"METER_{meter}_AcMeasurements"

    def _fn(status: dict[str, Any]) -> StateType:
        return _path(status, "esCan", "bus", "SYNC", section, field)

    return _fn


def _islanding(field: str) -> Callable[[dict[str, Any]], StateType]:
    def _fn(status: dict[str, Any]) -> StateType:
        value = _path(status, "control", "islanding", field)
        if isinstance(value, bool):
            return "on" if value else "off"
        return value

    return _fn


def _meters_field(location: str, field: str) -> Callable[[dict[str, Any]], StateType]:
    """Pull `meters_aggregates[location][field]`."""

    def _fn(data: dict[str, Any]) -> StateType:
        value = _path(data, location, field)
        return value if isinstance(value, (int, float)) else None

    return _fn


def _config_field(*path: str) -> Callable[[dict[str, Any]], StateType]:
    def _fn(cfg: dict[str, Any]) -> StateType:
        return _path(cfg, *path)

    return _fn


# ── Sensor descriptions, grouped by coordinator ─────────────────────────────


_LOCATIONS = ("site", "battery", "load", "solar")


_METERS_AGGREGATE_SENSORS: tuple[PowerwallV1RSensorDescription, ...] = tuple(
    sensor
    for location in _LOCATIONS
    for sensor in (
        PowerwallV1RSensorDescription(
            key=f"{location}_apparent_power",
            translation_key=f"{location}_apparent_power",
            device_class=SensorDeviceClass.APPARENT_POWER,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
            entity_registry_enabled_default=False,
            coordinator_attr="meters",
            value_fn=_meters_field(location, "instant_apparent_power"),
        ),
        PowerwallV1RSensorDescription(
            key=f"{location}_reactive_power",
            translation_key=f"{location}_reactive_power",
            device_class=SensorDeviceClass.REACTIVE_POWER,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            entity_registry_enabled_default=False,
            coordinator_attr="meters",
            value_fn=_meters_field(location, "instant_reactive_power"),
        ),
        PowerwallV1RSensorDescription(
            key=f"{location}_voltage",
            translation_key=f"{location}_voltage",
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            entity_registry_enabled_default=False,
            coordinator_attr="meters",
            value_fn=_meters_field(location, "instant_average_voltage"),
        ),
        PowerwallV1RSensorDescription(
            key=f"{location}_current",
            translation_key=f"{location}_current",
            device_class=SensorDeviceClass.CURRENT,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            entity_registry_enabled_default=False,
            coordinator_attr="meters",
            value_fn=_meters_field(location, "instant_total_current"),
        ),
        PowerwallV1RSensorDescription(
            key=f"{location}_frequency",
            translation_key=f"{location}_frequency",
            device_class=SensorDeviceClass.FREQUENCY,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfFrequency.HERTZ,
            entity_registry_enabled_default=False,
            coordinator_attr="meters",
            value_fn=_meters_field(location, "frequency"),
        ),
        PowerwallV1RSensorDescription(
            key=f"{location}_energy_exported",
            translation_key=f"{location}_energy_exported",
            device_class=ENERGY,
            state_class=TOTAL_INC,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            coordinator_attr="meters",
            value_fn=_meters_field(location, "energy_exported"),
        ),
        PowerwallV1RSensorDescription(
            key=f"{location}_energy_imported",
            translation_key=f"{location}_energy_imported",
            device_class=ENERGY,
            state_class=TOTAL_INC,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            coordinator_attr="meters",
            value_fn=_meters_field(location, "energy_imported"),
        ),
    )
)


_STATUS_SENSORS: tuple[PowerwallV1RSensorDescription, ...] = (
    # Top-level power flows from control.meterAggregates
    PowerwallV1RSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT,
        coordinator_attr="status",
        value_fn=_status_meter_power("BATTERY"),
    ),
    PowerwallV1RSensorDescription(
        key="site_power",
        translation_key="site_power",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT,
        coordinator_attr="status",
        value_fn=_status_meter_power("SITE"),
    ),
    PowerwallV1RSensorDescription(
        key="load_power",
        translation_key="load_power",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT,
        coordinator_attr="status",
        value_fn=_status_meter_power("LOAD"),
    ),
    PowerwallV1RSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT,
        coordinator_attr="status",
        value_fn=_status_meter_power("SOLAR"),
    ),
    PowerwallV1RSensorDescription(
        key="solar_rgm_power",
        translation_key="solar_rgm_power",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
        coordinator_attr="status",
        value_fn=_status_meter_power("SOLAR_RGM"),
    ),
    PowerwallV1RSensorDescription(
        key="generator_power",
        translation_key="generator_power",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
        coordinator_attr="status",
        value_fn=_status_meter_power("GENERATOR"),
    ),
    PowerwallV1RSensorDescription(
        key="conductor_power",
        translation_key="conductor_power",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
        coordinator_attr="status",
        value_fn=_status_meter_power("CONDUCTOR"),
    ),
    # Battery energy (computed SoC, plus raw remaining/full)
    PowerwallV1RSensorDescription(
        key="percentage_charged_computed",
        translation_key="percentage_charged_computed",
        device_class=SensorDeviceClass.BATTERY,
        state_class=MEAS,
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
        coordinator_attr="status",
        value_fn=_percentage_charged,
    ),
    PowerwallV1RSensorDescription(
        key="energy_remaining",
        translation_key="energy_remaining",
        device_class=ENERGY_STORE,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        coordinator_attr="status",
        value_fn=lambda s: _path(
            s, "control", "systemStatus", "nominalEnergyRemainingWh"
        ),
    ),
    PowerwallV1RSensorDescription(
        key="full_pack_energy",
        translation_key="full_pack_energy",
        device_class=ENERGY_STORE,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        coordinator_attr="status",
        value_fn=lambda s: _path(
            s, "control", "systemStatus", "nominalFullPackEnergyWh"
        ),
    ),
    # Islanding / gateway state diagnostics
    PowerwallV1RSensorDescription(
        key="island_mode",
        translation_key="island_mode",
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_islanding("customerIslandMode"),
    ),
    PowerwallV1RSensorDescription(
        key="islander_grid_state",
        translation_key="islander_grid_state",
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_islander("ISLAND_GridState"),
    ),
    PowerwallV1RSensorDescription(
        key="islander_grid_connection",
        translation_key="islander_grid_connection",
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_islander_grid_connection,
    ),
    PowerwallV1RSensorDescription(
        key="active_alerts",
        translation_key="active_alerts",
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_alerts,
    ),
    # ISLANDER per-phase frequency + voltage (Load + Main)
    *(
        PowerwallV1RSensorDescription(
            key=f"island_freq_l{phase}_{side.lower()}",
            translation_key=f"island_freq_l{phase}_{side.lower()}",
            device_class=SensorDeviceClass.FREQUENCY,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfFrequency.HERTZ,
            entity_registry_enabled_default=False,
            coordinator_attr="status",
            value_fn=_islander(f"ISLAND_FreqL{phase}_{side}"),
        )
        for phase in (1, 2, 3)
        for side in ("Load", "Main")
    ),
    *(
        PowerwallV1RSensorDescription(
            key=f"island_voltage_l{phase}n_{side.lower()}",
            translation_key=f"island_voltage_l{phase}n_{side.lower()}",
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            entity_registry_enabled_default=phase == 1,
            coordinator_attr="status",
            value_fn=_islander(f"ISLAND_VL{phase}N_{side}"),
        )
        for phase in (1, 2, 3)
        for side in ("Load", "Main")
    ),
    # SYNC METER_X / METER_Y per-CT measurements
    *(
        sensor
        for meter in ("X", "Y")
        for ct in ("A", "B", "C")
        for sensor in (
            PowerwallV1RSensorDescription(
                key=f"meter_{meter.lower()}_ct{ct.lower()}_real_power",
                translation_key=f"meter_{meter.lower()}_ct{ct.lower()}_real_power",
                device_class=POWER,
                state_class=MEAS,
                native_unit_of_measurement=UnitOfPower.WATT,
                entity_registry_enabled_default=False,
                coordinator_attr="status",
                value_fn=_sync_meter(meter, f"METER_{meter}_CT{ct}_InstRealPower"),
            ),
            PowerwallV1RSensorDescription(
                key=f"meter_{meter.lower()}_ct{ct.lower()}_reactive_power",
                translation_key=f"meter_{meter.lower()}_ct{ct.lower()}_reactive_power",
                device_class=SensorDeviceClass.REACTIVE_POWER,
                state_class=MEAS,
                native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
                entity_registry_enabled_default=False,
                coordinator_attr="status",
                value_fn=_sync_meter(meter, f"METER_{meter}_CT{ct}_InstReactivePower"),
            ),
            PowerwallV1RSensorDescription(
                key=f"meter_{meter.lower()}_ct{ct.lower()}_current",
                translation_key=f"meter_{meter.lower()}_ct{ct.lower()}_current",
                device_class=SensorDeviceClass.CURRENT,
                state_class=MEAS,
                native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                entity_registry_enabled_default=False,
                coordinator_attr="status",
                value_fn=_sync_meter(meter, f"METER_{meter}_CT{ct}_I"),
            ),
        )
    ),
    *(
        PowerwallV1RSensorDescription(
            key=f"meter_{meter.lower()}_vl{phase}n",
            translation_key=f"meter_{meter.lower()}_vl{phase}n",
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=MEAS,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            entity_registry_enabled_default=False,
            coordinator_attr="status",
            value_fn=_sync_meter(meter, f"METER_{meter}_VL{phase}N"),
        )
        for meter in ("X", "Y")
        for phase in (1, 2, 3)
    ),
)


_BATTERY_SOE_SENSORS: tuple[PowerwallV1RSensorDescription, ...] = (
    PowerwallV1RSensorDescription(
        key="battery_soe",
        translation_key="battery_soe",
        device_class=SensorDeviceClass.BATTERY,
        state_class=MEAS,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        coordinator_attr="battery_soe",
        value_fn=lambda v: v if isinstance(v, (int, float)) else None,
    ),
)


_GRID_STATUS_SENSORS: tuple[PowerwallV1RSensorDescription, ...] = (
    PowerwallV1RSensorDescription(
        key="grid_status",
        translation_key="grid_status",
        coordinator_attr="grid_status",
        value_fn=lambda v: v if isinstance(v, str) else None,
    ),
)


_CONFIG_SENSORS: tuple[PowerwallV1RSensorDescription, ...] = (
    PowerwallV1RSensorDescription(
        key="backup_reserve_percent",
        translation_key="backup_reserve_percent",
        state_class=MEAS,
        native_unit_of_measurement=PERCENTAGE,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "backup_reserve_percent"),
    ),
    PowerwallV1RSensorDescription(
        key="net_meter_mode",
        translation_key="net_meter_mode",
        entity_category=DIAG,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "net_meter_mode"),
    ),
    PowerwallV1RSensorDescription(
        key="customer_preferred_export_rule",
        translation_key="customer_preferred_export_rule",
        entity_category=DIAG,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "customer_preferred_export_rule"),
    ),
    PowerwallV1RSensorDescription(
        key="nominal_system_energy_ac",
        translation_key="nominal_system_energy_ac",
        device_class=ENERGY_STORE,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=DIAG,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "nominal_system_energy_ac"),
    ),
    PowerwallV1RSensorDescription(
        key="nominal_system_power_ac",
        translation_key="nominal_system_power_ac",
        device_class=POWER,
        state_class=MEAS,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        entity_category=DIAG,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "nominal_system_power_ac"),
    ),
    PowerwallV1RSensorDescription(
        key="grid_code",
        translation_key="grid_code",
        entity_category=DIAG,
        entity_registry_enabled_default=False,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "grid_code"),
    ),
    PowerwallV1RSensorDescription(
        key="country",
        translation_key="country",
        entity_category=DIAG,
        entity_registry_enabled_default=False,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "country"),
    ),
    PowerwallV1RSensorDescription(
        key="distributor",
        translation_key="distributor",
        entity_category=DIAG,
        entity_registry_enabled_default=False,
        coordinator_attr="config",
        value_fn=_config_field("site_info", "distributor"),
    ),
)


_ALL_SENSORS: tuple[PowerwallV1RSensorDescription, ...] = (
    *_STATUS_SENSORS,
    *_METERS_AGGREGATE_SENSORS,
    *_BATTERY_SOE_SENSORS,
    *_GRID_STATUS_SENSORS,
    *_CONFIG_SENSORS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerwallV1RConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Powerwall V1R sensors."""
    runtime = entry.runtime_data
    async_add_entities(
        PowerwallV1RSensor(runtime, description) for description in _ALL_SENSORS
    )


class PowerwallV1RSensor(CoordinatorEntity[DataUpdateCoordinator[Any]], SensorEntity):
    """A Powerwall V1R sensor bound to one of the per-endpoint coordinators."""

    _attr_has_entity_name = True
    entity_description: PowerwallV1RSensorDescription

    def __init__(
        self,
        runtime: PowerwallRuntimeData,
        description: PowerwallV1RSensorDescription,
    ) -> None:
        coordinator: DataUpdateCoordinator[Any] = getattr(
            runtime, description.coordinator_attr
        )
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{runtime.din}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.din)},
            name=coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=runtime.din,
            sw_version=runtime.firmware_version,
        )

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
