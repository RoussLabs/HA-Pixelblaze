"""Base entities for Pixelblaze."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ControlType, control_label
from .const import (
    CONF_CONTROL_ENTITIES,
    DEFAULT_CONTROL_ENTITIES,
    DOMAIN,
    MANUFACTURER,
    SIGNAL_NEW_CONTROLS,
)
from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator


class PixelblazeEntity(CoordinatorEntity[PixelblazeCoordinator]):
    """An entity belonging to one Pixelblaze device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PixelblazeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        device_id = entry.unique_id or entry.entry_id
        state = coordinator.client.state
        host = entry.data[CONF_HOST]
        self._attr_unique_id = f"{device_id}_{key}"
        model = "Pixelblaze"
        if state.version:
            model = f"Pixelblaze v{state.version.split('.')[0]}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=state.name or entry.title or host,
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=state.version,
            configuration_url=f"http://{host}",
        )

    @property
    def state_data(self):
        return self.coordinator.client.state


class PixelblazeControlEntity(PixelblazeEntity):
    """An entity for one UI control exported by a pattern.

    Controls belong to patterns, so the entity is only available while the
    running pattern has a control with this name. They sit in the device
    page's Configuration card, keeping the Controls card for the main controls.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PixelblazeCoordinator, control: str) -> None:
        super().__init__(coordinator, f"control_{control}")
        self.control = control
        self._attr_name = control_label(control)

    @property
    def available(self) -> bool:
        return super().available and self.control in self.state_data.controls

    @property
    def control_value(self):
        return self.state_data.controls.get(self.control)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"control": self.control}


@callback
def async_setup_control_entities(
    hass: HomeAssistant,
    entry: PixelblazeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factories: dict[ControlType, Callable[[PixelblazeCoordinator, str], Entity]],
) -> None:
    """Create entities for known pattern controls, and for new ones as they appear."""
    if not entry.options.get(CONF_CONTROL_ENTITIES, DEFAULT_CONTROL_ENTITIES):
        return
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def add(names: list[str]) -> None:
        entities = []
        for name in names:
            ctype = coordinator.known_controls.get(name)
            if name in added or ctype is None:
                continue
            factory = factories.get(ControlType(ctype))
            if factory is not None:
                added.add(name)
                entities.append(factory(coordinator, name))
        if entities:
            async_add_entities(entities)

    add(list(coordinator.known_controls))
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_CONTROLS.format(entry.entry_id), add)
    )
