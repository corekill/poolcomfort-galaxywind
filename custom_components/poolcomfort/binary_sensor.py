from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
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
