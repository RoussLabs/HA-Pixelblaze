"""Config flow for Pixelblaze."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    DurationSelector,
    DurationSelectorConfig,
    ObjectSelector,
    ObjectSelectorConfig,
    TextSelector,
)

from .api import PixelblazeClient, PixelblazeError
from .const import (
    CONF_CONTROL_ENTITIES,
    CONF_DEFAULT_DURATION,
    CONF_DURATION,
    CONF_ITEMS,
    CONF_PATTERN,
    CONF_SAVE_TO_FLASH,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONTROL_ENTITIES,
    DEFAULT_PLAYLIST_DURATION,
    DEFAULT_SAVE_TO_FLASH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_PLAYLIST_ITEMS,
    SUBENTRY_PLAYLIST,
)
from .coordinator import PixelblazeConfigEntry, PixelblazeCoordinator
from .discovery import async_discover

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY = "manual"


class CannotConnect(Exception):
    """The device did not respond like a Pixelblaze."""


class PixelblazeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Pixelblaze, either discovered on the network or by address."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: PixelblazeConfigEntry) -> OptionsFlow:
        return PixelblazeOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: PixelblazeConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_PLAYLIST: PlaylistSubentryFlow}

    async def _async_probe(self, host: str) -> tuple[str, str]:
        """Connect to ``host`` and return (unique id, device name)."""
        client = PixelblazeClient(host, async_get_clientsession(self.hass))
        try:
            state = await client.refresh_config()
            unique_id: str | None = None
            if state.chip_id:
                unique_id = str(state.chip_id)
            else:
                wifi = await client.get_wifi_status()
                if wifi and wifi.get("mac"):
                    unique_id = format_mac(wifi["mac"])
            return unique_id or host, state.name or host
        except PixelblazeError as err:
            _LOGGER.debug("Probe of %s failed: %s", host, err)
            raise CannotConnect from err
        finally:
            await client.disconnect()

    async def _async_create(self, host: str) -> ConfigFlowResult:
        unique_id, name = await self._async_probe(host)
        await self.async_set_unique_id(unique_id)
        # A known device at a new address: just update the address.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(title=name, data={CONF_HOST: host})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer discovered devices, falling back to manual entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            if host == MANUAL_ENTRY:
                return await self.async_step_manual()
            try:
                return await self._async_create(host)
            except CannotConnect:
                errors["base"] = "cannot_connect"

        if not self._discovered:
            entries = self._async_current_entries()
            configured_hosts = {entry.data.get(CONF_HOST) for entry in entries}
            configured_ids = {entry.unique_id for entry in entries}
            hosts = sorted(
                host
                for host, sender_id in (await async_discover()).items()
                if host not in configured_hosts and str(sender_id) not in configured_ids
            )
            names = await asyncio.gather(
                *(self._async_name(host) for host in hosts), return_exceptions=True
            )
            for host, name in zip(hosts, names, strict=True):
                label = host if isinstance(name, BaseException) or not name else f"{name} ({host})"
                self._discovered[host] = label

        if not self._discovered:
            return await self.async_step_manual()

        options = {**self._discovered, MANUAL_ENTRY: "Enter an address manually"}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): vol.In(options)}),
            errors=errors,
        )

    async def _async_name(self, host: str) -> str | None:
        try:
            _, name = await self._async_probe(host)
        except CannotConnect:
            return None
        return name

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a device by host name or IP address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                return await self._async_create(user_input[CONF_HOST].strip())
            except CannotConnect:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str}), user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address of an existing device."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                unique_id, _ = await self._async_probe(host)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str}),
                user_input or {CONF_HOST: entry.data[CONF_HOST]},
            ),
            errors=errors,
        )


class PixelblazeOptionsFlow(OptionsFlow):
    """Tune polling, flash saving and control entities."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(cv.positive_int, vol.Range(min=2, max=600)),
                vol.Required(
                    CONF_SAVE_TO_FLASH,
                    default=options.get(CONF_SAVE_TO_FLASH, DEFAULT_SAVE_TO_FLASH),
                ): bool,
                vol.Required(
                    CONF_CONTROL_ENTITIES,
                    default=options.get(CONF_CONTROL_ENTITIES, DEFAULT_CONTROL_ENTITIES),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _duration(ms: int) -> dict[str, int]:
    seconds = max(1, round(ms / 1000))
    return {"hours": seconds // 3600, "minutes": seconds % 3600 // 60, "seconds": seconds % 60}


def _milliseconds(value: dict[str, Any]) -> int:
    return max(1000, int(cv.time_period_dict(value).total_seconds() * 1000))


class PlaylistSubentryFlow(ConfigSubentryFlow):
    """Create or edit a playlist on one page.

    The patterns are a list of rows (pattern + duration) that can be added,
    edited, deleted and dragged into order. The pattern choices are read live
    from the device. Rows carry the pattern's display name, because that's
    what the row shows; it's turned back into the pattern ID on save.
    """

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_step_playlist("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_step_playlist("reconfigure", user_input)

    def _coordinator(self) -> PixelblazeCoordinator | None:
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return None
        return entry.runtime_data

    async def _async_step_playlist(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="device_unavailable")
        try:
            # Re-read so patterns saved or renamed since the last poll show up.
            await coordinator.client.refresh_patterns()
        except PixelblazeError as err:
            _LOGGER.debug("Using cached pattern list: %s", err)
        labels = coordinator.pattern_options()  # unique display name -> pattern id
        if not labels:
            return self.async_abort(reason="no_device_patterns")
        editing = self._get_reconfigure_subentry() if self.source == SOURCE_RECONFIGURE else None
        # Names of patterns in the playlist that were deleted from the device.
        # They stay selectable so the form can say which ones need replacing.
        stale = [
            item.get("name", item["id"])
            for item in (editing.data.get(CONF_ITEMS) or [] if editing else [])
            if item["id"] not in coordinator.client.state.patterns
        ]

        errors: dict[str, str] = {}
        placeholders = {"missing": "", "max": str(MAX_PLAYLIST_ITEMS)}
        if user_input is not None:
            title = user_input.get(CONF_NAME, "").strip()
            rows = user_input.get(CONF_ITEMS) or []
            default_ms = _milliseconds(user_input.get(CONF_DEFAULT_DURATION) or {"seconds": 30})
            taken = {
                sub.title.casefold()
                for sub in self._get_entry().subentries.values()
                if sub.subentry_type == SUBENTRY_PLAYLIST and sub is not editing
            }
            unknown = [row.get(CONF_PATTERN) for row in rows if row.get(CONF_PATTERN) not in labels]
            if not title:
                errors[CONF_NAME] = "name_required"
            elif title.casefold() in taken:
                errors[CONF_NAME] = "name_exists"
            if not rows:
                errors[CONF_ITEMS] = "no_patterns"
            elif len(rows) > MAX_PLAYLIST_ITEMS:
                errors[CONF_ITEMS] = "too_many_patterns"
            elif unknown:
                errors[CONF_ITEMS] = "unknown_patterns"
                placeholders["missing"] = ", ".join(str(name) for name in unknown)
            if not errors:
                patterns = coordinator.client.state.patterns
                items = [
                    {
                        "id": labels[row[CONF_PATTERN]],
                        "name": patterns[labels[row[CONF_PATTERN]]],
                        "ms": _milliseconds(row[CONF_DURATION])
                        if row.get(CONF_DURATION)
                        else default_ms,
                    }
                    for row in rows
                ]
                data = {CONF_ITEMS: items}
                if editing is not None:
                    return self.async_update_and_abort(
                        self._get_entry(), editing, title=title, data=data
                    )
                return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_ITEMS): ObjectSelector(
                    ObjectSelectorConfig(
                        multiple=True,
                        label_field=CONF_PATTERN,
                        description_field=CONF_DURATION,
                        fields={
                            CONF_PATTERN: {
                                "label": "Pattern",
                                "required": True,
                                "selector": {
                                    "select": {
                                        "options": list(labels)
                                        + [name for name in stale if name not in labels],
                                        "mode": "dropdown",
                                        "sort": False,
                                    }
                                },
                            },
                            CONF_DURATION: {
                                "label": "Duration (leave empty for the default)",
                                "required": False,
                                "selector": {"duration": {"enable_day": False}},
                            },
                        },
                    )
                ),
                vol.Required(CONF_DEFAULT_DURATION): DurationSelector(
                    DurationSelectorConfig(enable_day=False)
                ),
            }
        )
        if user_input is None:
            user_input = self._suggested(editing, coordinator, labels)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    def _suggested(
        editing: Any, coordinator: PixelblazeCoordinator, labels: dict[str, str]
    ) -> dict[str, Any]:
        default = {CONF_DEFAULT_DURATION: _duration(DEFAULT_PLAYLIST_DURATION * 1000)}
        if editing is None:
            return {**default, CONF_ITEMS: []}
        by_id = {pid: label for label, pid in labels.items()}
        rows = [
            {
                # A pattern deleted from the device keeps its old name, which
                # the form then flags as unknown until the row is removed.
                CONF_PATTERN: by_id.get(item["id"], item.get("name", item["id"])),
                CONF_DURATION: _duration(int(item["ms"])),
            }
            for item in editing.data.get(CONF_ITEMS) or []
        ]
        return {**default, CONF_NAME: editing.title, CONF_ITEMS: rows}
