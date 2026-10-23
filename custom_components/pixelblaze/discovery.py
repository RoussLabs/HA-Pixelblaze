"""Find Pixelblazes on the local network by listening for their UDP beacons.

Every Pixelblaze broadcasts a 12-byte beacon on UDP port 1889 about once a
second: three little-endian 32-bit words holding the packet type (42), the
device's IPv4 address and its clock.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct

_LOGGER = logging.getLogger(__name__)

BEACON_PORT = 1889
BEACON_TYPE = 42


def parse_beacon(data: bytes) -> int | None:
    """Return the sender ID (the device's chip ID) from a beacon packet, or None.

    The beacon does not carry the device's IP address; the address comes from
    the UDP packet's source, as in pixelblaze-client's enumerators.
    """
    if len(data) < 12:
        return None
    packet_type, sender_id, _clock = struct.unpack_from("<III", data)
    if packet_type != BEACON_TYPE:
        return None
    return sender_id


class _BeaconProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, int] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if (sender_id := parse_beacon(data)) is not None:
            if addr[0] not in self.found:
                _LOGGER.debug("Pixelblaze beacon from %s (sender ID %s)", addr[0], sender_id)
            self.found[addr[0]] = sender_id


async def async_discover(timeout: float = 2.5) -> dict[str, int]:
    """Listen for beacons for ``timeout`` seconds.

    Returns a mapping of each device's IP address to its sender (chip) ID.
    """
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setblocking(False)
        sock.bind(("", BEACON_PORT))
    except OSError as err:
        sock.close()
        _LOGGER.debug("Pixelblaze discovery unavailable: %s", err)
        return {}

    transport, protocol = await loop.create_datagram_endpoint(_BeaconProtocol, sock=sock)
    try:
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return protocol.found
