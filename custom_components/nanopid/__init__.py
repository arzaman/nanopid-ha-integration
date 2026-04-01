"""NanoPID Controller — Home Assistant integration."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import CONF_DEVICE_MAC, CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME, DOMAIN, TOPIC_STATUS

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "select", "number", "button"]


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
# Config entry lifecycle
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NanoPID from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = NanoPIDCoordinator(hass, entry)
    await coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: NanoPIDCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_unload()
    return unload_ok
