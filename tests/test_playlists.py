"""Playlists saved in Home Assistant and played on the Pixelblaze."""

from __future__ import annotations

import pytest
from homeassistant.components.select import (
    ATTR_OPTIONS,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigSubentry
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pixelblaze.const import DOMAIN

from .fake_pixelblaze import P1, P2, P3, FakePixelblaze

PLAYLIST = "select.test_pb_playlist"
PRESET = "select.test_pb_preset"
LIGHT = "light.test_pb"


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done()
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.client.ping()
    await hass.async_block_till_done()


NAMES = {P1: "Rainbow", P2: "Sparkle", P3: "Color fade"}


async def _create(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    name: str,
    patterns: list[str],
    durations: list[dict[str, int] | None],
    default: dict[str, int] | None = None,
) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "playlist"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    rows = []
    for pid, duration in zip(patterns, durations, strict=True):
        row = {"pattern": NAMES[pid]}
        if duration is not None:
            row["duration"] = duration
        rows.append(row)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": name, "items": rows, "default_duration": default or {"seconds": 30}},
    )
    await hass.async_block_till_done()
    return result


def _subentry(entry: MockConfigEntry, title: str) -> ConfigSubentry:
    return next(sub for sub in entry.subentries.values() if sub.title == title)


async def test_create_and_play(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    entry = setup_integration
    result = await _create(
        hass, entry, "Evening", [P3, P1], [{"seconds": 10}, {"minutes": 1, "seconds": 30}]
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _subentry(entry, "Evening").data["items"] == [
        {"id": P3, "name": "Color fade", "ms": 10_000},
        {"id": P1, "name": "Rainbow", "ms": 90_000},
    ]

    state = hass.states.get(PLAYLIST)
    assert state.attributes[ATTR_OPTIONS] == ["Evening"]
    assert state.state == STATE_UNKNOWN  # not playing yet

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PLAYLIST, "option": "Evening"},
        blocking=True,
    )
    await _settle(hass)
    assert fake_pb.playlist_items == [{"id": P3, "ms": 10_000}, {"id": P1, "ms": 90_000}]
    assert fake_pb.mode == 2 and fake_pb.running
    assert fake_pb.position == 0 and fake_pb.active == P3
    assert hass.states.get(PLAYLIST).state == "Evening"
    # Only one connection: saving a playlist refreshed entities, it didn't reload.
    assert fake_pb.connections == 1


async def test_rows_allow_repeats_and_default_duration(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    entry = setup_integration
    result = await _create(
        hass, entry, "Mix", [P2, P1, P2], [{"seconds": 5}, None, {"minutes": 2}], {"seconds": 45}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [(i["id"], i["ms"]) for i in _subentry(entry, "Mix").data["items"]] == [
        (P2, 5_000),
        (P1, 45_000),  # left empty: default duration
        (P2, 120_000),
    ]


async def test_form_offers_live_patterns(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """Patterns saved on the device since the last poll show up in the editor."""
    entry = setup_integration
    fake_pb.patterns["dddddddddddddddd4"] = "Fireflies"
    fake_pb.controls["dddddddddddddddd4"] = {}
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "playlist"), context={"source": SOURCE_USER}
    )
    items = result["data_schema"].schema["items"]
    options = items.config["fields"]["pattern"]["selector"]["select"]["options"]
    assert options == ["Color fade", "Fireflies", "Rainbow", "Sparkle"]
    assert items.config["multiple"] is True


async def test_validation(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    entry = setup_integration
    await _create(hass, entry, "Evening", [P1], [{"seconds": 5}])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "playlist"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "evening", "items": [], "default_duration": {"seconds": 5}}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_exists", "items": "no_patterns"}


async def test_edit(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    entry = setup_integration
    await _create(hass, entry, "Evening", [P3, P1], [{"seconds": 10}, {"seconds": 90}])
    subentry = _subentry(entry, "Evening")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "playlist"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    # The form opens with the playlist as saved.
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested["name"] == "Evening"
    assert suggested["items"] == [
        {"pattern": "Color fade", "duration": {"hours": 0, "minutes": 0, "seconds": 10}},
        {"pattern": "Rainbow", "duration": {"hours": 0, "minutes": 1, "seconds": 30}},
    ]

    # Reorder, drop one, add one, rename.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Late evening",
            "items": [
                {"pattern": "Rainbow", "duration": {"minutes": 1, "seconds": 30}},
                {"pattern": "Sparkle"},
            ],
            "default_duration": {"seconds": 20},
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    edited = entry.subentries[subentry.subentry_id]
    assert edited.title == "Late evening"
    assert [(item["id"], item["ms"]) for item in edited.data["items"]] == [
        (P1, 90_000),
        (P2, 20_000),
    ]
    assert hass.states.get(PLAYLIST).attributes[ATTR_OPTIONS] == ["Late evening"]
    assert fake_pb.connections == 1


async def test_edit_flags_deleted_patterns(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    entry = setup_integration
    await _create(hass, entry, "Evening", [P3, P1], [{"seconds": 10}, {"seconds": 90}])
    subentry = _subentry(entry, "Evening")
    del fake_pb.patterns[P3]

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "playlist"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Evening",
            "items": [{"pattern": "Color fade"}, {"pattern": "Rainbow"}],
            "default_duration": {"seconds": 20},
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"items": "unknown_patterns"}
    assert result["description_placeholders"]["missing"] == "Color fade"


async def test_delete(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    entry = setup_integration
    await _create(hass, entry, "Evening", [P1], [{"seconds": 5}])
    hass.config_entries.async_remove_subentry(entry, _subentry(entry, "Evening").subentry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PLAYLIST).attributes[ATTR_OPTIONS] == []
    assert fake_pb.connections == 1


async def test_device_playlist_change_detected(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """A playlist edited in the Pixelblaze web UI no longer matches the saved one."""
    entry = setup_integration
    await _create(hass, entry, "Evening", [P3, P1], [{"seconds": 10}, {"seconds": 90}])
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_play_playlist("Evening")
    await _settle(hass)
    assert hass.states.get(PLAYLIST).state == "Evening"

    fake_pb.playlist_items = [{"id": P2, "ms": 5000}]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(PLAYLIST)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["device_playlist"] == [{"pattern": "Sparkle", "seconds": 5.0}]


async def test_play_playlist_action_skips_missing_patterns(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    entry = setup_integration
    await _create(hass, entry, "Evening", [P3, P1], [{"seconds": 10}, {"seconds": 90}])
    # Color fade is deleted on the device.
    del fake_pb.patterns[P3]
    await entry.runtime_data.async_refresh_patterns()

    await hass.services.async_call(
        DOMAIN, "play_playlist", {ATTR_ENTITY_ID: LIGHT, "playlist": "Evening"}, blocking=True
    )
    await _settle(hass)
    assert fake_pb.playlist_items == [{"id": P1, "ms": 90_000}]
    # The playable part of the playlist still counts as this playlist.
    assert hass.states.get(PLAYLIST).state == "Evening"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "play_playlist", {ATTR_ENTITY_ID: LIGHT, "playlist": "Nope"}, blocking=True
        )


async def test_preset_remembers_playlist(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    entry = setup_integration
    coordinator = entry.runtime_data
    await _create(hass, entry, "Evening", [P3, P1], [{"seconds": 10}, {"seconds": 90}])
    await _create(hass, entry, "Party", [P2], [{"seconds": 5}])
    await coordinator.async_play_playlist("Evening")
    await _settle(hass)
    await hass.services.async_call(
        DOMAIN, "save_preset", {ATTR_ENTITY_ID: LIGHT, "preset": "Evening mood"}, blocking=True
    )
    assert coordinator.presets["Evening mood"]["playlist"] == "Evening"

    await coordinator.async_play_playlist("Party")
    await _settle(hass)
    assert hass.states.get(PRESET).state == STATE_UNKNOWN

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET, "option": "Evening mood"},
        blocking=True,
    )
    await _settle(hass)
    assert fake_pb.playlist_items == [{"id": P3, "ms": 10_000}, {"id": P1, "ms": 90_000}]
    assert hass.states.get(PRESET).state == "Evening mood"
    assert hass.states.get(PLAYLIST).state == "Evening"


async def test_firmware_without_get_playlist(
    hass: HomeAssistant, config_entry, patch_client, fake_pb: FakePixelblaze
):
    """Older firmware that ignores getPlaylist still sets up and polls normally."""
    fake_pb.answers_get_playlist = False
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LIGHT).state == "on"
    asked = sum("getPlaylist" in cmd for cmd in fake_pb.received)
    await config_entry.runtime_data.async_refresh()
    assert sum("getPlaylist" in cmd for cmd in fake_pb.received) == asked  # stopped asking
    assert config_entry.runtime_data.last_update_success
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_editor_needs_connected_device(hass: HomeAssistant, patch_client, fake_pb):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="123456", data={"host": "127.0.0.1"})
    entry.add_to_hass(hass)  # not set up
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "playlist"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_unavailable"


async def test_editor_form_serializes_for_frontend(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze, hass_client
):
    """The editor form survives the trip to the browser through the HTTP API."""
    from homeassistant.setup import async_setup_component

    entry = setup_integration
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    response = await client.post(
        "/api/config/config_entries/subentries/flow",
        json={"handler": [entry.entry_id, "playlist"], "show_advanced_options": False},
    )
    assert response.status == 200
    form = (await response.json())["data_schema"]
    items = next(field for field in form if field["name"] == "items")
    config = items["selector"]["object"]
    assert config["multiple"] is True
    assert config["label_field"] == "pattern"
    assert config["fields"]["pattern"]["selector"]["select"]["options"][0] == "Color fade"


def _asked(fake_pb: FakePixelblaze) -> int:
    return sum("getPlaylist" in cmd for cmd in fake_pb.received)


async def test_playlist_not_read_without_saved_playlists(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """With no playlists saved in HA, the device is never asked for its playlist."""
    coordinator = setup_integration.runtime_data
    for _ in range(3):
        await coordinator.async_refresh()
    assert _asked(fake_pb) == 0


async def test_playlist_read_rarely(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """Read after playlists change and every few minutes, not on every poll."""

    entry = setup_integration
    coordinator = entry.runtime_data
    await _create(hass, entry, "Evening", [P1], [{"seconds": 5}])
    await coordinator.async_refresh()
    assert _asked(fake_pb) == 1  # once, after the playlist was added
    for _ in range(5):
        await coordinator.async_refresh()
    assert _asked(fake_pb) == 1  # not on ordinary polls

    coordinator._playlist_read -= 301  # as if five minutes had passed
    await coordinator.async_refresh()
    assert _asked(fake_pb) == 2  # and again after five minutes


async def test_playlist_limited_to_64_patterns(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    entry = setup_integration
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "playlist"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Long",
            "items": [{"pattern": "Rainbow"}] * 65,
            "default_duration": {"seconds": 5},
        },
    )
    assert result["errors"] == {"items": "too_many_patterns"}
    assert result["description_placeholders"]["max"] == "64"
