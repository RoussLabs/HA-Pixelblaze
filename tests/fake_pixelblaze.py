"""A fake Pixelblaze that speaks enough of the websocket protocol for tests.

It deliberately reproduces the awkward parts of the real device: statistics
messages that interrupt request/response pairs, the getConfig answer arriving
as several messages, and binary messages split across frames.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import WSMsgType, web

P1 = "aaaaaaaaaaaaaaaa1"
P2 = "bbbbbbbbbbbbbbbb2"
P3 = "cccccccccccccccc3"


class FakePixelblaze:
    def __init__(self) -> None:
        self.patterns = {P1: "Rainbow", P2: "Sparkle", P3: "Color fade"}
        self.controls: dict[str, dict[str, Any]] = {
            P1: {"sliderSpeed": 0.5},
            P2: {"sliderSpeed": 0.2, "toggleReverse": 0},
            P3: {
                "hsvPickerColor": [0.5, 1, 1],
                "rgbPickerAccent": [1, 0, 0],
                "inputNumberCount": 3,
            },
        }
        self.settings: dict[str, Any] = {
            "name": "Test PB",
            "brandName": "",
            "pixelCount": 100,
            "brightness": 0.5,
            "maxBrightness": 80,
            "sequenceTimer": 15,
            "sequencerMode": 0,
            "runSequencer": False,
            "chipId": 123456,
            "ver": "3.51",
        }
        self.active = P1
        self.position = 0
        self.playlist_items = [{"id": P1, "ms": 30000}, {"id": P2, "ms": 30000}]
        self.received: list[dict[str, Any]] = []
        self.saved: list[dict[str, Any]] = []
        self.clients: set[web.WebSocketResponse] = set()
        self.connections = 0
        self.rebooted = False
        self.echo_active_program = True
        self.sparse_sequencer = False
        self.answers_get_playlist = True
        self.answers_list_programs = True
        self.runner: web.AppRunner | None = None
        self.port = 0

    # --- server lifecycle

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._ws_handler)
        app.router.add_get("/wifistatus", self._wifistatus)
        app.router.add_post("/reboot", self._reboot)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    async def stop(self) -> None:
        await self.drop_clients()
        if self.runner is not None:
            await self.runner.cleanup()

    async def drop_clients(self) -> None:
        for ws in list(self.clients):
            await ws.close()
        self.clients.clear()

    # --- state helpers

    @property
    def mode(self) -> int:
        return self.settings["sequencerMode"]

    @property
    def running(self) -> bool:
        return self.settings["runSequencer"]

    def sequencer_message(self, *, full: bool = False) -> dict[str, Any]:
        """The sequencer state.

        With ``sparse_sequencer`` set, pushes and command replies use the shape
        shown in the protocol documentation: no pattern name and empty
        controls. getConfig (``full``) always includes them.
        """
        program: dict[str, Any] = {"activeProgramId": self.active, "controls": {}}
        if full or not self.sparse_sequencer:
            program["name"] = self.patterns[self.active]
            program["controls"] = dict(self.controls[self.active])
        return {
            "activeProgram": program,
            "sequencerMode": self.mode,
            "runSequencer": self.running,
            "playlist": {
                "position": self.position,
                "id": "_defaultplaylist_",
                "ms": 30000,
                "remainingMs": 12000,
            },
        }

    def stats_message(self) -> dict[str, Any]:
        return {
            "fps": 59.5,
            "vmerr": 0,
            "mem": 2111,
            "uptime": 3_600_000,
            "storageUsed": 250_000,
            "storageSize": 1_000_000,
        }

    async def broadcast(self, message: dict[str, Any]) -> None:
        for ws in list(self.clients):
            await ws.send_str(json.dumps(message))

    async def send_program_list(self) -> None:
        """Send the pattern list unprompted (a reply that arrives late)."""
        body = "".join(f"{pid}\t{name}\n" for pid, name in self.patterns.items()).encode()
        for ws in list(self.clients):
            await ws.send_bytes(bytes([7, 1 | 4]) + body)

    async def sequencer_advance(self, pattern_id: str) -> None:
        """Simulate the sequencer moving on by itself (an unsolicited push)."""
        self.active = pattern_id
        await self.broadcast(self.sequencer_message())

    # --- protocol

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        self.connections += 1
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    command = json.loads(msg.data)
                    self.received.append(command)
                    await self._handle(ws, command)
        finally:
            self.clients.discard(ws)
        return ws

    async def _handle(self, ws: web.WebSocketResponse, cmd: dict[str, Any]) -> None:
        send = lambda m: ws.send_str(json.dumps(m))  # noqa: E731
        if cmd.get("save"):
            self.saved.append(cmd)

        if "getConfig" in cmd:
            # A stats message sneaks in first, as it does on real hardware.
            await send(self.stats_message())
            await send(self.settings)
            await send(self.sequencer_message(full=True))
            await ws.send_bytes(bytes([9, 1 | 4, 5]))  # expander config
        elif "listPrograms" in cmd:
            if not self.answers_list_programs:
                return
            body = "".join(f"{pid}\t{name}\n" for pid, name in self.patterns.items()).encode()
            half = len(body) // 2
            await ws.send_bytes(bytes([7, 1]) + body[:half])
            await send(self.stats_message())
            await ws.send_bytes(bytes([7, 4]) + body[half:])
        elif "activeProgramId" in cmd:
            self.active = cmd["activeProgramId"]
            if self.echo_active_program:
                await send(self.sequencer_message())
        elif "setControls" in cmd:
            self.controls[self.active].update(cmd["setControls"])
            await send({"ack": 1})
        elif "brightness" in cmd:
            self.settings["brightness"] = cmd["brightness"]
        elif "maxBrightness" in cmd:
            self.settings["maxBrightness"] = cmd["maxBrightness"]
        elif "sequenceTimer" in cmd:
            self.settings["sequenceTimer"] = cmd["sequenceTimer"]
        elif "sequencerMode" in cmd:
            self.settings["sequencerMode"] = cmd["sequencerMode"]
            self.settings["runSequencer"] = cmd["sequencerMode"] != 0
            await send(self.sequencer_message())
        elif "runSequencer" in cmd:
            self.settings["runSequencer"] = cmd["runSequencer"]
            await send(self.sequencer_message())
        elif "nextProgram" in cmd:
            ids = list(self.patterns)
            self.active = ids[(ids.index(self.active) + 1) % len(ids)]
            self.settings["runSequencer"] = True
            await send(self.sequencer_message())
        elif "playlist" in cmd:
            playlist = cmd["playlist"]
            if "items" in playlist:
                self.playlist_items = [dict(item) for item in playlist["items"]]
                self.position = 0
            if "position" in playlist:
                self.position = playlist["position"]
            if self.mode == 2 and self.playlist_items:
                self.active = self.playlist_items[self.position]["id"]
            await send(self.sequencer_message())
        elif "getControls" in cmd:
            pid = cmd["getControls"]
            await send({"controls": {pid: dict(self.controls.get(pid, {}))}})
        elif "getPlaylist" in cmd:
            if not self.answers_get_playlist:
                return
            await send(
                {
                    "playlist": {
                        "id": "_defaultplaylist_",
                        "items": self.playlist_items,
                        "position": self.position,
                    }
                }
            )
        elif "ping" in cmd:
            await send({"ack": 1})

    async def _wifistatus(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"status": 3, "ip": "127.0.0.1", "ssid": "x", "mac": "AA:BB:CC:DD:EE:FF"}
        )

    async def _reboot(self, request: web.Request) -> web.Response:
        self.rebooted = True
        return web.json_response({"ack": 1})


async def wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll until ``predicate()`` is true."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)
