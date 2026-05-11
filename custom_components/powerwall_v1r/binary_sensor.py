"""Binary sensor platform for Powerwall V1R."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import PowerwallRuntimeData, PowerwallV1RConfigEntry


def _path(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, Mapping):
            return None
        data = data.get(key)
    return data


def _bool(*path: str) -> Callable[[dict[str, Any]], bool | None]:
    """Return a value_fn that reads a bool at ``path`` from a status payload."""

    def _fn(data: dict[str, Any]) -> bool | None:
        value = _path(data, *path)
        return value if isinstance(value, bool) else None

    return _fn


def _not_shutdown(data: dict[str, Any]) -> bool | None:
    """Site `is_on` = NOT siteShutdown.isShutDown."""
    value = _path(data, "control", "siteShutdown", "isShutDown")
    return (not value) if isinstance(value, bool) else None


@dataclass(frozen=True, kw_only=True)
class PowerwallV1RBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Powerwall V1R binary sensor."""

    coordinator_attr: str
    value_fn: Callable[[Any], bool | None]


DIAG = EntityCategory.DIAGNOSTIC


_BINARY_SENSORS: tuple[PowerwallV1RBinarySensorDescription, ...] = (
    PowerwallV1RBinarySensorDescription(
        key="grid_ok",
        translation_key="grid_ok",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        coordinator_attr="status",
        value_fn=_bool("control", "islanding", "gridOK"),
    ),
    PowerwallV1RBinarySensorDescription(
        key="microgrid_ok",
        translation_key="microgrid_ok",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_bool("control", "islanding", "microGridOK"),
    ),
    PowerwallV1RBinarySensorDescription(
        key="contactor_closed",
        translation_key="contactor_closed",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_bool("control", "islanding", "contactorClosed"),
    ),
    PowerwallV1RBinarySensorDescription(
        key="site_running",
        translation_key="site_running",
        device_class=BinarySensorDeviceClass.POWER,
        coordinator_attr="status",
        value_fn=_not_shutdown,
    ),
    PowerwallV1RBinarySensorDescription(
        key="sitemanager_running",
        translation_key="sitemanager_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_bool("system", "sitemanagerStatus", "isRunning"),
    ),
    PowerwallV1RBinarySensorDescription(
        key="escan_firmware_updating",
        translation_key="escan_firmware_updating",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_bool("esCan", "firmwareUpdate", "isUpdating"),
    ),
    PowerwallV1RBinarySensorDescription(
        key="pw3can_firmware_updating",
        translation_key="pw3can_firmware_updating",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=DIAG,
        coordinator_attr="status",
        value_fn=_bool("pw3Can", "firmwareUpdate", "isUpdating"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerwallV1RConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Powerwall V1R binary sensors."""
    runtime = entry.runtime_data
    async_add_entities(
        PowerwallV1RBinarySensor(runtime, description)
        for description in _BINARY_SENSORS
    )


class PowerwallV1RBinarySensor(
    CoordinatorEntity[DataUpdateCoordinator[Any]], BinarySensorEntity
):
    """A Powerwall V1R binary sensor bound to a coordinator."""

    _attr_has_entity_name = True
    entity_description: PowerwallV1RBinarySensorDescription

    def __init__(
        self,
        runtime: PowerwallRuntimeData,
        description: PowerwallV1RBinarySensorDescription,
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
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
