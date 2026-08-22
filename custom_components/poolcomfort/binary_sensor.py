from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PoolComfortCoordinator
from .protocol import POOL_WORK_DETAIL_LABELS, PoolDiagnostics


@dataclass(frozen=True, kw_only=True)
class PoolComfortBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[PoolDiagnostics], bool | None]


def working_detail(data: PoolDiagnostics, key: str) -> bool | None:
    decoded = data.attributes.get("0x0015", {}).get("decoded", {})
    details = decoded.get("working_details", {})
    value = details.get(key)
    return value if isinstance(value, bool) else None


BINARY_SENSOR_DESCRIPTIONS: tuple[PoolComfortBinarySensorDescription, ...] = tuple(
    PoolComfortBinarySensorDescription(
        key=key,
        name=label,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, detail_key=key: working_detail(data, detail_key),
    )
    for key, label in POOL_WORK_DETAIL_LABELS.items()
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolComfortCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PoolComfortBinarySensor(coordinator, entry, desc) for desc in BINARY_SENSOR_DESCRIPTIONS
    )
    async_add_entities([PoolComfortConnectionSensor(coordinator, entry)])


class PoolComfortBinarySensor(CoordinatorEntity[PoolComfortCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    entity_description: PoolComfortBinarySensorDescription

    def __init__(
        self,
        coordinator: PoolComfortCoordinator,
        entry: ConfigEntry,
        description: PoolComfortBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        serial = coordinator.data.state.serial if coordinator.data else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial or entry.entry_id)},
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class PoolComfortConnectionSensor(
    CoordinatorEntity[PoolComfortCoordinator], BinarySensorEntity
):
    """Whether the readings you are looking at are actually live.

    Every other entity holds its last value while the pump is unreachable,
    so without this one a wedged pump is indistinguishable from a pump that
    is simply sitting still.  Use it to drive notifications and to guard
    automations that act on water temperature.
    """

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PoolComfortCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connection"
        serial = coordinator.data.state.serial if coordinator.data else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial or entry.entry_id)},
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_connected

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        age = self.coordinator.data_age
        return {
            "data_age_seconds": None if age is None else round(age),
            "consecutive_failures": self.coordinator.consecutive_failures,
            "sessions_opened": self.coordinator.sessions_opened,
            "last_error": self.coordinator.last_error,
        }
