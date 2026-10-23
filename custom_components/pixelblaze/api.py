"""Asynchronous client for the Pixelblaze websocket API.

The Pixelblaze serves its control protocol over a websocket on port 81. Most
traffic is request/response, but the device also pushes messages on its own:

* ``{"activeProgram": ...}`` whenever the sequencer changes pattern,
* ``{"fps": ...}`` statistics roughly once per second, and
* binary preview frames (which we turn off with ``sendUpdates: false``).

This client keeps one persistent connection open, tracks everything the device
tells us in :class:`PixelblazeState`, and notifies listeners when the state the
user cares about (pattern, controls, sequencer) changes. That is what lets Home
Assistant show the *current* state of the device instead of the last thing it
asked for.

The protocol details are taken from the reverse-engineered documentation in
https://github.com/zranger1/pixelblaze-client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_WS_PORT = 81
DEFAULT_HTTP_PORT = 80
DEFAULT_TIMEOUT = 5.0
# The pattern list can be long and the device builds it from flash, so it gets
# more time (pixelblaze-client also allows listPrograms extra time).
LIST_PROGRAMS_TIMEOUT = 15.0
DEFAULT_PLAYLIST = "_defaultplaylist_"


class PixelblazeError(Exception):
    """Base error for Pixelblaze communication."""


class PixelblazeConnectionError(PixelblazeError):
    """The device could not be reached or the connection dropped."""


class PixelblazeTimeoutError(PixelblazeError):
    """The device did not answer in time."""


class SequencerMode(IntEnum):
    """Sequencer modes understood by the firmware."""

    OFF = 0
    SHUFFLE = 1
    PLAYLIST = 2


class MessageType(IntEnum):
    """Type byte at the start of every binary websocket frame."""

    PUT_SOURCE_CODE = 1
    PUT_BYTE_CODE = 3
    PREVIEW_IMAGE = 4
    PREVIEW_FRAME = 5
    GET_SOURCE_CODE = 6
    GET_PROGRAM_LIST = 7
    PUT_PIXEL_MAP = 8
    EXPANDER_CONFIG = 9


FRAME_FIRST = 1
FRAME_MIDDLE = 2
FRAME_LAST = 4


class ControlType(StrEnum):
    """Pattern UI control types, derived from the exported function name."""

    SLIDER = "slider"
    TOGGLE = "toggle"
    INPUT_NUMBER = "inputNumber"
    HSV_PICKER = "hsvPicker"
    RGB_PICKER = "rgbPicker"


def control_type(name: str) -> ControlType | None:
    """Return the control type for a control name, or None if unsupported.

    ``trigger``, ``showNumber`` and ``gauge`` controls are intentionally not
    supported: the first has no documented wire format and the others are
    outputs, not inputs.
    """
    for ctype in ControlType:
        if name.startswith(ctype.value):
            return ctype
    return None


def control_label(name: str) -> str:
    """Turn ``sliderWaveSpeed`` into ``Wave speed`` and ``sliderLEDCount`` into ``LED count``."""
    ctype = control_type(name)
    rest = name[len(ctype.value) :] if ctype else name
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", rest) or [name]
    label = " ".join(w if len(w) > 1 and w.isupper() else w.lower() for w in words)
    return label[0].upper() + label[1:]


@dataclass
class PixelblazeState:
    """Everything we know about the device, as last reported by it."""

    settings: dict[str, Any] = field(default_factory=dict)
    sequencer: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    patterns: dict[str, str] = field(default_factory=dict)
    patterns_updated: float = 0.0
    sequencer_updated: float = 0.0
    # Contents of the device's (only) playlist: [{"id": pattern id, "ms": duration}].
    # None until read, or if the firmware can't report it.
    playlist_items: list[dict[str, Any]] | None = None

    # --- settings (from getConfig)

    @property
    def name(self) -> str | None:
        return self.settings.get("name")

    @property
    def version(self) -> str | None:
        ver = self.settings.get("ver")
        return None if ver is None else str(ver)

    @property
    def chip_id(self) -> int | None:
        return self.settings.get("chipId")

    @property
    def brightness(self) -> float | None:
        """UI brightness slider, 0..1."""
        value = self.settings.get("brightness")
        return None if value is None else float(value)

    @property
    def max_brightness(self) -> float | None:
        """Global brightness limit, 0..100."""
        value = self.settings.get("maxBrightness")
        return None if value is None else float(value)

    @property
    def pixel_count(self) -> int | None:
        return self.settings.get("pixelCount")

    @property
    def sequence_timer(self) -> float | None:
        """Seconds each pattern plays for in shuffle mode."""
        value = self.settings.get("sequenceTimer")
        return None if value is None else float(value)

    # --- sequencer (from getConfig and pushed on pattern change)

    @property
    def active_program(self) -> dict[str, Any]:
        return self.sequencer.get("activeProgram") or {}

    @property
    def active_pattern_id(self) -> str | None:
        return self.active_program.get("activeProgramId") or None

    @property
    def active_pattern_name(self) -> str | None:
        pid = self.active_pattern_id
        if pid and pid in self.patterns:
            return self.patterns[pid]
        return self.active_program.get("name")

    @property
    def controls(self) -> dict[str, Any]:
        """Current values of the active pattern's UI controls."""
        return self.active_program.get("controls") or {}

    @property
    def sequencer_mode(self) -> SequencerMode:
        mode = self.sequencer.get("sequencerMode", self.settings.get("sequencerMode"))
        try:
            return SequencerMode(int(mode))
        except (TypeError, ValueError):
            return SequencerMode.OFF

    @property
    def sequencer_running(self) -> bool:
        running = self.sequencer.get("runSequencer", self.settings.get("runSequencer"))
        return bool(running)

    @property
    def playlist(self) -> dict[str, Any]:
        return self.sequencer.get("playlist") or {}

    # --- statistics (pushed once a second)

    @property
    def fps(self) -> float | None:
        value = self.stats.get("fps")
        return None if value is None else float(value)

    @property
    def uptime_ms(self) -> int | None:
        return self.stats.get("uptime")

    def color_controls(self) -> list[str]:
        """Names of the active pattern's color pickers, in pattern order."""
        return [
            name
            for name in self.controls
            if control_type(name) in (ControlType.HSV_PICKER, ControlType.RGB_PICKER)
        ]


def _save(save: bool) -> dict[str, bool]:
    """Only send ``"save": true``; the official API examples omit the key otherwise."""
    return {"save": True} if save else {}


Listener = Callable[[], None]
_Predicate = Callable[[Any], bool]


class PixelblazeClient:
    """A persistent, push-aware connection to one Pixelblaze."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        ws_port: int = DEFAULT_WS_PORT,
        http_port: int = DEFAULT_HTTP_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self._session = session
        self._ws_port = ws_port
        self._http_port = http_port
        self._timeout = timeout
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._waiters: list[tuple[_Predicate, asyncio.Future[Any]]] = []
        self._binary: dict[int, bytearray] = {}
        self._listeners: list[Listener] = []
        self._disconnect_listeners: list[Listener] = []
        self._closing = False
        self.state = PixelblazeState()

    # ------------------------------------------------------------------
    # Connection management

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def add_listener(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` when the device pushes a pattern/sequencer change."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def add_disconnect_listener(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` when the connection drops unexpectedly."""
        self._disconnect_listeners.append(listener)
        return lambda: self._disconnect_listeners.remove(listener)

    async def connect(self) -> None:
        """Open the websocket if it is not already open."""
        async with self._connect_lock:
            if self.connected:
                return
            self._closing = False
            url = f"ws://{self.host}:{self._ws_port}"
            try:
                self._ws = await self._session.ws_connect(
                    url,
                    timeout=aiohttp.ClientWSTimeout(ws_close=self._timeout),
                    heartbeat=30,
                    max_msg_size=0,
                )
            except (TimeoutError, aiohttp.ClientError, OSError) as err:
                raise PixelblazeConnectionError(
                    f"Cannot connect to Pixelblaze at {url}: {err}"
                ) from err
            self._binary.clear()
            self._reader = asyncio.get_running_loop().create_task(
                self._read_loop(self._ws), name=f"pixelblaze-reader-{self.host}"
            )
            # Preview frames are sent after every render; we never need them.
            await self._send({"sendUpdates": False})

    async def disconnect(self) -> None:
        """Close the connection and stop the reader."""
        self._closing = True
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            await ws.close()
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader = None
        self._fail_waiters(PixelblazeConnectionError("Disconnected"))

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_text(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    self._handle_binary(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.debug("Websocket error from %s: %s", self.host, ws.exception())
                    break
        except Exception:  # noqa: BLE001 - a bad frame must not kill silently
            _LOGGER.exception("Unexpected error reading from Pixelblaze %s", self.host)
        finally:
            if self._ws is ws:
                self._ws = None
            if not ws.closed:
                await ws.close()
            self._fail_waiters(PixelblazeConnectionError("Connection lost"))
            if not self._closing:
                _LOGGER.debug("Connection to Pixelblaze %s lost", self.host)
                for listener in list(self._disconnect_listeners):
                    listener()

    # ------------------------------------------------------------------
    # Incoming messages

    def _handle_text(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Ignoring non-JSON text from %s: %.80s", self.host, raw)
            return
        if not isinstance(data, dict):
            return

        notify = False
        if "fps" in data:
            # Statistics arrive every second; keep them but don't wake anyone.
            self.state.stats = data
        elif "activeProgram" in data:
            self._merge_sequencer(data)
            notify = True
        elif "ver" in data and "pixelCount" in data:
            self.state.settings = data

        self._resolve(data)
        if notify:
            self._notify()

    def _merge_sequencer(self, data: dict[str, Any]) -> None:
        previous = self.state.sequencer
        merged = dict(data)
        # Pushes sometimes omit the playlist block; don't forget it.
        if "playlist" not in merged and "playlist" in previous:
            merged["playlist"] = previous["playlist"]
        self.state.sequencer = merged
        self.state.sequencer_updated = time.monotonic()
        # getConfig settings also carry the sequencer fields; keep them aligned.
        for key in ("sequencerMode", "runSequencer"):
            if key in data:
                self.state.settings[key] = data[key]

    def _handle_binary(self, frame: bytes) -> None:
        if len(frame) < 2:
            return
        msg_type = frame[0]
        if msg_type == MessageType.PREVIEW_FRAME:
            return
        flags = frame[1]
        _LOGGER.debug(
            "Pixelblaze %s binary frame: type %d, flags %#04x, %d bytes",
            self.host,
            msg_type,
            flags,
            len(frame),
        )
        if flags & FRAME_FIRST:
            self._binary[msg_type] = bytearray()
        buf = self._binary.get(msg_type)
        if buf is None:
            _LOGGER.debug("Dropping type %d continuation frame with no start frame", msg_type)
            return
        buf.extend(frame[2:])
        if not flags & FRAME_LAST:
            return
        payload = bytes(self._binary.pop(msg_type))
        if msg_type == MessageType.GET_PROGRAM_LIST:
            self.state.patterns = self._parse_program_list(payload)
            self.state.patterns_updated = time.monotonic()
            _LOGGER.debug("Pixelblaze %s listed %d patterns", self.host, len(self.state.patterns))
            resolved = self._resolve((msg_type, payload))
            if not resolved:
                # A reply that arrived after its request timed out: still use it.
                self._notify()
            return
        self._resolve((msg_type, payload))

    @staticmethod
    def _parse_program_list(payload: bytes) -> dict[str, str]:
        patterns: dict[str, str] = {}
        for line in payload.decode("utf-8", errors="replace").split("\n"):
            parts = line.split("\t")
            if len(parts) == 2 and parts[0]:
                patterns[parts[0]] = parts[1]
        return patterns

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error in Pixelblaze listener")

    # ------------------------------------------------------------------
    # Request/response plumbing

    def _resolve(self, message: Any) -> bool:
        """Hand ``message`` to whoever is waiting for it; True if anyone was."""
        resolved = False
        for waiter in list(self._waiters):
            predicate, future = waiter
            if future.done():
                continue
            try:
                matched = predicate(message)
            except Exception:  # noqa: BLE001
                matched = False
            if matched:
                future.set_result(message)
                self._waiters.remove(waiter)
                resolved = True
        return resolved

    def _fail_waiters(self, err: Exception) -> None:
        waiters, self._waiters = self._waiters, []
        for _, future in waiters:
            if not future.done():
                future.set_exception(err)

    def _expect(self, predicate: _Predicate) -> asyncio.Future[Any]:
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._waiters.append((predicate, future))
        return future

    async def _wait(
        self, future: asyncio.Future[Any], what: str, timeout: float | None = None
    ) -> Any:
        try:
            return await asyncio.wait_for(future, timeout or self._timeout)
        except TimeoutError as err:
            raise PixelblazeTimeoutError(f"Pixelblaze {self.host} did not answer {what}") from err
        finally:
            self._waiters = [w for w in self._waiters if w[1] is not future]

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self.connected:
            await self.connect()
        assert self._ws is not None
        try:
            await self._ws.send_str(json.dumps(payload, separators=(",", ":")))
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as err:
            raise PixelblazeConnectionError(f"Send to {self.host} failed: {err}") from err

    async def _request(
        self,
        payload: dict[str, Any],
        predicate: _Predicate,
        what: str,
        timeout: float | None = None,
    ) -> Any:
        if not self.connected:
            await self.connect()
        future = self._expect(predicate)
        try:
            await self._send(payload)
        except PixelblazeError:
            future.cancel()
            self._waiters = [w for w in self._waiters if w[1] is not future]
            raise
        return await self._wait(future, what, timeout)

    @staticmethod
    def _has_key(key: str) -> _Predicate:
        return lambda msg: isinstance(msg, dict) and key in msg

    @staticmethod
    def _is_binary(msg_type: MessageType) -> _Predicate:
        return lambda msg: isinstance(msg, tuple) and msg[0] == msg_type

    # ------------------------------------------------------------------
    # Reading state

    async def refresh_config(self) -> PixelblazeState:
        """Ask for the full config: settings plus sequencer state."""
        if not self.connected:
            await self.connect()
        settings = self._expect(lambda m: isinstance(m, dict) and "ver" in m and "pixelCount" in m)
        sequencer = self._expect(self._has_key("activeProgram"))
        await self._send({"getConfig": True})
        await self._wait(settings, "getConfig (settings)")
        await self._wait(sequencer, "getConfig (sequencer)")
        return self.state

    async def refresh_patterns(self) -> dict[str, str]:
        """Fetch the list of saved patterns (id -> name)."""
        await self._request(
            {"listPrograms": True},
            self._is_binary(MessageType.GET_PROGRAM_LIST),
            "listPrograms",
            timeout=max(LIST_PROGRAMS_TIMEOUT, self._timeout),
        )
        return self.state.patterns

    async def get_playlist(self, playlist_id: str = DEFAULT_PLAYLIST) -> dict[str, Any]:
        """Return the playlist (``{"id":..., "items":[{"id","ms"}...], ...}``)."""
        response = await self._request(
            {"getPlaylist": playlist_id},
            # Sequencer pushes also carry a "playlist" block (just the
            # position), so only accept a reply that lists the items.
            lambda m: (
                isinstance(m, dict)
                and "activeProgram" not in m
                and isinstance((m.get("playlist") or {}).get("items"), list)
            ),
            "getPlaylist",
        )
        playlist = response.get("playlist") or {}
        self.state.playlist_items = [
            {"id": item.get("id"), "ms": int(item.get("ms", 0))}
            for item in playlist.get("items") or []
        ]
        return playlist

    async def ping(self) -> None:
        await self._request({"ping": True}, self._has_key("ack"), "ping")

    async def get_wifi_status(self) -> dict[str, Any] | None:
        """Return ``/wifistatus`` (v3 firmware only) or None."""
        url = f"http://{self.host}:{self._http_port}/wifistatus"
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError, OSError):
            return None

    # ------------------------------------------------------------------
    # Changing state. Every setter also updates ``self.state`` immediately so
    # callers can reflect the change before the device confirms it.

    async def set_brightness(self, brightness: float, *, save: bool = False) -> None:
        """Set the UI brightness slider (0..1)."""
        brightness = max(0.0, min(1.0, float(brightness)))
        await self._send({"brightness": brightness, **_save(save)})
        self.state.settings["brightness"] = brightness

    async def set_max_brightness(self, percent: float, *, save: bool = True) -> None:
        """Set the global brightness limit (0..100)."""
        percent = max(0.0, min(100.0, float(percent)))
        await self._send({"maxBrightness": percent, **_save(save)})
        self.state.settings["maxBrightness"] = percent

    async def set_active_pattern(self, pattern_id: str, *, save: bool = False) -> None:
        """Switch to a saved pattern.

        The device answers with the new ``activeProgram`` (including the
        pattern's control values), which updates state via the push handler.
        """
        try:
            await self._request(
                {"activeProgramId": pattern_id, **_save(save)},
                lambda m: (
                    isinstance(m, dict)
                    and (m.get("activeProgram") or {}).get("activeProgramId") == pattern_id
                ),
                "activeProgramId",
            )
        except PixelblazeTimeoutError:
            # Older firmware may not echo; record what we asked for.
            program = dict(self.state.active_program)
            program.update(
                activeProgramId=pattern_id,
                name=self.state.patterns.get(pattern_id),
                controls={},
            )
            self.state.sequencer["activeProgram"] = program

    async def set_controls(self, controls: dict[str, Any], *, save: bool = False) -> None:
        """Set one or more UI controls on the active pattern."""
        await self._send({"setControls": controls, **_save(save)})
        program = dict(self.state.active_program)
        program["controls"] = {**(program.get("controls") or {}), **controls}
        self.state.sequencer["activeProgram"] = program

    async def get_controls(self, pattern_id: str) -> dict[str, Any]:
        """Return the saved control values of any pattern (not just the active one).

        The response is ``{"controls": {"<pattern id>": {"sliderX": 0.5, ...}}}``.
        """
        response = await self._request(
            {"getControls": pattern_id},
            lambda m: isinstance(m, dict) and isinstance(m.get("controls"), dict),
            "getControls",
        )
        return response["controls"].get(pattern_id) or {}

    async def set_variables(self, variables: dict[str, Any]) -> None:
        """Set exported variables on the active pattern."""
        await self._send({"setVars": variables})

    async def set_sequencer_mode(self, mode: SequencerMode, *, save: bool = False) -> None:
        """Set the sequencer mode. Shuffle and playlist also start it running."""
        await self._send({"sequencerMode": int(mode), **_save(save)})
        self.state.sequencer["sequencerMode"] = int(mode)
        self.state.sequencer["runSequencer"] = mode != SequencerMode.OFF
        self.state.settings["sequencerMode"] = int(mode)
        self.state.settings["runSequencer"] = mode != SequencerMode.OFF

    async def set_sequencer_running(self, running: bool, *, save: bool = False) -> None:
        """Play or pause the sequencer without changing its mode."""
        await self._send({"runSequencer": running, **_save(save)})
        self.state.sequencer["runSequencer"] = running
        self.state.settings["runSequencer"] = running

    async def next_pattern(self) -> None:
        """Advance the sequencer (also starts it)."""
        await self._send({"nextProgram": True})

    async def set_playlist(self, items: list[dict[str, Any]], *, save: bool = False) -> None:
        """Replace the device's playlist with ``[{"id": pattern id, "ms": duration}]``."""
        clean = [{"id": item["id"], "ms": int(item["ms"])} for item in items]
        await self._send({"playlist": {"id": DEFAULT_PLAYLIST, "items": clean}, **_save(save)})
        self.state.playlist_items = clean

    async def set_playlist_position(self, position: int) -> None:
        await self._send({"playlist": {"position": int(position)}})

    async def set_sequence_timer(self, seconds: float, *, save: bool = True) -> None:
        """Set the shuffle-mode time per pattern, in seconds."""
        seconds = max(1, int(round(seconds)))
        await self._send({"sequenceTimer": seconds, **_save(save)})
        self.state.settings["sequenceTimer"] = seconds

    async def reboot(self) -> None:
        """Reboot the device via the HTTP API."""
        url = f"http://{self.host}:{self._http_port}/reboot"
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.post(url) as resp:
                    resp.raise_for_status()
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise PixelblazeConnectionError(f"Reboot of {self.host} failed: {err}") from err
