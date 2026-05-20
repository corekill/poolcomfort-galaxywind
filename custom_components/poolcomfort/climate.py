from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MAX_TEMP, MIN_TEMP
from .coordinator import PoolComfortCoordinator
from .protocol import Mode

HVAC_MODE_TO_DEVICE: dict[HVACMode, Mode] = {
    HVACMode.AUTO: Mode.AUTO,
    HVACMode.COOL: Mode.COOLING,
    HVACMode.HEAT: Mode.HEATING,
}
DEVICE_TO_HVAC_MODE: dict[Mode, HVACMode] = {
    Mode.AUTO: HVACMode.AUTO,
    Mode.COOLING: HVACMode.COOL,
    Mode.HEATING: HVACMode.HEAT,
    Mode.WARM: HVACMode.HEAT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolComfortCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolComfortClimate(coordinator, entry)])


class PoolComfortClimate(CoordinatorEntity[PoolComfortCoordinator], ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]

    def __init__(self, coordinator: PoolComfortCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_climate"
        serial = coordinator.data.state.serial if coordinator.data else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial or entry.entry_id)},
            name="Pool Comfort heat pump",
            manufacturer="Galaxywind",
            model="Pool Comfort",
            serial_number=serial,
        )

    @property
    def current_temperature(self) -> float | None:
        return self.coordinator.data.state.in_water_temp

    @property
    def target_temperature(self) -> float | None:
        return self.coordinator.data.state.target_temp

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.coordinator.data.state.power:
            return HVACMode.OFF
        return DEVICE_TO_HVAC_MODE.get(self.coordinator.data.state.mode, HVACMode.AUTO)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        new_mode = kwargs.get(ATTR_HVAC_MODE)
        temp = kwargs.get(ATTR_TEMPERATURE)
        if new_mode is not None and new_mode != self.hvac_mode:
            await self.async_set_hvac_mode(new_mode)
        if temp is None:
            return
        target = int(round(float(temp)))

        def action(client) -> None:
            if not self.coordinator.data.state.power and new_mode != HVACMode.OFF:
                client.set_power(True)
            client.set_target_temp(target)

        await self.coordinator.async_apply(action)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_apply(lambda c: c.set_power(False))
            return
        device_mode = HVAC_MODE_TO_DEVICE.get(hvac_mode)
        if device_mode is None:
            return

        def action(client) -> None:
            if not self.coordinator.data.state.power:
                client.set_power(True)
            client.set_mode(int(device_mode))

        await self.coordinator.async_apply(action)

    async def async_turn_on(self) -> None:
        await self.coordinator.async_apply(lambda c: c.set_power(True))

    async def async_turn_off(self) -> None:
        await self.coordinator.async_apply(lambda c: c.set_power(False))
