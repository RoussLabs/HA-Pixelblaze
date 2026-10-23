"""Sensors for Pixelblaze."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator
from .entity import PixelblazeEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixelblazeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PixelblazePatternSensor(coordinator),
            PixelblazeFpsSensor(coordinator),
            PixelblazeStorageSensor(coordinator),
            PixelblazeBootSensor(coordinator),
        ]
    )


class PixelblazePatternSensor(PixelblazeEntity, SensorEntity):
    """Name of the running pattern, handy for history and automations."""

    _attr_translation_key = "active_pattern"

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "active_pattern")

    @property
    def native_value(self) -> str | None:
        return self.state_data.active_pattern_name

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self.state_data
        return {
            "pattern_id": state.active_pattern_id,
            "preset": self.coordinator.matching_preset(),
            "playlist_position": state.playlist.get("position"),
        }


class PixelblazeFpsSensor(PixelblazeEntity, SensorEntity):
    """Render speed, from the statistics the device sends every second."""

    _attr_translation_key = "fps"
    _attr_native_unit_of_measurement = "fps"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "fps")

    @property
    def available(self) -> bool:
        return super().available and self.state_data.fps is not None

    @property
    def native_value(self) -> float | None:
        return self.state_data.fps


class PixelblazeStorageSensor(PixelblazeEntity, SensorEntity):
    _attr_translation_key = "storage_used"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "storage_used")

    @property
    def available(self) -> bool:
        return super().available and bool(self.state_data.stats.get("storageSize"))

    @property
    def native_value(self) -> float | None:
        stats = self.state_data.stats
        size = stats.get("storageSize")
        if not size:
            return None
        return stats.get("storageUsed", 0) / size * 100


class PixelblazeBootSensor(PixelblazeEntity, SensorEntity):
    """When the device last booted (derived from its uptime counter)."""

    _attr_translation_key = "last_boot"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PixelblazeCoordinator) -> None:
        super().__init__(coordinator, "last_boot")
        self._boot: datetime | None = None

    @property
    def available(self) -> bool:
        return super().available and self.state_data.uptime_ms is not None

    @property
    def native_value(self) -> datetime | None:
        uptime = self.state_data.uptime_ms
        if uptime is None:
            return None
        boot = dt_util.utcnow() - timedelta(milliseconds=uptime)
        # Network latency jitters the result; only move it for a real reboot.
        if self._boot is None or abs((boot - self._boot).total_seconds()) > 60:
            self._boot = boot.replace(microsecond=0)
        return self._boot
