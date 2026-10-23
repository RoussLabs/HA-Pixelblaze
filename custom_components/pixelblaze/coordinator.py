"""Coordinator that keeps Home Assistant in sync with a Pixelblaze.

State reaches Home Assistant two ways:

* **Push.** The device announces every pattern change made by its sequencer,
  and answers each of our commands with its new state. Those messages update
  entities immediately.
* **Poll.** Changes made from the Pixelblaze web UI or another client (a
  brightness drag, a slider tweak, a pattern click) are not broadcast, so the
  full config is re-read every ``scan_interval`` seconds. The same poll
  re-opens the connection if it dropped.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Awaitable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PixelblazeClient,
    PixelblazeConnectionError,
    PixelblazeError,
    PixelblazeState,
    PixelblazeTimeoutError,
    SequencerMode,
    control_type,
)
from .const import (
    CONF_ITEMS,
    CONF_SAVE_TO_FLASH,
    CONF_SCAN_INTERVAL,
    DEFAULT_SAVE_TO_FLASH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FLASH_SAVE_DELAY,
    PATTERN_REFRESH_INTERVAL,
    PLAYLIST_REFRESH_INTERVAL,
    SIGNAL_NEW_CONTROLS,
    STORAGE_VERSION,
    SUBENTRY_PLAYLIST,
)

_LOGGER = logging.getLogger(__name__)

type PixelblazeConfigEntry = ConfigEntry[PixelblazeCoordinator]

_TOLERANCE = 0.005


def values_close(a: Any, b: Any) -> bool:
    """Compare control values, allowing for float rounding on the device."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_close(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= _TOLERANCE
    return a == b


class PixelblazeCoordinator(DataUpdateCoordinator[PixelblazeState]):
    """Owns the connection, the device state, and the saved presets."""

    config_entry: PixelblazeConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: PixelblazeConfigEntry, client: PixelblazeClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"Pixelblaze {entry.data[CONF_HOST]}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.presets: dict[str, dict[str, Any]] = {}
        self.known_controls: dict[str, str] = {}
        self.last_brightness: float = 1.0
        self._dirty: set[str] = set()
        self._cancel_flash_save: CALLBACK_TYPE | None = None
        self._refresh_patterns = True
        self._playlist_supported = True
        self._pattern_list_failing = False
        self._stored_patterns: dict[str, str] = {}
        self._playlist_stale = True
        self._playlist_read = 0.0
        self.loaded_options: dict[str, Any] = {}
        self._pushed_pattern: str | None = None
        self._unsubs = [
            client.add_listener(self._handle_push),
            client.add_disconnect_listener(self._handle_disconnect),
        ]

    # ------------------------------------------------------------------
    # Lifecycle

    @property
    def save_to_flash(self) -> bool:
        return self.config_entry.options.get(CONF_SAVE_TO_FLASH, DEFAULT_SAVE_TO_FLASH)

    async def _async_setup(self) -> None:
        stored = await self.store.async_load() or {}
        self.presets = stored.get("presets", {})
        self.known_controls = stored.get("controls", {})
        self.last_brightness = stored.get("last_brightness", 1.0)
        # Last known pattern list, so a slow or silent listPrograms at startup
        # doesn't leave the pattern entities empty. Re-read on the first poll.
        if not self.client.state.patterns:
            self.client.state.patterns = dict(stored.get("patterns", {}))
        self._stored_patterns = dict(self.client.state.patterns)

    async def async_shutdown(self) -> None:
        """Write pending changes and close the connection."""
        if self._cancel_flash_save is not None:
            self._cancel_flash_save()
            self._cancel_flash_save = None
            if self.client.connected:
                try:
                    await self._async_flush_to_flash()
                except HomeAssistantError:
                    pass
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await super().async_shutdown()
        await self.client.disconnect()
        await self.store.async_save(self._data_to_store())

    async def _async_update_data(self) -> PixelblazeState:
        client = self.client
        try:
            await client.refresh_config()
            stale = time.monotonic() - client.state.patterns_updated > PATTERN_REFRESH_INTERVAL
            if self._refresh_patterns or stale or not client.state.patterns:
                await self._async_refresh_pattern_list()
            if self._playlist_due():
                self._playlist_read = time.monotonic()
                self._playlist_stale = False
                try:
                    await client.get_playlist()
                except PixelblazeTimeoutError:
                    # Older firmware doesn't answer getPlaylist; stop asking.
                    _LOGGER.debug("Pixelblaze %s doesn't report its playlist", client.host)
                    self._playlist_supported = False
        except PixelblazeError as err:
            await client.disconnect()
            raise UpdateFailed(str(err)) from err
        self._track_state()
        return client.state

    def _playlist_due(self) -> bool:
        """Whether to read the device's playlist on this poll.

        Never when no playlists are saved in HA (nothing to match against);
        otherwise at startup, after playlists change, and every few minutes.
        """
        if not self._playlist_supported or not self.playlists:
            return False
        return (
            self._playlist_stale
            or time.monotonic() - self._playlist_read > PLAYLIST_REFRESH_INTERVAL
        )

    @callback
    def async_playlists_changed(self) -> None:
        """Saved playlists were added, edited or deleted."""
        self._playlist_stale = True
        self.async_update_listeners()

    async def _async_refresh_pattern_list(self) -> None:
        """Re-read the pattern list without letting a slow reply fail the update.

        Everything else keeps working with the last known list; the next poll
        tries again, and a reply that turns up late is still used.
        """
        try:
            await self.client.refresh_patterns()
        except PixelblazeTimeoutError as err:
            self._refresh_patterns = True
            if not self._pattern_list_failing:
                _LOGGER.warning(
                    "%s; using the last known list of %d patterns and retrying",
                    err,
                    len(self.client.state.patterns),
                )
            self._pattern_list_failing = True
            return
        self._refresh_patterns = False
        if self._pattern_list_failing:
            _LOGGER.info("Pixelblaze %s pattern list received", self.client.host)
        self._pattern_list_failing = False
        if self.client.state.patterns != self._stored_patterns:
            self._stored_patterns = dict(self.client.state.patterns)
            self.store.async_delay_save(self._data_to_store, 10)

    @callback
    def _handle_push(self) -> None:
        """The device told us something changed (e.g. the sequencer moved on)."""
        state = self.client.state
        if self._pattern_list_failing and state.patterns_updated:
            # The pattern list we gave up waiting for arrived after all.
            _LOGGER.info("Pixelblaze %s pattern list received (late)", self.client.host)
            self._pattern_list_failing = False
            self._refresh_patterns = False
            self._stored_patterns = dict(state.patterns)
            self.store.async_delay_save(self._data_to_store, 10)
        pid = state.active_pattern_id
        if pid and pid not in state.patterns:
            # A pattern we've never seen: probably newly saved. Re-list soon.
            self._refresh_patterns = True
        if pid and pid != self._pushed_pattern and not state.controls:
            # The documented push format carries "controls":{}, so ask for the
            # new pattern's control values with getControls.
            self.config_entry.async_create_background_task(
                self.hass, self._async_fetch_controls(pid), "pixelblaze_fetch_controls"
            )
        self._pushed_pattern = pid
        self._track_state()
        self.async_set_updated_data(state)

    async def _async_fetch_controls(self, pattern_id: str) -> None:
        try:
            controls = await self.client.get_controls(pattern_id)
        except PixelblazeError as err:
            _LOGGER.debug("getControls for %s failed: %s", pattern_id, err)
            return
        state = self.client.state
        if not controls or state.active_pattern_id != pattern_id or state.controls:
            return
        state.sequencer["activeProgram"] = {**state.active_program, "controls": controls}
        self._track_state()
        self.async_set_updated_data(state)

    @callback
    def _handle_disconnect(self) -> None:
        self.async_set_update_error(PixelblazeConnectionError("Connection lost"))

    @callback
    def _track_state(self) -> None:
        """Remember things worth keeping across restarts."""
        state = self.client.state
        changed = False
        if state.brightness and abs(state.brightness - self.last_brightness) > 1e-6:
            self.last_brightness = state.brightness
            changed = True

        new_controls = []
        for name in state.controls:
            ctype = control_type(name)
            if ctype is not None and name not in self.known_controls:
                self.known_controls[name] = ctype.value
                new_controls.append(name)
        if new_controls:
            changed = True
            async_dispatcher_send(
                self.hass,
                SIGNAL_NEW_CONTROLS.format(self.config_entry.entry_id),
                new_controls,
            )
        if changed:
            self.store.async_delay_save(self._data_to_store, 10)

    def _data_to_store(self) -> dict[str, Any]:
        return {
            "presets": self.presets,
            "controls": self.known_controls,
            "last_brightness": self.last_brightness,
            "patterns": self.client.state.patterns,
        }

    # ------------------------------------------------------------------
    # Command plumbing

    async def _command(self, action: Awaitable[Any], *dirty: str) -> None:
        """Run a client command, then publish the (optimistic) new state."""
        try:
            await action
        except PixelblazeError as err:
            raise HomeAssistantError(
                f"Error communicating with Pixelblaze {self.client.host}: {err}"
            ) from err
        self._mark_dirty(*dirty)
        self._track_state()
        self.async_set_updated_data(self.client.state)

    @callback
    def _mark_dirty(self, *kinds: str) -> None:
        """Schedule slider-style changes to be written to flash once they settle."""
        if not kinds or not self.save_to_flash:
            return
        self._dirty.update(kinds)
        if self._cancel_flash_save is not None:
            self._cancel_flash_save()
        self._cancel_flash_save = async_call_later(
            self.hass,
            FLASH_SAVE_DELAY,
            HassJob(self._async_flush_to_flash, cancel_on_shutdown=True),
        )

    async def _async_flush_to_flash(self, _now: Any = None) -> None:
        self._cancel_flash_save = None
        dirty, self._dirty = self._dirty, set()
        state = self.client.state
        try:
            if "brightness" in dirty and state.brightness is not None:
                await self.client.set_brightness(state.brightness, save=True)
            if "controls" in dirty and state.controls:
                await self.client.set_controls(dict(state.controls), save=True)
        except PixelblazeError as err:
            _LOGGER.warning(
                "Could not save Pixelblaze %s settings to flash: %s", self.client.host, err
            )

    # ------------------------------------------------------------------
    # Patterns

    def pattern_options(self) -> dict[str, str]:
        """Display name -> pattern id, sorted, with duplicate names made unique."""
        patterns = self.client.state.patterns
        counts: dict[str, int] = {}
        for name in patterns.values():
            counts[name] = counts.get(name, 0) + 1
        options = {}
        for pid, name in sorted(patterns.items(), key=lambda kv: (kv[1].casefold(), kv[0])):
            label = name if counts[name] == 1 else f"{name} ({pid[:6]})"
            options[label] = pid
        return options

    def pattern_label(self, pattern_id: str | None) -> str | None:
        if pattern_id is None:
            return None
        for label, pid in self.pattern_options().items():
            if pid == pattern_id:
                return label
        return self.client.state.active_pattern_name

    def resolve_pattern(self, name_or_id: str) -> str:
        """Find a pattern id from a display name, plain name or id."""
        patterns = self.client.state.patterns
        if name_or_id in patterns:
            return name_or_id
        options = self.pattern_options()
        if name_or_id in options:
            return options[name_or_id]
        folded = name_or_id.casefold()
        for pid, name in patterns.items():
            if name.casefold() == folded:
                return pid
        raise ServiceValidationError(
            f"Pixelblaze {self.client.host} has no pattern named '{name_or_id}'"
        )

    async def async_select_pattern(self, pattern_id: str) -> None:
        """Play one pattern, pausing the sequencer so it stays put."""
        save = self.save_to_flash

        async def run() -> None:
            if self.client.state.sequencer_running:
                await self.client.set_sequencer_running(False, save=save)
            await self.client.set_active_pattern(pattern_id)

        await self._command(run())

    async def async_random_pattern(self) -> None:
        choices = [
            p for p in self.client.state.patterns if p != self.client.state.active_pattern_id
        ]
        if not choices:
            raise HomeAssistantError("No other patterns to choose from")
        await self.async_select_pattern(random.choice(choices))

    async def async_refresh_patterns(self) -> None:
        await self._command(self.client.refresh_patterns())

    # ------------------------------------------------------------------
    # Brightness

    async def async_set_brightness(self, brightness: float) -> None:
        await self._command(self.client.set_brightness(brightness), "brightness")

    async def async_turn_on(self, brightness: float | None = None) -> None:
        if brightness is None:
            current = self.client.state.brightness
            brightness = current if current else self.last_brightness or 1.0
        await self.async_set_brightness(brightness)

    async def async_turn_off(self) -> None:
        await self.async_set_brightness(0)

    async def async_set_max_brightness(self, percent: float) -> None:
        await self._command(self.client.set_max_brightness(percent, save=self.save_to_flash))

    # ------------------------------------------------------------------
    # Controls

    async def async_set_controls(self, controls: dict[str, Any]) -> None:
        await self._command(self.client.set_controls(controls), "controls")

    async def async_set_variables(self, variables: dict[str, Any]) -> None:
        await self._command(self.client.set_variables(variables))

    # ------------------------------------------------------------------
    # Sequencer

    async def async_set_sequencer_mode(self, mode: SequencerMode) -> None:
        await self._command(self.client.set_sequencer_mode(mode, save=self.save_to_flash))

    async def async_set_sequencer_running(self, running: bool) -> None:
        if running and self.client.state.sequencer_mode == SequencerMode.OFF:
            # Playing with no mode selected means "shuffle", as in the web UI.
            await self.async_set_sequencer_mode(SequencerMode.SHUFFLE)
            return
        await self._command(self.client.set_sequencer_running(running, save=self.save_to_flash))

    async def async_next_pattern(self) -> None:
        await self._command(self.client.next_pattern())

    async def async_previous_pattern(self) -> None:
        state = self.client.state
        if state.sequencer_mode != SequencerMode.PLAYLIST:
            raise ServiceValidationError("Previous pattern is only available in playlist mode")

        async def run() -> None:
            playlist = await self.client.get_playlist()
            count = len(playlist.get("items") or [])
            if not count:
                return
            position = int(state.playlist.get("position", playlist.get("position", 0)))
            await self.client.set_playlist_position((position - 1) % count)

        await self._command(run())

    async def async_set_sequence_timer(self, seconds: float) -> None:
        await self._command(self.client.set_sequence_timer(seconds, save=self.save_to_flash))

    async def async_reboot(self) -> None:
        await self._command(self.client.reboot())

    # ------------------------------------------------------------------
    # Presets

    # ------------------------------------------------------------------
    # Playlists (saved as config subentries; played through the device's
    # single on-board playlist)

    @property
    def playlists(self) -> dict[str, list[dict[str, Any]]]:
        """Saved playlists, by name: [{"id", "name", "ms"}, ...]."""
        found = {
            subentry.title: list(subentry.data.get(CONF_ITEMS) or [])
            for subentry in self.config_entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_PLAYLIST
        }
        return dict(sorted(found.items(), key=lambda kv: kv[0].casefold()))

    def _playable(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The items whose patterns are still on the device."""
        patterns = self.client.state.patterns
        return [item for item in items if item.get("id") in patterns]

    def matching_playlist(self) -> str | None:
        """The saved playlist that the device's playlist currently holds."""
        current = self.client.state.playlist_items
        if not current:
            return None
        wanted = [(item["id"], int(item["ms"])) for item in current]
        for name, items in self.playlists.items():
            playable = self._playable(items)
            if [(item["id"], int(item["ms"])) for item in playable] == wanted:
                return name
        return None

    async def async_play_playlist(self, name: str) -> None:
        """Load a saved playlist into the device and start it from the top."""
        items = self.playlists.get(name)
        if items is None:
            raise ServiceValidationError(f"No playlist named '{name}'")
        playable = self._playable(items)
        if not playable:
            raise ServiceValidationError(
                f"None of the patterns in playlist '{name}' are on the device any more"
            )
        if len(playable) < len(items):
            _LOGGER.warning(
                "Playlist '%s': skipping %d pattern(s) no longer on Pixelblaze %s",
                name,
                len(items) - len(playable),
                self.client.host,
            )
        client = self.client
        save = self.save_to_flash

        async def run() -> None:
            await client.set_playlist(playable, save=save)
            await client.set_sequencer_mode(SequencerMode.PLAYLIST, save=save)
            await client.set_playlist_position(0)

        await self._command(run())

    def capture_preset(self, include_brightness: bool) -> dict[str, Any]:
        state = self.client.state
        preset: dict[str, Any] = {
            "pattern_id": state.active_pattern_id,
            "pattern_name": state.active_pattern_name,
            "controls": dict(state.controls),
            "sequencer_mode": state.sequencer_mode.name.lower(),
            "sequencer_running": state.sequencer_running,
        }
        if state.sequencer_mode == SequencerMode.PLAYLIST and (
            playlist := self.matching_playlist()
        ):
            preset["playlist"] = playlist
        if include_brightness and state.brightness is not None:
            preset["brightness"] = state.brightness
        return preset

    async def async_save_preset(self, name: str, include_brightness: bool = False) -> None:
        name = name.strip()
        if not name:
            raise ServiceValidationError("A preset needs a name")
        self.presets[name] = self.capture_preset(include_brightness)
        await self.store.async_save(self._data_to_store())
        self.async_update_listeners()

    async def async_delete_preset(self, name: str) -> None:
        if self.presets.pop(name, None) is None:
            raise ServiceValidationError(f"No preset named '{name}'")
        await self.store.async_save(self._data_to_store())
        self.async_update_listeners()

    async def async_recall_preset(self, name: str) -> None:
        preset = self.presets.get(name)
        if preset is None:
            raise ServiceValidationError(f"No preset named '{name}'")
        client = self.client
        save = self.save_to_flash
        mode = SequencerMode[preset.get("sequencer_mode", "off").upper()]
        use_sequencer = bool(preset.get("sequencer_running")) and mode != SequencerMode.OFF
        pattern_id = preset.get("pattern_id")
        if not use_sequencer and (not pattern_id or pattern_id not in client.state.patterns):
            raise ServiceValidationError(
                f"Preset '{name}' uses pattern '{preset.get('pattern_name')}', "
                "which is no longer on the device"
            )

        playlist = preset.get("playlist")
        playlist_items = self._playable(self.playlists.get(playlist) or [])
        if use_sequencer and mode == SequencerMode.PLAYLIST and playlist and not playlist_items:
            raise ServiceValidationError(
                f"Preset '{name}' uses playlist '{playlist}', which no longer exists"
            )

        async def run() -> None:
            if use_sequencer:
                if mode == SequencerMode.PLAYLIST and playlist_items:
                    await client.set_playlist(playlist_items, save=save)
                await client.set_sequencer_mode(mode, save=save)
            else:
                if client.state.sequencer_running:
                    await client.set_sequencer_running(False, save=save)
                if client.state.active_pattern_id != pattern_id:
                    await client.set_active_pattern(pattern_id)
                if preset.get("controls"):
                    await client.set_controls(preset["controls"])
            if preset.get("brightness") is not None:
                await client.set_brightness(preset["brightness"])

        await self._command(run(), "controls", "brightness")

    def matching_preset(self) -> str | None:
        """Name of the first preset that describes what the device is doing now."""
        for name, preset in self.presets.items():
            if self._preset_matches(preset):
                return name
        return None

    def _preset_matches(self, preset: dict[str, Any]) -> bool:
        state = self.client.state
        brightness = preset.get("brightness")
        if brightness is not None and (
            state.brightness is None or not values_close(brightness, state.brightness)
        ):
            return False
        mode = SequencerMode[preset.get("sequencer_mode", "off").upper()]
        if preset.get("sequencer_running") and mode != SequencerMode.OFF:
            if preset.get("playlist") and preset["playlist"] != self.matching_playlist():
                return False
            return state.sequencer_running and state.sequencer_mode == mode
        if state.sequencer_running or state.active_pattern_id != preset.get("pattern_id"):
            return False
        current = state.controls
        return all(
            values_close(value, current[key])
            for key, value in (preset.get("controls") or {}).items()
            if key in current
        )
