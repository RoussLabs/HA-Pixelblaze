"""Buttons for Pixelblaze."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SequencerMode
from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator
from .entity import PixelblazeEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PixelblazeButtonDescription(ButtonEntityDescription):
    press: Callable[[PixelblazeCoordinator], Awaitable[None]]
    available: Callable[[PixelblazeCoordinator], bool] = lambda _: True


BUTTONS = (
    PixelblazeButtonDescription(
        key="next_pattern",
        translation_key="next_pattern",
        press=lambda c: c.async_next_pattern(),
    ),
    PixelblazeButtonDescription(
        key="previous_pattern",
        translation_key="previous_pattern",
        press=lambda c: c.async_previous_pattern(),
        available=lambda c: c.client.state.sequencer_mode == SequencerMode.PLAYLIST,
    ),
    PixelblazeButtonDescription(
        key="random_pattern",
        translation_key="random_pattern",
        press=lambda c: c.async_random_pattern(),
    ),
    PixelblazeButtonDescription(
        key="refresh_patterns",
        translation_key="refresh_patterns",
        press=lambda c: c.async_refresh_patterns(),
    ),
    PixelblazeButtonDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        press=lambda c: c.async_reboot(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixelblazeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(PixelblazeButton(coordinator, desc) for desc in BUTTONS)


class PixelblazeButton(PixelblazeEntity, ButtonEntity):
    entity_description: PixelblazeButtonDescription

    def __init__(
        self, coordinator: PixelblazeCoordinator, description: PixelblazeButtonDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available(self.coordinator)

    async def async_press(self) -> None:
        await self.entity_description.press(self.coordinator)
