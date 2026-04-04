"""Number platform for NanoPID — setpoint and alarm thresholds."""
from __future__ import annotations

import json
import time
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


@dataclass(frozen=True)
class NanoPIDNumberDescription(NumberEntityDescription):
    """Extends NumberEntityDescription with MQTT helpers."""
    status_key: str = ""
    command_topic_tpl: str = TOPIC_CONFIG
    command_payload_fn: Callable[[float], str] | None = None
    # If True, skip coordinator sync when device is in IDLE (fsm=0).
    # Used for main_setpoint: device reports sp=0 in idle, which must not
    # overwrite a value the user has just set via the slider.
    preserve_on_idle: bool = False


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
        preserve_on_idle=True,
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
        # Local value: updated immediately on user input (optimistic) and by coordinator
        self._current_value: float | None = None
        # Monotonic timestamp until which coordinator updates are ignored after a
        # user action — gives the device time to process the command and publish
        # the updated value without the integration snapping back to stale data.
        self._optimistic_until: float = 0.0

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
        # Optimistic update FIRST: set local value and protection window before
        # the MQTT publish.  The publish is async and yields to the event loop,
        # so the coordinator callback can fire during the await — overwriting
        # _current_value with stale data or sp=0 from the firmware.  Setting the
        # guard here closes that race window completely.
        self._current_value = value
        self._optimistic_until = time.monotonic() + 10.0
        self.async_write_ha_state()

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

    @callback
    def _async_update(self) -> None:
        # Sync with device-reported value from status JSON
        raw = self._coordinator.data.get(self.entity_description.status_key)
        if raw is not None:
            if self.entity_description.preserve_on_idle:
                # IDLE guard: device reports sp=0 when idle — never overwrite a
                # user-set value in this state.
                fsm = int(self._coordinator.data.get("fsm", 0))
                if fsm == 0 and self._current_value is not None:
                    self.async_write_ha_state()
                    return
            # Optimistic guard: ignore coordinator for 10 s after a user action.
            # Covers both IDLE and RUN — firmware may publish sp=0 for 1-2 cycles
            # after a setpoint command before reporting the confirmed new value.
            if time.monotonic() < self._optimistic_until:
                self.async_write_ha_state()
                return
            self._current_value = float(raw)
        self.async_write_ha_state()
