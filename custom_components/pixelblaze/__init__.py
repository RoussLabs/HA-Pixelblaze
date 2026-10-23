"""The Pixelblaze integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PixelblazeClient
from .const import DOMAIN
from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator

PLATFORMS = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: PixelblazeConfigEntry) -> bool:
    """Connect to a Pixelblaze and set up its entities."""
    client = PixelblazeClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    coordinator = PixelblazeCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.disconnect()
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_remove_stale_devices(hass, entry)
    coordinator.loaded_options = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _device_identifier(entry: PixelblazeConfigEntry) -> tuple[str, str]:
    return (DOMAIN, entry.unique_id or entry.entry_id)


@callback
def _async_remove_stale_devices(hass: HomeAssistant, entry: PixelblazeConfigEntry) -> None:
    """Remove devices this entry no longer uses.

    Each Pixelblaze is one device. An early build briefly put pattern controls
    on a second "pattern controls" device; Home Assistant keeps a device until
    it's removed, so clean it (or anything like it) up.
    """
    registry = dr.async_get(hass)
    keep = _device_identifier(entry)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if keep not in device.identifiers:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PixelblazeConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting any device except the Pixelblaze itself from its page."""
    return _device_identifier(entry) not in device.identifiers


async def async_unload_entry(hass: HomeAssistant, entry: PixelblazeConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: PixelblazeConfigEntry) -> None:
    """Reload for new options; for playlist (subentry) changes, just refresh entities."""
    coordinator = entry.runtime_data
    if dict(entry.options) != coordinator.loaded_options:
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        coordinator.async_playlists_changed()
