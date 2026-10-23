"""Numbers for Pixelblaze: device settings and pattern slider/number controls."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ControlType
from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator
from .entity import PixelblazeControlEntity, PixelblazeEntity, async_setup_control_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixelblazeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [PixelblazeMaxBrightnessNumber(coordinator), PixelblazeShuffleTimeNumber(coordinator)]
    )
    async_setup_control_entities(
        hass,
        entry,
        async_add_entities,
        {
            ControlType.SLIDER: PixelblazeSliderNumber,
            ControlType.INPUT_NUMBER: PixelblazeInputNumber,
        },
    )


class PixelblazeMaxBrightnessNumber(PixelblazeEntity, NumberEntity):
    """Global brightness limit (Settings tab)."""

    _attr_translation_key = "max_brightness"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "max_brightness")

    @property
    def native_value(self) -> float | None:
        return self.state_data.max_brightness

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_max_brightness(value)


class PixelblazeShuffleTimeNumber(PixelblazeEntity, NumberEntity):
    """How long each pattern plays in shuffle mode."""

    _attr_translation_key = "shuffle_time"
    _attr_native_min_value = 1
    _attr_native_max_value = 86400
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "shuffle_time")

    @property
    def native_value(self) -> float | None:
        return self.state_data.sequence_timer

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_sequence_timer(value)


class PixelblazeSliderNumber(PixelblazeControlEntity, NumberEntity):
    """A pattern slider (0..1 on the device, shown as a percentage)."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float | None:
        value = self.control_value
        if isinstance(value, (int, float)):
            return round(float(value) * 100, 1)
        return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_controls({self.control: round(value / 100, 4)})


class PixelblazeInputNumber(PixelblazeControlEntity, NumberEntity):
    """A pattern number input. Pixelblaze numbers are 16.16 fixed point."""

    _attr_native_min_value = -32768
    _attr_native_max_value = 32767
    _attr_native_step = 0.001
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        value = self.control_value
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_controls({self.control: value})
