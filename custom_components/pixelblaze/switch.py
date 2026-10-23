"""Switches for Pixelblaze: sequencer play/pause and pattern toggle controls."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities([PixelblazeSequencerSwitch(entry.runtime_data)])
    async_setup_control_entities(
        hass, entry, async_add_entities, {ControlType.TOGGLE: PixelblazeToggleSwitch}
    )


class PixelblazeSequencerSwitch(PixelblazeEntity, SwitchEntity):
    """Whether the sequencer is playing."""

    _attr_translation_key = "sequencer"

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "sequencer_running")

    @property
    def is_on(self) -> bool:
        return self.state_data.sequencer_running

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_sequencer_running(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_sequencer_running(False)


class PixelblazeToggleSwitch(PixelblazeControlEntity, SwitchEntity):
    """A pattern's toggle control."""

    @property
    def is_on(self) -> bool | None:
        value = self.control_value
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_controls({self.control: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_controls({self.control: 0})
