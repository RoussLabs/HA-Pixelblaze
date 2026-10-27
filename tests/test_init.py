"""End-to-end tests: Home Assistant + the integration + a fake Pixelblaze."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.select import (
    ATTR_OPTIONS,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.pixelblaze.const import (
    DOMAIN,
    EFFECT_PLAYLIST,
    FLASH_SAVE_DELAY,
)

from .fake_pixelblaze import P1, P2, P3, FakePixelblaze, wait_for

LIGHT = "light.test_pb"
PATTERN = "select.test_pb_pattern"
SEQUENCER = "select.test_pb_sequencer_mode"
PRESET = "select.test_pb_preset"
PLAYING = "switch.test_pb_sequencer_playing"
SPEED = "number.test_pb_speed"
ACTIVE = "sensor.test_pb_active_pattern"


async def _call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def _poll(hass, seconds: int = 11):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def _settle(hass, fake_pb):
    """Let commands reach the fake device and its replies come back.

    A ping round trip on the same websocket guarantees every earlier command
    has been handled and every earlier reply has been received.
    """
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.client.ping()
    await hass.async_block_till_done()


async def test_setup_reflects_device_state(hass: HomeAssistant, setup_integration):
    assert setup_integration.state is ConfigEntryState.LOADED

    light = hass.states.get(LIGHT)
    assert light.state == STATE_ON
    assert light.attributes[ATTR_BRIGHTNESS] == 128
    assert light.attributes[ATTR_EFFECT] == "Rainbow"
    assert light.attributes[ATTR_EFFECT_LIST] == [
        "Sequencer: shuffle all",
        EFFECT_PLAYLIST,
        "Color fade",
        "Rainbow",
        "Sparkle",
    ]
    assert light.attributes["pattern_id"] == P1
    assert light.attributes["controls"] == {"sliderSpeed": 0.5}

    pattern = hass.states.get(PATTERN)
    assert pattern.state == "Rainbow"
    assert pattern.attributes[ATTR_OPTIONS] == ["Color fade", "Rainbow", "Sparkle"]
    assert hass.states.get(SEQUENCER).state == "off"
    assert hass.states.get(PLAYING).state == STATE_OFF
    assert hass.states.get(SPEED).state == "50.0"
    assert hass.states.get(ACTIVE).state == "Rainbow"
    assert hass.states.get("number.test_pb_brightness_limit").state == "80.0"
    assert hass.states.get("number.test_pb_shuffle_time_per_pattern").state == "15.0"
    assert hass.states.get("sensor.test_pb_frame_rate").state == "59.5"
    assert hass.states.get(PRESET).state == STATE_UNKNOWN


async def test_sequencer_push_updates_immediately(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """A pattern change made by the device's own sequencer shows up with no poll."""
    await fake_pb.sequencer_advance(P2)
    await wait_for(lambda: hass.states.get(PATTERN).state == "Sparkle")
    assert hass.states.get(LIGHT).attributes[ATTR_EFFECT] == "Sparkle"
    assert hass.states.get(ACTIVE).state == "Sparkle"
    # Sparkle has a toggle control we hadn't seen before: it gets an entity.
    await hass.async_block_till_done()
    assert hass.states.get("switch.test_pb_reverse").state == STATE_OFF
    # ...and the slider entity keeps tracking the shared control name.
    assert hass.states.get(SPEED).state == "20.0"


async def test_external_changes_picked_up_by_poll(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """Changes made in the Pixelblaze web UI arrive with the next poll."""
    fake_pb.settings["brightness"] = 0.0
    fake_pb.controls[P1]["sliderSpeed"] = 0.75
    fake_pb.settings["sequencerMode"] = 1
    fake_pb.settings["runSequencer"] = True
    await _poll(hass)
    await wait_for(lambda: hass.states.get(LIGHT).state == STATE_OFF)
    assert hass.states.get(SPEED).state == "75.0"
    assert hass.states.get(SEQUENCER).state == "shuffle"
    assert hass.states.get(PLAYING).state == STATE_ON


async def test_light_on_off_and_brightness(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT, brightness=255)
    await _settle(hass, fake_pb)
    assert fake_pb.settings["brightness"] == 1.0

    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_OFF, LIGHT)
    await _settle(hass, fake_pb)
    assert fake_pb.settings["brightness"] == 0
    assert hass.states.get(LIGHT).state == STATE_OFF

    # Turning back on restores the last brightness rather than going to 100%.
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT, brightness=64)
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_OFF, LIGHT)
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT)
    await _settle(hass, fake_pb)
    assert fake_pb.settings["brightness"] == pytest.approx(64 / 255)
    assert hass.states.get(LIGHT).attributes[ATTR_BRIGHTNESS] == 64


async def test_effect_selects_pattern_and_pauses_sequencer(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT, effect=EFFECT_PLAYLIST)
    await _settle(hass, fake_pb)
    assert fake_pb.mode == 2 and fake_pb.running
    assert hass.states.get(SEQUENCER).state == "playlist"
    assert hass.states.get(PLAYING).state == STATE_ON

    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT, effect="Color fade")
    await _settle(hass, fake_pb)
    assert fake_pb.active == P3
    assert not fake_pb.running  # paused so the chosen pattern sticks
    assert fake_pb.mode == 2  # but the mode is kept
    assert hass.states.get(PATTERN).state == "Color fade"
    assert hass.states.get(PLAYING).state == STATE_OFF


async def test_color_follows_pattern_color_picker(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    assert hass.states.get(LIGHT).attributes[ATTR_SUPPORTED_COLOR_MODES] == ["brightness"]

    await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, PATTERN, option="Color fade")
    await _settle(hass, fake_pb)
    light = hass.states.get(LIGHT)
    assert light.attributes[ATTR_SUPPORTED_COLOR_MODES] == ["hs"]
    assert light.attributes[ATTR_HS_COLOR] == (180.0, 100.0)

    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT, hs_color=(90, 50))
    await _settle(hass, fake_pb)
    assert fake_pb.controls[P3]["hsvPickerColor"] == [0.25, 0.5, 1]

    # Each color picker also became its own light.
    accent = "light.test_pb_accent"
    assert hass.states.get(accent).attributes[ATTR_RGB_COLOR] == (255, 0, 0)
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, accent, rgb_color=(0, 0, 255), brightness=128)
    await _settle(hass, fake_pb)
    assert fake_pb.controls[P3]["rgbPickerAccent"] == pytest.approx([0, 0, 0.502], abs=1e-3)

    # And the number input became a number entity.
    await _call(hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, "number.test_pb_count", **{ATTR_VALUE: 7})
    await _settle(hass, fake_pb)
    assert fake_pb.controls[P3]["inputNumberCount"] == 7

    # Back on a pattern without those controls, they go unavailable.
    await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, PATTERN, option="Rainbow")
    await _settle(hass, fake_pb)
    assert hass.states.get(accent).state == STATE_UNAVAILABLE
    assert hass.states.get(SPEED).state == "50.0"


async def test_slider_control(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    await _call(hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, SPEED, **{ATTR_VALUE: 25})
    await _settle(hass, fake_pb)
    assert fake_pb.controls[P1]["sliderSpeed"] == 0.25
    assert hass.states.get(SPEED).state == "25.0"


async def test_presets(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    await _call(hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, SPEED, **{ATTR_VALUE: 80})
    await _call(hass, DOMAIN, "save_preset", LIGHT, preset="Fast rainbow")
    await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, PATTERN, option="Sparkle")
    await _call(hass, DOMAIN, "save_preset", LIGHT, preset="Sparkle", include_brightness=True)
    await _call(
        hass, SEQUENCER_DOMAIN := SELECT_DOMAIN, SERVICE_SELECT_OPTION, SEQUENCER, option="shuffle"
    )
    await _call(hass, DOMAIN, "save_preset", LIGHT, preset="Party")
    await _settle(hass, fake_pb)

    preset = hass.states.get(PRESET)
    assert preset.attributes[ATTR_OPTIONS] == ["Fast rainbow", "Sparkle", "Party"]
    assert preset.state == "Party"

    # Recall a pattern preset: sequencer pauses, pattern and slider restored.
    await _call(hass, SEQUENCER_DOMAIN, SERVICE_SELECT_OPTION, PRESET, option="Fast rainbow")
    await _settle(hass, fake_pb)
    assert fake_pb.active == P1
    assert fake_pb.controls[P1]["sliderSpeed"] == 0.8
    assert not fake_pb.running
    assert hass.states.get(PRESET).state == "Fast rainbow"

    # Someone moves the slider in the web UI: no preset matches any more.
    fake_pb.controls[P1]["sliderSpeed"] = 0.1
    await _poll(hass)
    await wait_for(lambda: hass.states.get(PRESET).state == STATE_UNKNOWN)

    # Brightness is part of the "Sparkle" preset.
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT, brightness=255)
    await _call(hass, DOMAIN, "recall_preset", LIGHT, preset="Sparkle")
    await _settle(hass, fake_pb)
    assert fake_pb.active == P2
    assert fake_pb.settings["brightness"] == 0.5

    await _call(hass, DOMAIN, "recall_preset", LIGHT, preset="Party")
    await _settle(hass, fake_pb)
    assert fake_pb.mode == 1 and fake_pb.running
    assert hass.states.get(PRESET).state == "Party"

    await _call(hass, DOMAIN, "delete_preset", LIGHT, preset="Party")
    assert hass.states.get(PRESET).attributes[ATTR_OPTIONS] == ["Fast rainbow", "Sparkle"]
    with pytest.raises(ServiceValidationError):
        await _call(hass, DOMAIN, "recall_preset", LIGHT, preset="Party")


async def test_presets_survive_restart(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    entry = setup_integration
    await _call(hass, DOMAIN, "save_preset", LIGHT, preset="Keep me")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PRESET).attributes[ATTR_OPTIONS] == ["Keep me"]
    assert hass.states.get(PRESET).state == "Keep me"


async def test_set_pattern_service_and_get_state(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    await _call(
        hass, DOMAIN, "set_pattern", LIGHT, pattern="sparkle", controls={"sliderSpeed": 0.3}
    )
    await _settle(hass, fake_pb)
    assert fake_pb.active == P2
    assert fake_pb.controls[P2]["sliderSpeed"] == 0.3

    response = await hass.services.async_call(
        DOMAIN, "get_state", {ATTR_ENTITY_ID: LIGHT}, blocking=True, return_response=True
    )
    state = response[LIGHT]
    assert state["pattern"] == "Sparkle"
    assert state["controls"]["sliderSpeed"] == 0.3
    assert state["sequencer_mode"] == "off"

    with pytest.raises(ServiceValidationError):
        await _call(hass, DOMAIN, "set_pattern", LIGHT, pattern="nope")


async def test_buttons(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    await _call(hass, BUTTON_DOMAIN, SERVICE_PRESS, "button.test_pb_next_pattern")
    await _settle(hass, fake_pb)
    assert fake_pb.active == P2
    assert hass.states.get(PATTERN).state == "Sparkle"

    assert hass.states.get("button.test_pb_previous_pattern").state == STATE_UNAVAILABLE
    await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, SEQUENCER, option="playlist")
    await _settle(hass, fake_pb)
    fake_pb.position = 0
    await _call(hass, BUTTON_DOMAIN, SERVICE_PRESS, "button.test_pb_previous_pattern")
    await _settle(hass, fake_pb)
    assert fake_pb.position == 1

    await _call(hass, BUTTON_DOMAIN, SERVICE_PRESS, "button.test_pb_restart")
    assert fake_pb.rebooted


async def test_sequencer_switch(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    # Playing with the sequencer off starts shuffle, like the web UI.
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, PLAYING)
    await _settle(hass, fake_pb)
    assert fake_pb.mode == 1 and fake_pb.running
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, PLAYING)
    await _settle(hass, fake_pb)
    assert fake_pb.mode == 1 and not fake_pb.running


async def test_unavailable_when_disconnected(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    await fake_pb.drop_clients()
    await wait_for(lambda: hass.states.get(LIGHT).state == STATE_UNAVAILABLE)
    # The next poll reconnects.
    await _poll(hass)
    await wait_for(lambda: hass.states.get(LIGHT).state == STATE_ON)
    assert fake_pb.connections == 2


async def test_slider_changes_saved_to_flash_after_delay(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    for value in (10, 20, 30):
        await _call(hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, SPEED, **{ATTR_VALUE: value})
    await _call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT, brightness=200)
    await _settle(hass, fake_pb)
    assert fake_pb.saved == []

    await _poll(hass, FLASH_SAVE_DELAY + 1)
    await wait_for(lambda: len(fake_pb.saved) == 2)
    saved = {next(iter(k for k in cmd if k != "save")): cmd for cmd in fake_pb.saved}
    assert saved["setControls"]["setControls"] == {"sliderSpeed": 0.3}
    assert saved["brightness"]["brightness"] == pytest.approx(200 / 255)


async def test_unload(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    entry = setup_integration
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    await wait_for(lambda: not fake_pb.clients)


async def test_setup_retries_when_unreachable(
    hass: HomeAssistant, config_entry, patch_client, fake_pb
):
    await fake_pb.stop()
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_documented_push_format_still_yields_controls(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """Pushes shaped like the protocol docs (no name, empty controls) trigger a re-read."""
    fake_pb.sparse_sequencer = True
    await fake_pb.sequencer_advance(P2)
    await wait_for(lambda: hass.states.get(PATTERN).state == "Sparkle")
    await wait_for(lambda: hass.states.get(SPEED).state == "20.0")
    await hass.async_block_till_done()
    assert hass.states.get("switch.test_pb_reverse").state == STATE_OFF
    assert hass.states.get(ACTIVE).state == "Sparkle"
    # Fetched with the documented getControls call, not a full config re-read.
    assert {"getControls": P2} in fake_pb.received


async def test_pattern_not_resent_to_save(
    hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze
):
    """activeProgramId is persisted by the device itself (official API docs)."""
    await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, PATTERN, option="Color fade")
    await _settle(hass, fake_pb)
    sent = len(fake_pb.received)
    await _poll(hass, FLASH_SAVE_DELAY + 1)
    await _settle(hass, fake_pb)
    assert not any("activeProgramId" in cmd for cmd in fake_pb.received[sent:])
    assert fake_pb.saved == []


async def test_device_page_layout(hass: HomeAssistant, setup_integration, fake_pb: FakePixelblaze):
    """Pattern controls go in the Configuration card; everything else in Controls."""
    from homeassistant.const import EntityCategory
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    assert registry.async_get(SPEED).entity_category is EntityCategory.CONFIG
    for entity_id in (
        LIGHT,
        PATTERN,
        PRESET,
        SEQUENCER,
        PLAYING,
        "button.test_pb_next_pattern",
        "button.test_pb_refresh_pattern_list",
        "button.test_pb_restart",
        "number.test_pb_brightness_limit",
        "number.test_pb_shuffle_time_per_pattern",
    ):
        assert registry.async_get(entity_id).entity_category is None, entity_id


async def test_silent_pattern_list_does_not_block_setup(
    hass: HomeAssistant, config_entry, patch_client, fake_pb: FakePixelblaze
):
    """If listPrograms goes unanswered, setup still succeeds with the saved list."""
    from unittest.mock import patch

    from custom_components.pixelblaze import api

    # First run: list received and saved.
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    # The device stops answering listPrograms.
    fake_pb.answers_list_programs = False
    with patch.object(api, "LIST_PROGRAMS_TIMEOUT", 0.2):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(PATTERN).state == "Rainbow"
    assert hass.states.get(PATTERN).attributes[ATTR_OPTIONS] == [
        "Color fade",
        "Rainbow",
        "Sparkle",
    ]
    assert hass.states.get(LIGHT).state == STATE_ON

    # The reply turns up late, with a new pattern: it's used.
    fake_pb.patterns["dddddddddddddddd4"] = "Fireflies"
    await fake_pb.send_program_list()
    await wait_for(lambda: "Fireflies" in hass.states.get(PATTERN).attributes[ATTR_OPTIONS])


async def test_silent_pattern_list_first_run(
    hass: HomeAssistant, config_entry, patch_client, fake_pb: FakePixelblaze
):
    """Even with no saved list, setup completes and fills in when the list arrives."""
    from unittest.mock import patch

    from custom_components.pixelblaze import api

    fake_pb.answers_list_programs = False
    config_entry.add_to_hass(hass)
    with patch.object(api, "LIST_PROGRAMS_TIMEOUT", 0.2):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    # The pattern name still comes from the sequencer state.
    assert hass.states.get(ACTIVE).state == "Rainbow"

    fake_pb.answers_list_programs = True
    await _poll(hass)
    await wait_for(lambda: len(hass.states.get(PATTERN).attributes[ATTR_OPTIONS]) == 3)
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_stale_pattern_controls_device_removed(
    hass: HomeAssistant, config_entry, patch_client, fake_pb: FakePixelblaze
):
    """A leftover pattern controls device is removed; the Pixelblaze device stays."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.pixelblaze import async_remove_config_entry_device

    config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    main = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "123456")},
        name="Test PB",
    )
    stale = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "123456_pattern_controls")},
        name="Test PB pattern controls",
    )
    registry.async_update_device(stale.id, via_device_id=main.id)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.id) is None
    assert registry.async_get(main.id) is not None
    assert not await async_remove_config_entry_device(hass, config_entry, main)
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
