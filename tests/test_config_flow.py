"""Tests for the Pixelblaze config and options flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pixelblaze.const import (
    CONF_CONTROL_ENTITIES,
    CONF_SAVE_TO_FLASH,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

DISCOVER = "custom_components.pixelblaze.config_flow.async_discover"


async def test_discovered_device(hass: HomeAssistant, patch_client):
    with patch(DISCOVER, return_value={"127.0.0.1": 123456}):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test PB"
    assert result["data"] == {CONF_HOST: "127.0.0.1"}
    assert result["result"].unique_id == "123456"


async def test_manual_entry_when_nothing_discovered(hass: HomeAssistant, patch_client):
    with patch(DISCOVER, return_value={}):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " 127.0.0.1 "}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "127.0.0.1"}


async def test_manual_cannot_connect(hass: HomeAssistant, socket_enabled):
    with patch(DISCOVER, return_value={}):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    # Nothing listens on port 81 here.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_known_device_new_address_updates_entry(hass: HomeAssistant, patch_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="123456", data={CONF_HOST: "10.0.0.9"})
    entry.add_to_hass(hass)
    with patch(DISCOVER, return_value={}):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "127.0.0.1"


async def test_reconfigure(hass: HomeAssistant, patch_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="123456", data={CONF_HOST: "10.0.0.9"})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "127.0.0.1"


async def test_options(hass: HomeAssistant, setup_integration):
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 30, CONF_SAVE_TO_FLASH: False, CONF_CONTROL_ENTITIES: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 30
    assert entry.runtime_data.update_interval.total_seconds() == 30


async def test_discovery_hides_configured_devices(hass: HomeAssistant, patch_client):
    """A device already added (matched by chip ID) isn't offered again at a new address."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="123456", data={CONF_HOST: "10.0.0.9"})
    entry.add_to_hass(hass)
    with patch(DISCOVER, return_value={"127.0.0.1": 123456}):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "manual"
