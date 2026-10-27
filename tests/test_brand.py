"""The integration's icon is served by Home Assistant (2026.3 and newer)."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_custom_components
from homeassistant.setup import async_setup_component

BRAND = Path(__file__).parent.parent / "custom_components" / "pixelblaze" / "brand"

try:
    import homeassistant.components.brands  # noqa: F401
except ImportError:  # Home Assistant before 2026.3
    pytest.skip("Home Assistant serves bundled icons from 2026.3", allow_module_level=True)


@pytest.mark.parametrize(
    ("image", "source"),
    [
        ("icon.png", "icon.png"),
        ("icon@2x.png", "icon@2x.png"),
        # Missing variants fall back to the icon.
        ("logo.png", "icon.png"),
        ("dark_icon.png", "icon.png"),
    ],
)
async def test_icon_served(
    hass: HomeAssistant, hass_client, socket_enabled, image: str, source: str
) -> None:
    integration = (await async_get_custom_components(hass))["pixelblaze"]
    assert integration.has_branding

    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    response = await client.get(f"/api/brands/integration/pixelblaze/{image}")
    assert response.status == 200
    assert await response.read() == (BRAND / source).read_bytes()
