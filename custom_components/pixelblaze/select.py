"""Selects for Pixelblaze: pattern, sequencer mode and preset."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SequencerMode
from .const import SEQUENCER_OFF, SEQUENCER_PLAYLIST, SEQUENCER_SHUFFLE
from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator
from .entity import PixelblazeEntity

PARALLEL_UPDATES = 0

SEQUENCER_OPTIONS = {
    SEQUENCER_OFF: SequencerMode.OFF,
    SEQUENCER_SHUFFLE: SequencerMode.SHUFFLE,
    SEQUENCER_PLAYLIST: SequencerMode.PLAYLIST,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixelblazeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PixelblazePatternSelect(coordinator),
            PixelblazeSequencerSelect(coordinator),
            PixelblazePresetSelect(coordinator),
            PixelblazePlaylistSelect(coordinator),
        ]
    )


class PixelblazePatternSelect(PixelblazeEntity, SelectEntity):
    """The running pattern. Follows the sequencer as it changes patterns."""

    _attr_translation_key = "pattern"

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "pattern")

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.pattern_options())

    @property
    def current_option(self) -> str | None:
        label = self.coordinator.pattern_label(self.state_data.active_pattern_id)
        return label if label in self.coordinator.pattern_options() else None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"pattern_id": self.state_data.active_pattern_id}

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_select_pattern(self.coordinator.resolve_pattern(option))


class PixelblazeSequencerSelect(PixelblazeEntity, SelectEntity):
    """Sequencer mode: off, shuffle all, or playlist."""

    _attr_translation_key = "sequencer_mode"
    _attr_options = list(SEQUENCER_OPTIONS)

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "sequencer_mode")

    @property
    def current_option(self) -> str:
        return self.state_data.sequencer_mode.name.lower()

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_sequencer_mode(SEQUENCER_OPTIONS[option])


class PixelblazePresetSelect(PixelblazeEntity, SelectEntity):
    """Saved presets.

    Shows the preset that matches what the device is doing right now (or
    nothing, if no preset matches), and recalls a preset when one is picked.
    """

    _attr_translation_key = "preset"

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "preset")

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.presets)

    @property
    def current_option(self) -> str | None:
        return self.coordinator.matching_preset()

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_recall_preset(option)


class PixelblazePlaylistSelect(PixelblazeEntity, SelectEntity):
    """Playlists saved in Home Assistant.

    Shows the playlist the device is playing (or nothing, if it's playing one
    that wasn't saved here); picking one loads it into the device and plays it.
    """

    _attr_translation_key = "playlist"

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "playlist")

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.playlists)

    @property
    def current_option(self) -> str | None:
        if self.state_data.sequencer_mode != SequencerMode.PLAYLIST:
            return None
        return self.coordinator.matching_playlist()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self.state_data
        names = state.patterns
        return {
            "device_playlist": [
                {"pattern": names.get(item["id"], item["id"]), "seconds": item["ms"] / 1000}
                for item in state.playlist_items or []
            ],
            "position": state.playlist.get("position"),
        }

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_play_playlist(option)
