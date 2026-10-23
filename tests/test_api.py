"""Tests for the Pixelblaze websocket client, against a fake device."""

from __future__ import annotations

import asyncio
import socket
import struct

import aiohttp
import pytest

from custom_components.pixelblaze.api import (
    ControlType,
    PixelblazeClient,
    PixelblazeConnectionError,
    SequencerMode,
    control_label,
    control_type,
)
from custom_components.pixelblaze.discovery import BEACON_PORT, async_discover, parse_beacon

from .fake_pixelblaze import P1, P2, P3, FakePixelblaze, wait_for


@pytest.fixture
async def client(fake_pb: FakePixelblaze):
    async with aiohttp.ClientSession() as session:
        pb = PixelblazeClient(
            "127.0.0.1", session, ws_port=fake_pb.port, http_port=fake_pb.port, timeout=2
        )
        yield pb
        await pb.disconnect()


async def test_refresh_config_reads_settings_and_sequencer(client, fake_pb):
    state = await client.refresh_config()
    assert state.name == "Test PB"
    assert state.version == "3.51"
    assert state.chip_id == 123456
    assert state.brightness == 0.5
    assert state.max_brightness == 80
    assert state.active_pattern_id == P1
    assert state.controls == {"sliderSpeed": 0.5}
    assert state.sequencer_mode is SequencerMode.OFF
    assert not state.sequencer_running
    # The interleaved stats message was captured too.
    assert state.fps == 59.5
    # Preview frames were switched off on connect.
    assert fake_pb.received[0] == {"sendUpdates": False}


async def test_pattern_list_reassembled_from_frames(client):
    patterns = await client.refresh_patterns()
    assert patterns == {P1: "Rainbow", P2: "Sparkle", P3: "Color fade"}
    await client.refresh_config()
    assert client.state.active_pattern_name == "Rainbow"


async def test_set_active_pattern_updates_state_from_reply(client, fake_pb):
    await client.refresh_config()
    await client.set_active_pattern(P3)
    assert fake_pb.active == P3
    # Exactly the official format: no "save" key unless saving.
    assert {"activeProgramId": P3} in fake_pb.received
    assert client.state.active_pattern_id == P3
    assert client.state.controls["hsvPickerColor"] == [0.5, 1, 1]
    assert client.state.color_controls() == ["hsvPickerColor", "rgbPickerAccent"]


async def test_set_active_pattern_without_echo(client, fake_pb):
    """Firmware that doesn't answer still leaves us with the requested pattern."""
    await client.refresh_config()
    fake_pb.echo_active_program = False
    client._timeout = 0.2
    await client.set_active_pattern(P2)
    assert client.state.active_pattern_id == P2


async def test_push_from_sequencer_notifies_listeners(client, fake_pb):
    await client.refresh_config()
    calls = []
    client.add_listener(lambda: calls.append(client.state.active_pattern_id))
    await fake_pb.sequencer_advance(P2)
    await wait_for(lambda: calls)
    assert calls == [P2]
    assert client.state.controls == {"sliderSpeed": 0.2, "toggleReverse": 0}


async def test_setters(client, fake_pb):
    await client.refresh_config()
    await client.set_brightness(0.25)
    await client.set_controls({"sliderSpeed": 0.9})
    await client.set_max_brightness(60)
    await client.set_sequence_timer(42)
    await client.ping()  # round trip: everything above has been processed
    assert fake_pb.settings["brightness"] == 0.25
    assert fake_pb.controls[P1]["sliderSpeed"] == 0.9
    assert fake_pb.settings["maxBrightness"] == 60
    assert fake_pb.settings["sequenceTimer"] == 42
    assert client.state.brightness == 0.25
    assert client.state.controls["sliderSpeed"] == 0.9

    await client.set_sequencer_mode(SequencerMode.PLAYLIST)
    await client.ping()
    assert fake_pb.running
    assert client.state.sequencer_mode is SequencerMode.PLAYLIST
    assert client.state.sequencer_running

    await client.set_sequencer_running(False)
    await client.ping()
    assert not fake_pb.running

    assert await client.get_controls(P3) == {
        "hsvPickerColor": [0.5, 1, 1],
        "rgbPickerAccent": [1, 0, 0],
        "inputNumberCount": 3,
    }

    playlist = await client.get_playlist()
    assert [item["id"] for item in playlist["items"]] == [P1, P2]


async def test_disconnect_listener_and_reconnect(client, fake_pb):
    await client.refresh_config()
    dropped = []
    client.add_disconnect_listener(lambda: dropped.append(True))
    await fake_pb.drop_clients()
    await wait_for(lambda: dropped)
    assert not client.connected
    # The next request reconnects on its own.
    await client.refresh_config()
    assert client.connected
    assert fake_pb.connections == 2


async def test_connect_failure(socket_enabled):
    async with aiohttp.ClientSession() as session:
        pb = PixelblazeClient("127.0.0.1", session, ws_port=1, timeout=1)
        with pytest.raises(PixelblazeConnectionError):
            await pb.refresh_config()


async def test_http_endpoints(client, fake_pb):
    status = await client.get_wifi_status()
    assert status["mac"] == "AA:BB:CC:DD:EE:FF"
    await client.reboot()
    assert fake_pb.rebooted


def test_control_helpers():
    assert control_type("sliderSpeed") is ControlType.SLIDER
    assert control_type("hsvPickerColor") is ControlType.HSV_PICKER
    assert control_type("toggleReverse") is ControlType.TOGGLE
    assert control_type("inputNumberCount") is ControlType.INPUT_NUMBER
    assert control_type("triggerFire") is None
    assert control_type("gaugeLevel") is None
    assert control_label("sliderWaveSpeed") == "Wave speed"
    assert control_label("hsvPickerColor") == "Color"
    assert control_label("sliderLEDCount") == "LED count"
    assert control_label("slider_my_value") == "My value"


def test_parse_beacon():
    # type 42, sender id 0x00005FC4, clock: the second word is the chip ID.
    packet = bytes([42, 0, 0, 0, 0xC4, 0x5F, 0, 0, 0x98, 0x8F, 0xD2, 0x21])
    assert parse_beacon(packet) == 0x5FC4
    assert parse_beacon(bytes([43, 0, 0, 0]) + bytes(8)) is None
    assert parse_beacon(b"short") is None


async def test_discover_uses_packet_source_address(socket_enabled):
    """The device's address is where the beacon came from, not a field in it."""
    beacon = struct.pack("<III", 42, 123456, 0)

    async def send_beacons():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _ in range(10):
                sock.sendto(beacon, ("127.0.0.1", BEACON_PORT))
                await asyncio.sleep(0.05)
        finally:
            sock.close()

    found, _ = await asyncio.gather(async_discover(timeout=0.6), send_beacons())
    assert found == {"127.0.0.1": 123456}
