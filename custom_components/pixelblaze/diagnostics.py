"""Diagnostics for Pixelblaze."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import PixelblazeConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PixelblazeConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    state = coordinator.client.state
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "connected": coordinator.client.connected,
        "settings": state.settings,
        "sequencer": state.sequencer,
        "stats": state.stats,
        "pattern_count": len(state.patterns),
        "known_controls": coordinator.known_controls,
        "presets": coordinator.presets,
    }
