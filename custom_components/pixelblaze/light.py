"""Lights for Pixelblaze.

* The main light is the device itself: brightness is the Pixelblaze brightness
  slider, "off" is brightness 0, the effect is the running pattern, and the
  color drives the pattern's first color picker (if it has one).
* Each ``hsvPicker``/``rgbPicker`` control also gets its own light.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import color as color_util

from .api import ControlType, SequencerMode, control_type
from .const import (
    ATTR_CONTROLS,
    ATTR_INCLUDE_BRIGHTNESS,
    ATTR_PATTERN,
    ATTR_PLAYLIST,
    ATTR_PRESET,
    ATTR_VARIABLES,
    EFFECT_PLAYLIST,
    EFFECT_SHUFFLE,
    SERVICE_DELETE_PRESET,
    SERVICE_PLAY_PLAYLIST,
    SERVICE_RECALL_PRESET,
    SERVICE_SAVE_PRESET,
    SERVICE_SET_CONTROLS,
    SERVICE_SET_PATTERN,
    SERVICE_SET_VARIABLES,
)
from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator
from .entity import PixelblazeControlEntity, PixelblazeEntity, async_setup_control_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixelblazeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([PixelblazeLight(coordinator)])
    async_setup_control_entities(
        hass,
        entry,
        async_add_entities,
        {
            ControlType.HSV_PICKER: PixelblazeColorControlLight,
            ControlType.RGB_PICKER: PixelblazeColorControlLight,
        },
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SAVE_PRESET,
        {
            vol.Required(ATTR_PRESET): cv.string,
            vol.Optional(ATTR_INCLUDE_BRIGHTNESS, default=False): cv.boolean,
        },
        "async_save_preset",
    )
    platform.async_register_entity_service(
        SERVICE_RECALL_PRESET, {vol.Required(ATTR_PRESET): cv.string}, "async_recall_preset"
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_PRESET, {vol.Required(ATTR_PRESET): cv.string}, "async_delete_preset"
    )
    platform.async_register_entity_service(
        SERVICE_SET_PATTERN,
        {
            vol.Required(ATTR_PATTERN): cv.string,
            vol.Optional(ATTR_CONTROLS): dict,
        },
        "async_set_pattern",
    )
    platform.async_register_entity_service(
        SERVICE_PLAY_PLAYLIST, {vol.Required(ATTR_PLAYLIST): cv.string}, "async_play_playlist"
    )
    platform.async_register_entity_service(
        SERVICE_SET_CONTROLS, {vol.Required(ATTR_CONTROLS): dict}, "async_set_controls"
    )
    platform.async_register_entity_service(
        SERVICE_SET_VARIABLES, {vol.Required(ATTR_VARIABLES): dict}, "async_set_variables"
    )
    platform.async_register_entity_service(
        "get_state", None, "async_get_state", supports_response=SupportsResponse.ONLY
    )


def _hsv_from_value(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return float(value[0]), float(value[1]), float(value[2])
    return None


class PixelblazeLight(PixelblazeEntity, LightEntity):
    """The Pixelblaze as a light."""

    _attr_name = None
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "light")

    # --- state

    @property
    def _color_control(self) -> str | None:
        colors = self.state_data.color_controls()
        return colors[0] if colors else None

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return {ColorMode.HS} if self._color_control else {ColorMode.BRIGHTNESS}

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.HS if self._color_control else ColorMode.BRIGHTNESS

    @property
    def is_on(self) -> bool | None:
        brightness = self.state_data.brightness
        return None if brightness is None else brightness > 0

    @property
    def brightness(self) -> int | None:
        brightness = self.state_data.brightness
        return None if brightness is None else round(brightness * 255)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        name = self._color_control
        if name is None:
            return None
        value = self.state_data.controls.get(name)
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return None
        if control_type(name) == ControlType.HSV_PICKER:
            return (float(value[0]) % 1 * 360, float(value[1]) * 100)
        return color_util.color_RGB_to_hs(*(float(c) * 255 for c in value[:3]))

    @property
    def effect_list(self) -> list[str]:
        return [EFFECT_SHUFFLE, EFFECT_PLAYLIST, *self.coordinator.pattern_options()]

    @property
    def effect(self) -> str | None:
        return self.coordinator.pattern_label(self.state_data.active_pattern_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.state_data
        return {
            "pattern_id": state.active_pattern_id,
            "sequencer_mode": state.sequencer_mode.name.lower(),
            "sequencer_running": state.sequencer_running,
            "playlist_position": state.playlist.get("position"),
            "preset": self.coordinator.matching_preset(),
            "controls": dict(state.controls),
        }

    # --- commands

    async def async_turn_on(self, **kwargs: Any) -> None:
        coordinator = self.coordinator
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            if effect == EFFECT_SHUFFLE:
                await coordinator.async_set_sequencer_mode(SequencerMode.SHUFFLE)
            elif effect == EFFECT_PLAYLIST:
                await coordinator.async_set_sequencer_mode(SequencerMode.PLAYLIST)
            else:
                await coordinator.async_select_pattern(coordinator.resolve_pattern(effect))

        if (hs_color := kwargs.get(ATTR_HS_COLOR)) is not None and (name := self._color_control):
            await coordinator.async_set_controls({name: self._color_value(name, hs_color)})

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        await coordinator.async_turn_on(None if brightness is None else brightness / 255)

    def _color_value(self, name: str, hs_color: tuple[float, float]) -> list[float]:
        hue, sat = hs_color
        if control_type(name) == ControlType.HSV_PICKER:
            current = _hsv_from_value(self.state_data.controls.get(name))
            value = current[2] if current and current[2] > 0 else 1.0
            return [round(hue / 360, 4), round(sat / 100, 4), value]
        red, green, blue = color_util.color_hs_to_RGB(hue, sat)
        return [round(red / 255, 4), round(green / 255, 4), round(blue / 255, 4)]

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off()

    # --- services

    async def async_save_preset(self, preset: str, include_brightness: bool = False) -> None:
        await self.coordinator.async_save_preset(preset, include_brightness)

    async def async_recall_preset(self, preset: str) -> None:
        await self.coordinator.async_recall_preset(preset)

    async def async_delete_preset(self, preset: str) -> None:
        await self.coordinator.async_delete_preset(preset)

    async def async_set_pattern(self, pattern: str, controls: dict[str, Any] | None = None) -> None:
        await self.coordinator.async_select_pattern(self.coordinator.resolve_pattern(pattern))
        if controls:
            await self.coordinator.async_set_controls(controls)

    async def async_play_playlist(self, playlist: str) -> None:
        await self.coordinator.async_play_playlist(playlist)

    async def async_set_controls(self, controls: dict[str, Any]) -> None:
        await self.coordinator.async_set_controls(controls)

    async def async_set_variables(self, variables: dict[str, Any]) -> None:
        await self.coordinator.async_set_variables(variables)

    async def async_get_state(self) -> ServiceResponse:
        """Return everything known about the device right now."""
        state = self.state_data
        return {
            "name": state.name,
            "version": state.version,
            "brightness": state.brightness,
            "max_brightness": state.max_brightness,
            "pattern_id": state.active_pattern_id,
            "pattern": state.active_pattern_name,
            "controls": dict(state.controls),
            "sequencer_mode": state.sequencer_mode.name.lower(),
            "sequencer_running": state.sequencer_running,
            "playlist": dict(state.playlist),
            "preset": self.coordinator.matching_preset(),
            "presets": dict(self.coordinator.presets),
            "active_playlist": self.coordinator.matching_playlist()
            if state.sequencer_mode == SequencerMode.PLAYLIST
            else None,
            "playlists": self.coordinator.playlists,
            "patterns": dict(state.patterns),
            "fps": state.fps,
        }


class PixelblazeColorControlLight(PixelblazeControlEntity, LightEntity):
    """A pattern's hsvPicker or rgbPicker control, as a light."""

    def __init__(self, coordinator: PixelblazeCoordinator, control: str) -> None:
        super().__init__(coordinator, control)
        self._is_hsv = control_type(control) == ControlType.HSV_PICKER
        mode = ColorMode.HS if self._is_hsv else ColorMode.RGB
        self._attr_supported_color_modes = {mode}
        self._attr_color_mode = mode
        self._last_on: list[float] | None = None

    @property
    def _value(self) -> list[float] | None:
        value = self.control_value
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return [float(v) for v in value[:3]]
        return None

    def _level(self, value: list[float]) -> float:
        return value[2] if self._is_hsv else max(value)

    @property
    def is_on(self) -> bool | None:
        value = self._value
        return None if value is None else self._level(value) > 0

    @property
    def brightness(self) -> int | None:
        value = self._value
        return None if value is None else round(min(1.0, self._level(value)) * 255)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        value = self._value
        if value is None or not self._is_hsv:
            return None
        return (value[0] % 1 * 360, value[1] * 100)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        value = self._value
        if value is None or self._is_hsv:
            return None
        level = max(value)
        if level <= 0:
            return (255, 255, 255)
        return tuple(round(c / level * 255) for c in value)  # type: ignore[return-value]

    async def async_turn_on(self, **kwargs: Any) -> None:
        current = self._value or [0.0, 0.0, 0.0]
        if self._level(current) <= 0 and self._last_on:
            current = self._last_on
        level = self._level(current) or 1.0
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            level = brightness / 255

        if self._is_hsv:
            hue, sat = current[0], current[1]
            if (hs_color := kwargs.get(ATTR_HS_COLOR)) is not None:
                hue, sat = hs_color[0] / 360, hs_color[1] / 100
            value = [round(hue, 4), round(sat, 4), round(level, 4)]
        else:
            peak = max(current)
            base = [c / peak for c in current] if peak > 0 else [1.0, 1.0, 1.0]
            if (rgb := kwargs.get(ATTR_RGB_COLOR)) is not None:
                top = max(rgb) or 255
                base = [c / top for c in rgb]
            value = [round(c * level, 4) for c in base]
        await self.coordinator.async_set_controls({self.control: value})

    async def async_turn_off(self, **kwargs: Any) -> None:
        value = self._value
        if value is None:
            return
        self._last_on = value
        off = [value[0], value[1], 0.0] if self._is_hsv else [0.0, 0.0, 0.0]
        await self.coordinator.async_set_controls({self.control: off})
