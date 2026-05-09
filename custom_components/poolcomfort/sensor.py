from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from poolcomfort_local import PoolState

from .const import DOMAIN
from .coordinator import PoolComfortCoordinator


@dataclass(frozen=True, kw_only=True)
class PoolComfortSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PoolState], float | int | str | None]


SENSOR_DESCRIPTIONS: tuple[PoolComfortSensorDescription, ...] = (
    PoolComfortSensorDescription(
        key="in_water_temp",
        translation_key="in_water_temp",
        name="Water inlet temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.in_water_temp,
    ),
    PoolComfortSensorDescription(
        key="out_water_temp",
        translation_key="out_water_temp",
        name="Water outlet temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.out_water_temp,
    ),
    PoolComfortSensorDescription(
        key="serial",
        name="Serial number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.serial,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolComfortCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(PoolComfortSensor(coordinator, entry, desc) for desc in SENSOR_DESCRIPTIONS)


class PoolComfortSensor(CoordinatorEntity[PoolComfortCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: PoolComfortSensorDescription

    def __init__(
        self,
        coordinator: PoolComfortCoordinator,
        entry: ConfigEntry,
        description: PoolComfortSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        serial = coordinator.data.serial if coordinator.data else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial or entry.entry_id)},
        )

    @property
    def native_value(self) -> float | int | str | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
