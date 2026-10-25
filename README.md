# Pixelblaze for Home Assistant

Directed and edited by RoussLabs; grunt work by Robots.\
Nothing here is anything I wouldn't read or reference myself.

<br>
<p align="center">
  <img src="https://raw.githubusercontent.com/RoussLabs/HA-Pixelblaze/main/custom_components/pixelblaze/brand/icon.png" alt="Blue LED on fire">
</p>

## About
Control [Pixelblaze](https://electromage.com/) LED controllers from Home Assistant. You can set brightness, patterns, sequencer modes and pattern controls (sliders, toggles, colour pickers), and can save and recall multiple presets. Additionally, Home Assistant monitors current state of Pixelblaze and keeps HA in sync, even when the change came from somewhere else.

<br>

> **Not affiliated with ElectroMage.** This is an independent, community-made integration. It isn't made, endorsed, supported or reviewed by ElectroMage, the maker of Pixelblaze. "Pixelblaze" is used only to say which devices it works with. For problems with this integration, [open an issue here](https://github.com/RoussLabs/HA-Pixelblaze/issues), not with ElectroMage.

This is a rewrite of [vutang50/homeassistant-pixelblaze](https://github.com/vutang50/homeassistant-pixelblaze). The protocol knowledge comes from [zranger1/pixelblaze-client](https://github.com/zranger1/pixelblaze-client).

<br>

## Installation

Requires Home Assistant 2025.7 or newer. The integration's icon shows in Home Assistant 2026.3 and newer; earlier versions show a placeholder.

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=RoussLabs&repository=HA-Pixelblaze&category=integration)

1. Install [HACS](https://hacs.xyz/docs/use/) if you haven't already.
2. In Home Assistant, open **HACS**, then the **⋮** menu (top right), then **Custom repositories**.
3. Enter `https://github.com/RoussLabs/HA-Pixelblaze`, choose type **Integration**, and select **Add**.
4. Search HACS for **Pixelblaze**, open it, and select **Download**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration → Pixelblaze**. Pixelblazes on your network are found automatically from their UDP beacons on port 1889. You can also type an address.

HACS will offer updates whenever a new release is published.

> **Note:** HACS can only download from **public** GitHub repositories. Home Assistant's *Settings → Apps → Repositories* is for apps (add-ons) and can't install integrations.

### Manual

Copy `custom_components/pixelblaze` into your Home Assistant `config/custom_components/` folder, restart Home Assistant, then add the integration as in step 6.

There are no Python dependencies. The integration uses Home Assistant's own `aiohttp`.

If the device's IP address changes, use **Reconfigure** on the integration entry, or add it again and the existing entry is updated. Devices are identified by chip ID (or MAC on older firmware), not by IP.

### Migrating from vutang50/homeassistant-pixelblaze

The domain (`pixelblaze`) is the same, but the entities are new and YAML configuration is no longer supported. Remove the old integration entries and YAML, install this version, then add your devices again.

<br>

## How it Knows the Current State of PB

The original integration opened a connection for each command and never read anything back. If the sequencer changed pattern, or someone used the Pixelblaze web UI, Home Assistant showed stale state.

This integration keeps one websocket open to each device and uses two sources of updates:

- **Push (immediate).** The Pixelblaze sends `activeProgram` messages, and replies to each command with its new state. This catches sequencer pattern changes (shuffle or playlist) and the result of every command sent from HA.
- **Poll (within the scan interval, default 10 s).** `getConfig` re-reads the full state. This catches brightness, control, pattern or sequencer changes made from the web UI, the Firestorm app or another client, which the device doesn't broadcast.

The poll also reconnects automatically if the device reboots or drops off Wi-Fi. While it's disconnected, the entities show as unavailable.

## Entities

For a device named "Living Room":

- **`light.living_room`**: on/off and brightness use the Pixelblaze brightness slider ("off" is brightness 0; "on" restores the last brightness).
  - **Effect** is the running pattern, plus `Sequencer: shuffle all` and `Sequencer: playlist`.
  - If the pattern has a colour picker, **colour** controls it.
  - Attributes: `pattern_id`, `controls`, `sequencer_mode`, `sequencer_running`, `playlist_position`, `preset`.
- **`select.living_room_pattern`**: the running pattern. It follows the sequencer as it changes.
- **`select.living_room_sequencer_mode`**: Off, Shuffle all or Playlist.
- **`select.living_room_playlist`**: your saved playlists. It shows the one the device is playing, or *unknown* if it's playing something else. Choosing one loads it and plays it from the start. Its `device_playlist` attribute lists what's actually on the device.
- **`select.living_room_preset`**: your saved presets. It shows the preset that matches what the device is doing now, or *unknown* if none match. Choosing one recalls it.
- **`switch.living_room_sequencer_playing`**: play or pause the sequencer.
- **`sensor.living_room_active_pattern`**: name of the running pattern, useful in history and automation triggers.
- **Buttons**: Next pattern, Previous pattern (playlist mode), Random pattern, Refresh pattern list, Restart.
- **`number.*_brightness_limit`**: global brightness cap (the Pixelblaze Settings tab).
- **`number.*_shuffle_time_per_pattern`**: seconds per pattern in shuffle mode.
- **`sensor.*_frame_rate`**: render FPS (diagnostic). Storage used and last boot are also available but disabled by default.

### Pattern control entities

Pixelblaze patterns expose UI controls through exported functions such as `sliderSpeed` and `hsvPickerColor`. The integration creates an entity for each control the first time it sees it on a running pattern:

- `slider*` becomes a `number` (0–100 %).
- `toggle*` becomes a `switch`.
- `inputNumber*` becomes a `number` (box).
- `hsvPicker*` becomes a `light` (HS colour, brightness = V).
- `rgbPicker*` becomes a `light` (RGB colour).

These entities appear in the device page's **Configuration** card, so the **Controls** card holds only the main controls and device settings. Home Assistant's auto-generated dashboards leave out Configuration entities; add the ones you want to a dashboard by hand.

Entities are keyed by control name, so `sliderSpeed` is a single entity shared by every pattern that has a "Speed" slider. An entity is **unavailable while the running pattern doesn't have that control.** You can turn these entities off in the integration options.

`trigger*` controls are not supported, because their wire format isn't documented. `showNumber*` and `gauge*` controls are outputs, so they aren't supported either.

## Presets

A preset is a snapshot of the pattern, its control values and the sequencer state, with brightness optional. Presets are stored in Home Assistant, so they don't depend on firmware support. They survive restarts.

```yaml
# Save what the Pixelblaze is doing right now
action: pixelblaze.save_preset
target:
  entity_id: light.living_room
data:
  preset: Calm blue
  include_brightness: false

# Recall it (or just pick it in select.living_room_preset)
action: pixelblaze.recall_preset
target:
  entity_id: light.living_room
data:
  preset: Calm blue
```

If you saved the preset while the sequencer was playing, recalling it restarts that sequencer mode. Otherwise it pauses the sequencer, then sets the pattern and its controls.

## Playlists

The Pixelblaze holds only one playlist. You can keep as many as you like in Home Assistant and load one onto the device whenever you want. This action will overwrite the existing playlist so if its important, save in HA first.

**Create one:** go to **Settings → Devices & services → Pixelblaze** and select **Add playlist** next to your device. Then:

1. Give it a name.
2. Under **Patterns**, select **Add** and pick a pattern and how long it plays. The list of patterns is read from the device when the editor opens, so newly saved patterns show up. The same pattern can appear more than once.
3. Drag rows by the handle to set the order. Use the pencil to change a row or the bin to remove it.
4. **Default duration** is used for any row you leave without a duration.

**Edit or delete one:** each playlist is listed under the device's entry on the integration page. Use **⋮ → Reconfigure** to edit it, or **⋮ → Delete** to delete it. Saving or deleting a playlist doesn't reconnect to the device.

**Play one:** pick it in `select.living_room_playlist`, or use the action:

```yaml
action: pixelblaze.play_playlist
target:
  entity_id: light.living_room
data:
  playlist: Evening
```

This writes the playlist into the Pixelblaze's own playlist and starts playlist mode from the first pattern. The device then runs it by itself, so timing is exact and it keeps going if Home Assistant is offline. It replaces whatever was in the device's playlist. With **Save changes to flash** on, the playlist also survives a power cycle.

If a pattern in a playlist has since been deleted from the device, it's skipped when playing. When you edit that playlist, the editor names the missing patterns so you can remove or replace them.

A preset saved while a playlist is playing remembers that playlist, and recalling the preset loads it again.

## Actions

All of these target the main light entity.

- **`pixelblaze.save_preset`**: `preset`, `include_brightness`
- **`pixelblaze.recall_preset`**: `preset`
- **`pixelblaze.delete_preset`**: `preset`
- **`pixelblaze.set_pattern`**: `pattern` (name or ID), optional `controls`
- **`pixelblaze.set_controls`**: `controls`, for example `{"sliderSpeed": 0.5, "hsvPickerColor": [0.6, 1, 1]}`
- **`pixelblaze.set_variables`**: `variables` (exported pattern variables)
- **`pixelblaze.play_playlist`**: `playlist` (name of a saved playlist)
- **`pixelblaze.get_state`**: returns the full current state: pattern, controls, sequencer, playlist, presets and pattern list

Selecting a pattern from HA **pauses** the sequencer, so the pattern you chose stays put. The sequencer mode is kept, so the *Sequencer playing* switch or Next button resumes it.

## Options

- **Polling interval** (default 10 s): how quickly changes made outside HA show up.
- **Save changes to flash** (default on): brightness and slider changes are saved with `save: true` 30 s after they stop changing, following the official guidance to avoid flash wear. Sequencer changes are saved right away. **Pattern changes are always saved by the device itself** (official API), whatever this option says, so automations that switch patterns very often do write to flash.
- **Create entities for pattern controls** (default on).

## Protocol Notes (Advanced)

Sources, in order of authority:

1. ElectroMage's official [Websockets API](https://electromage.com/docs/websockets-api)
2. [pixelblaze-client](https://github.com/zranger1/pixelblaze-client): its source code, [API reference](https://github.com/zranger1/pixelblaze-client/blob/main/docs/pixelblaze.md) and [how-to guides](https://github.com/zranger1/pixelblaze-client/blob/main/docs/how-to-guides.md)
3. The reverse-engineered [protocol notes](https://github.com/zranger1/pixelblaze-client/blob/main/docs/pixelblazeProtocol.md)

- **Select pattern**
  - Official API: `{"activeProgramId": id}`, **saved automatically**.
  - Used here: the same, with no `save` key.
  - Protocol notes instead say `setActivePattern`, re-sent with `save` after 30 s.
- **List patterns**
  - Official API: `{"listPrograms": true}`. The reply is binary 0x07 frames, joined until flag 0x04 (0x05 means a single frame).
  - Used here: the same.
  - Protocol notes instead say `getProgramList`.
- **Brightness slider**
  - Official API: `0.0..1.0`, not saved.
  - Used here: the same; `save: true` is added after 30 s, as the client library does.
  - Protocol notes instead say `0..100`.
- **Read controls**
  - Official API: `{"getControls": id}` returns `{"controls": {id: (...)}}`, for any pattern.
  - Used here: after sequencer pushes.
- **Set controls**
  - Official API: `{"setControls": (...)}`. Sliders are 0-1 and colour pickers are `[a, b, c]` in 0-1. Add `"save": true` "after a period of time after the last user interaction".
  - Used here: the same; saved 30 s after the last change.
  - Protocol notes instead say `setVar`.
- **Variables**
  - Official API: `{"setVars": (...)}` and `{"getVars": true}`.
  - Used here: `setVars` (the `set_variables` action).

These aren't in the official page and come from pixelblaze-client:

- `getConfig`
- the sequencer commands (`sequencerMode`, `runSequencer`, `nextProgram`, `playlist`, `sequenceTimer`)
- `maxBrightness`
- `sendUpdates`
- the stats messages
- `/reboot` and `/wifistatus`

Discovery beacons (UDP port 1889) carry the packet type, the device's **sender (chip) ID** and its clock. The protocol notes describe the second field as the IP address, but pixelblaze-client's code takes the address from the packet's source, and so does this integration.

`sequenceTimer` is treated as seconds, following the config example `"sequenceTimer":15`; the client's docstring says milliseconds.

The official page only mentions sliders and colour pickers. `toggle*` and `inputNumber*` controls are supported on the assumption that they work the same way (toggle sent as 1/0), but that isn't documented.

Unsolicited sequencer pushes are documented as `{"activeProgram":{"activeProgramId":…,"controls":{}},…}`, with no name and empty controls. When a push changes the pattern but carries no controls, the integration fetches that pattern's values with `getControls`.

## Troubleshooting

To see what the device is sending, add this to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.pixelblaze: debug
```

- **"did not answer listPrograms"**: the Pixelblaze didn't send its pattern list within 15 s. The integration keeps working with the last known list and retries on every poll. With debug logging on, look for `binary frame: type 7` lines: if none appear, the device isn't answering. Close any open Pixelblaze web UI tabs and power-cycle the device.

## Known Limitations

* **Tested on one device.** It has been used on a Pixelblaze v3 ([Fibonacci512](https://www.evilgeniuslabs.org/fibonacci512) by Evil Genius Labs) and is tested against a simulated device that follows the documentation in [References](#references). Other firmware versions, Pixelblaze v2 and output expanders haven't been tried.
* **One playlist on the device.** The Pixelblaze holds a single playlist, so playing a saved playlist replaces what's on the device. A playlist can have at most 64 patterns (firmware v3.40 and later).
* **Some pattern controls aren't supported.** `trigger*` controls aren't, because their wire format isn't documented; `showNumber*` and `gauge*` are outputs. `toggle*` and `inputNumber*` work, but aren't in the official API documentation.
* **Pattern controls aren't on auto-generated dashboards.** They're in the device page's Configuration card, which Home Assistant's auto-generated dashboards leave out; add the ones you want to a dashboard by hand.
* **Changes made elsewhere arrive on the next poll.** The Pixelblaze only pushes pattern changes made by its sequencer. Changes from its web UI or other apps show up within the polling interval (10 s by default), and its playlist is re-read only every 5 minutes.
* **Pattern changes are always written to flash** by the device, whatever the *Save changes to flash* option says.
* **One connection per device.** The Pixelblaze accepts a limited number of websocket clients; this integration uses one.
* **No manifest categories yet.** HACS warns that the manifest has none, but Home Assistant's validator doesn't accept the field in any release so far.

## References

**Pixelblaze**

* [Pixelblaze](https://electromage.com/pixelblaze/): the product page (ElectroMage)
* [Websockets API](https://electromage.com/docs/websockets-api): the official protocol documentation
* [Language reference](https://electromage.com/docs/language-reference/): pattern language, including UI controls such as `sliderX` and `hsvPickerX`
* [ElectroMage forum](https://forum.electromage.com/c/news-and-announcements/10): firmware release announcements, including [v3.40](https://forum.electromage.com/t/significant-feature-release-sync-multiple-pixelblazes/2891) (playlist transitions and 64-entry playlists)
* [Fibonacci512 by Evil Genius Labs](https://www.evilgeniuslabs.org/fibonacci512): the test device

**Libraries and apps this integration learned from**

* [zranger1/pixelblaze-client](https://github.com/zranger1/pixelblaze-client): Python client. See its [API reference](https://github.com/zranger1/pixelblaze-client/blob/main/docs/pixelblaze.md), [protocol notes](https://github.com/zranger1/pixelblaze-client/blob/main/docs/pixelblazeProtocol.md) and [how-to guides](https://github.com/zranger1/pixelblaze-client/blob/main/docs/how-to-guides.md).
* [vutang50/homeassistant-pixelblaze](https://github.com/vutang50/homeassistant-pixelblaze): the original Home Assistant integration this one replaces
* [simap/Firestorm](https://github.com/simap/Firestorm): ElectroMage's app for controlling many Pixelblazes

**Home Assistant**

* [Creating a custom integration](https://developers.home-assistant.io/docs/creating_component_index)
* [Config subentries](https://developers.home-assistant.io/docs/core/integration/config_flow#subentry-flows), used for playlists
* [Entity categories](https://developers.home-assistant.io/docs/core/entity/#generic-properties), used to put pattern controls in the Configuration card
* [HACS: publishing an integration](https://hacs.xyz/docs/publish/integration)
