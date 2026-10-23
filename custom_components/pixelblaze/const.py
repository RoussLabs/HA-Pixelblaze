"""Constants for the Pixelblaze integration."""

from __future__ import annotations

DOMAIN = "pixelblaze"
MANUFACTURER = "ElectroMage"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_SAVE_TO_FLASH = "save_to_flash"
CONF_CONTROL_ENTITIES = "control_entities"

DEFAULT_SCAN_INTERVAL = 10
DEFAULT_SAVE_TO_FLASH = True
DEFAULT_CONTROL_ENTITIES = True

# How long a change must stay put before it is written to flash. The
# Pixelblaze web UI uses the same delay, which keeps flash wear down while
# someone drags a slider around.
FLASH_SAVE_DELAY = 30

# How often to re-read the pattern list even if nothing asked us to.
PATTERN_REFRESH_INTERVAL = 300

# How often to re-read the device's playlist, and only when playlists are saved
# in HA. The playlist only changes when someone edits it (we track our own
# writes), so there's no reason to ask on every poll.
PLAYLIST_REFRESH_INTERVAL = 300

STORAGE_VERSION = 1

SIGNAL_NEW_CONTROLS = f"{DOMAIN}_new_controls_{{}}"

EFFECT_SHUFFLE = "Sequencer: shuffle all"
EFFECT_PLAYLIST = "Sequencer: playlist"

SEQUENCER_OFF = "off"
SEQUENCER_SHUFFLE = "shuffle"
SEQUENCER_PLAYLIST = "playlist"

ATTR_PRESET = "preset"
ATTR_INCLUDE_BRIGHTNESS = "include_brightness"
ATTR_CONTROLS = "controls"
ATTR_PATTERN = "pattern"
ATTR_SAVE = "save"
ATTR_VARIABLES = "variables"

SERVICE_SAVE_PRESET = "save_preset"
SERVICE_RECALL_PRESET = "recall_preset"
SERVICE_DELETE_PRESET = "delete_preset"
SERVICE_SET_PATTERN = "set_pattern"
SERVICE_SET_CONTROLS = "set_controls"
SERVICE_SET_VARIABLES = "set_variables"
SERVICE_PLAY_PLAYLIST = "play_playlist"

ATTR_PLAYLIST = "playlist"

# Playlists are stored as config subentries of the device's entry.
SUBENTRY_PLAYLIST = "playlist"
CONF_ITEMS = "items"
CONF_DEFAULT_DURATION = "default_duration"
CONF_PATTERN = "pattern"
CONF_DURATION = "duration"
DEFAULT_PLAYLIST_DURATION = 30
# Firmware v3.40 raised the playlist limit to 64 entries.
MAX_PLAYLIST_ITEMS = 64
