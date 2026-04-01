"""Binary sensor platform for NanoPID — AC zero-crossing detection."""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NanoPIDCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NanoPIDCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NanoPIDAcDetected(coordinator)])


class NanoPIDAcDetected(BinarySensorEntity):
    """Binary sensor: AC zero-crossing signal present (value_json.zc)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "AC Detected"
    _attr_icon = "mdi:sine-wave"
    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, coordinator: NanoPIDCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.mac}_ac_detected"
        self._remove_listener: Callable | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.mac)},
            name=self._coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def is_on(self) -> bool | None:
        raw = self._coordinator.data.get("zc")
        if raw is None:
            return None
        return bool(int(raw))

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self._coordinator.async_add_listener(
            self._async_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()

    @callback
    def _async_update(self) -> None:
        self.async_write_ha_state()
