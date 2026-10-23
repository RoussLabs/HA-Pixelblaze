"""Fixtures for Pixelblaze tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import partial
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pixelblaze.api import PixelblazeClient
from custom_components.pixelblaze.const import DOMAIN

from .fake_pixelblaze import FakePixelblaze


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components in every test."""
    return


@pytest.fixture
async def fake_pb(socket_enabled) -> AsyncGenerator[FakePixelblaze]:
    device = FakePixelblaze()
    await device.start()
    yield device
    await device.stop()


@pytest.fixture
def patch_client(fake_pb: FakePixelblaze):
    """Point every PixelblazeClient the integration makes at the fake device."""
    factory = partial(PixelblazeClient, ws_port=fake_pb.port, http_port=fake_pb.port, timeout=2)
    with (
        patch("custom_components.pixelblaze.PixelblazeClient", factory),
        patch("custom_components.pixelblaze.config_flow.PixelblazeClient", factory),
    ):
        yield


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="Test PB", unique_id="123456", data={CONF_HOST: "127.0.0.1"}
    )


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, patch_client
) -> AsyncGenerator[MockConfigEntry]:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
