"""NanoPID Controller — Home Assistant integration."""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable

import voluptuous as vol
import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    CONF_DEVICE_MAC,
    CONF_DEVICE_NAME,
    DEFAULT_DEVICE_NAME,
    DOMAIN,
    TOPIC_COMMAND,
    TOPIC_STATUS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "select", "number", "button"]

SERVICE_BUNDLED_START = "bundled_start"

# device_id is optional: required only when multiple NanoPID devices are configured
SERVICE_BUNDLED_START_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
    }
)


# ---------------------------------------------------------------------------
# Coordinator — single MQTT subscriber, fan-out to all platform entities
# ---------------------------------------------------------------------------

class NanoPIDCoordinator:
    """Subscribe once to nanopid/<mac>/status and distribute data to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.mac: str = entry.data[CONF_DEVICE_MAC]
        self.device_name: str = entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)
        self.data: dict = {}
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe: Callable | None = None

    async def async_setup(self) -> None:
        """Start MQTT subscription."""
        from homeassistant.components import mqtt

        topic = TOPIC_STATUS.format(mac=self.mac)
        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, topic, self._async_message_received, qos=0
        )
        _LOGGER.debug("NanoPID coordinator subscribed to %s", topic)

    @callback
    def _async_message_received(self, msg) -> None:
        """Parse status JSON and notify all registered entity listeners."""
        try:
            self.data = json.loads(msg.payload)
        except (ValueError, TypeError) as exc:
            _LOGGER.warning("NanoPID: invalid JSON on status topic — %s", exc)
            return
        for listener in self._listeners:
            listener()

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a listener; returns a removal callback."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def async_unload(self) -> None:
        """Unsubscribe from MQTT."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None


# ---------------------------------------------------------------------------
# Service: nanopid.bundled_start
#
# Reads current HA entity states and sends a single atomic JSON start command.
# The MAC is resolved internally from the coordinator — never hardcoded anywhere.
#
# Usage (single device):   service: nanopid.bundled_start
# Usage (multi device):    service: nanopid.bundled_start
#                          data: {device_id: "<ha_device_id>"}
# ---------------------------------------------------------------------------

def _get_coordinator_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> NanoPIDCoordinator | None:
    """Return the coordinator for this service call.

    - No device_id → single device expected; returns it directly.
    - device_id provided → find the config entry whose device matches.
    """
    coordinators: dict[str, NanoPIDCoordinator] = hass.data.get(DOMAIN, {})

    if not coordinators:
        _LOGGER.error("nanopid.bundled_start: no NanoPID devices configured")
        return None

    device_id: str | None = call.data.get("device_id")

    if device_id is None:
        if len(coordinators) == 1:
            return next(iter(coordinators.values()))
        _LOGGER.error(
            "nanopid.bundled_start: multiple NanoPID devices found — "
            "specify device_id to identify the target device"
        )
        return None

    # Match device_id → config entry via device registry
    from homeassistant.helpers import device_registry as dr
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        _LOGGER.error("nanopid.bundled_start: device_id %s not found", device_id)
        return None

    for entry_id in device.config_entries:
        if entry_id in coordinators:
            return coordinators[entry_id]

    _LOGGER.error(
        "nanopid.bundled_start: device_id %s is not a NanoPID device", device_id
    )
    return None


def _entity_state(
    hass: HomeAssistant, registry: er.EntityRegistry, mac: str, key: str
) -> str:
    """Return the current HA state for unique_id '<mac>_<key>'."""
    unique_id = f"{mac}_{key}"
    for entity_entry in registry.entities.values():
        if entity_entry.unique_id == unique_id:
            state = hass.states.get(entity_entry.entity_id)
            return state.state if state else ""
    return ""


async def _async_handle_bundled_start(hass: HomeAssistant, call: ServiceCall) -> None:
    """Build and publish the bundled-start JSON for the target device."""
    from homeassistant.components import mqtt

    coordinator = _get_coordinator_for_call(hass, call)
    if coordinator is None:
        return

    registry = er.async_get(hass)
    mac = coordinator.mac

    def state(key: str) -> str:
        return _entity_state(hass, registry, mac, key)

    sp_raw = state("main_setpoint")
    payload = json.dumps(
        {
            "cmd": "start",
            "mode": state("target_mode"),
            "sp": float(sp_raw) if sp_raw else 0.0,
            "ctrl": state("control_mode"),
            "dir": state("direction"),
            "beh": state("start_behaviour"),
            "prof": state("profile_type"),
        }
    )
    topic = TOPIC_COMMAND.format(mac=mac)
    await mqtt.async_publish(hass, topic, payload, qos=1)
    _LOGGER.debug("nanopid.bundled_start → %s : %s", topic, payload)


# ---------------------------------------------------------------------------
# Lovelace dashboard auto-creation
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Replicate HA's entity-id slugification (lowercase, non-alphanum → _)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


async def _async_create_lovelace_dashboard(
    hass: HomeAssistant, coordinator: "NanoPIDCoordinator"
) -> None:
    """Create the NanoPID Lovelace dashboard in HA storage on first install.

    Idempotent: skips silently if a dashboard for this MAC already exists.
    """
    mac = coordinator.mac
    url_path = f"nanopid_{mac}"
    device_name = coordinator.device_name
    prefix = _slugify(device_name)

    # -- 1. Check / update the dashboard registry (lovelace_dashboards) -----
    dashboards_store: Store = Store(hass, 1, "lovelace_dashboards", minor_version=1)
    dashboards_data = await dashboards_store.async_load() or {"items": []}
    existing_paths = {d.get("url_path") for d in dashboards_data.get("items", [])}

    if url_path in existing_paths:
        _LOGGER.debug("NanoPID dashboard already exists at /%s — skipping", url_path)
        return

    dashboards_data.setdefault("items", []).append(
        {
            "id": url_path,
            "url_path": url_path,
            "title": device_name,
            "icon": "mdi:thermostat",
            "show_in_sidebar": True,
            "require_admin": False,
            "mode": "storage",
        }
    )
    await dashboards_store.async_save(dashboards_data)

    # -- 2. Build dashboard config from bundled YAML template ----------------
    template_path = os.path.join(os.path.dirname(__file__), "dashboard_template.yaml")
    try:
        with open(template_path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        _LOGGER.error("NanoPID: cannot read dashboard template — %s", exc)
        return

    # Substitute placeholders: {prefix}, {device_name}, {mac}
    # Jinja2 braces in the template are doubled ({{ }}) so they survive .format()
    rendered = raw.format(prefix=prefix, device_name=device_name, mac=mac)

    try:
        dashboard_config = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        _LOGGER.error("NanoPID: dashboard template YAML parse error — %s", exc)
        return

    # -- 3. Write dashboard content ------------------------------------------
    content_store: Store = Store(hass, 1, f"lovelace.{url_path}", minor_version=1)
    await content_store.async_save({"config": dashboard_config})

    _LOGGER.info(
        "NanoPID dashboard created: /%s (entity prefix: %s_*)", url_path, prefix
    )


# ---------------------------------------------------------------------------
# Config entry lifecycle
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NanoPID from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = NanoPIDCoordinator(hass, entry)
    await coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the service once; subsequent entries reuse the same registration
    if not hass.services.has_service(DOMAIN, SERVICE_BUNDLED_START):
        async def _handle_bundled_start(call: ServiceCall) -> None:
            await _async_handle_bundled_start(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_BUNDLED_START,
            _handle_bundled_start,
            schema=SERVICE_BUNDLED_START_SCHEMA,
        )

    # Auto-create the Lovelace dashboard on first install (idempotent)
    hass.async_create_task(_async_create_lovelace_dashboard(hass, coordinator))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: NanoPIDCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_unload()
        # Remove the service only when the last NanoPID device is removed
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_BUNDLED_START)
    return unload_ok
