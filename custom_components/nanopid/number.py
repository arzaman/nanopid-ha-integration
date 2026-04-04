"""Number platform for NanoPID — setpoint and alarm thresholds."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NanoPIDCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL, TOPIC_CONFIG, TOPIC_SETPOINT

_LOGGER = logging.getLogger(__name__)

# How long to wait after the last set_value call before publishing to the device.
# Coalesces rapid-fire slider events (mushroom emits one per pixel of drag).
_SETPOINT_DEBOUNCE_S = 0.15


@dataclass(frozen=True)
class NanoPIDNumberDescription(NumberEntityDescription):
    """Extends NumberEntityDescription with MQTT helpers."""

    status_key: str = ""
    command_topic_tpl: str = TOPIC_CONFIG
    command_payload_fn: Callable[[float], str] | None = None
    # Mark the main setpoint entity.  Enables:
    #   • debounced publish (coalesces rapid slider input events)
    #   • one-shot coordinator init (coordinator only sets the value once,
    #     on the first non-zero reading; after that the entity owns its state)
    is_setpoint: bool = False


def _plain_float(v: float) -> str:
    return str(v)

def _json_th_low(v: float) -> str:
    return json.dumps({"th_low": v})

def _json_th_high(v: float) -> str:
    return json.dumps({"th_high": v})


NUMBER_DESCRIPTIONS: tuple[NanoPIDNumberDescription, ...] = (
    NanoPIDNumberDescription(
        key="main_setpoint",
        name="Main Setpoint",
        icon="mdi:target",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_min_value=-55.0,
        native_max_value=125.0,
        native_step=0.1,
        mode=NumberMode.BOX,
        status_key="sp",
        command_topic_tpl=TOPIC_SETPOINT,
        command_payload_fn=_plain_float,
        is_setpoint=True,
    ),
    NanoPIDNumberDescription(
        key="alarm_th_low",
        name="Alarm TH Low",
        icon="mdi:thermometer-low",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_min_value=-55.0,
        native_max_value=125.0,
        native_step=1.0,
        mode=NumberMode.BOX,
        status_key="th_l",
        command_topic_tpl=TOPIC_CONFIG,
        command_payload_fn=_json_th_low,
    ),
    NanoPIDNumberDescription(
        key="alarm_th_high",
        name="Alarm TH High",
        icon="mdi:thermometer-high",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_min_value=-55.0,
        native_max_value=125.0,
        native_step=1.0,
        mode=NumberMode.BOX,
        status_key="th_h",
        command_topic_tpl=TOPIC_CONFIG,
        command_payload_fn=_json_th_high,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NanoPIDCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NanoPIDNumber(coordinator, desc) for desc in NUMBER_DESCRIPTIONS
    )


class NanoPIDNumber(NumberEntity):
    """A number entity for NanoPID setpoint / alarm thresholds."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: NanoPIDCoordinator,
        description: NanoPIDNumberDescription,
    ) -> None:
        self.entity_description: NanoPIDNumberDescription = description
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.mac}_{description.key}"
        self._remove_listener: Callable | None = None
        self._current_value: float | None = None
        # Setpoint-specific state
        self._sp_inited: bool = False          # True once coordinator gave us a non-zero SP
        self._publish_handle: asyncio.TimerHandle | None = None  # debounce handle

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.mac)},
            name=self._coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def native_value(self) -> float | None:
        return self._current_value

    async def async_set_native_value(self, value: float) -> None:
        """Called by HA whenever the user changes the entity value."""
        desc = self.entity_description

        # Always update local state immediately so the dashboard reflects the
        # user's intent without waiting for the device to confirm.
        self._current_value = value
        if desc.is_setpoint:
            self._sp_inited = True  # user has spoken; stop accepting coordinator updates
        self.async_write_ha_state()

        if desc.is_setpoint:
            # Debounced publish: the mushroom slider fires one event per pixel of
            # drag.  We wait for 150 ms of silence, then publish only the final
            # value.  This naturally coalesces any burst of events (ghost or real)
            # so the device receives exactly one command per user gesture.
            if self._publish_handle is not None:
                self._publish_handle.cancel()
            self._publish_handle = self.hass.loop.call_later(
                _SETPOINT_DEBOUNCE_S,
                lambda: self.hass.async_create_task(self._async_publish_sp()),
            )
        else:
            # Alarm thresholds: no debounce needed, publish directly.
            await self._async_publish(value)

    async def _async_publish_sp(self) -> None:
        """Publish the current setpoint to the device (called after debounce window)."""
        self._publish_handle = None
        value = self._current_value
        if value is None:
            return
        await self._async_publish(value)
        _LOGGER.debug(
            "NanoPID %s: setpoint → %.2f published to %s",
            self._coordinator.mac,
            value,
            self.entity_description.command_topic_tpl.format(mac=self._coordinator.mac),
        )

    async def _async_publish(self, value: float) -> None:
        """Low-level MQTT publish helper."""
        from homeassistant.components import mqtt

        desc = self.entity_description
        topic = desc.command_topic_tpl.format(mac=self._coordinator.mac)
        payload = desc.command_payload_fn(value) if desc.command_payload_fn else str(value)
        await mqtt.async_publish(self.hass, topic, payload, qos=1)

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self._coordinator.async_add_listener(
            self._async_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
        if self._publish_handle is not None:
            self._publish_handle.cancel()
            self._publish_handle = None

    @callback
    def _async_update(self) -> None:
        """Receive a new status payload from the coordinator."""
        raw = self._coordinator.data.get(self.entity_description.status_key)
        if raw is not None:
            value = float(raw)
            if self.entity_description.is_setpoint:
                # One-shot init: accept the first non-zero SP from the device
                # (e.g., HA restarts while device is already running).
                # Once the user has set a value (_sp_inited=True), we stop
                # accepting coordinator updates entirely — the entity owns its
                # own state and only the user can change it.
                if not self._sp_inited and value != 0.0:
                    self._current_value = value
                    self._sp_inited = True
            else:
                self._current_value = value
        self.async_write_ha_state()
