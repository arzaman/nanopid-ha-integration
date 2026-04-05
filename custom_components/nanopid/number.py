"""Number platform for NanoPID — setpoint and alarm thresholds."""
from __future__ import annotations

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


@dataclass(frozen=True)
class NanoPIDNumberDescription(NumberEntityDescription):
    """Extends NumberEntityDescription with MQTT helpers."""

    status_key: str = ""
    command_topic_tpl: str = TOPIC_CONFIG
    command_payload_fn: Callable[[float], str] | None = None
    # When True the entity filters sp=0 from coordinator updates (firmware
    # always reports sp=0 in IDLE and may briefly do so during transitions).
    is_setpoint: bool = False


def _fmt_sp(v: float) -> str:
    """Format a setpoint float for MQTT.

    HA quantises slider values with  round(v / step) * step  which introduces
    binary floating-point noise:  43.3 → 43.300000000000004.
    str() on that produces a 18-character string that overflows the firmware's
    sscanf/atof input buffer, causing it to fall back to sp=0.
    Rounding to 1 decimal place (matching native_step=0.1) removes the noise.
    """
    return f"{round(v, 1):.1f}"

def _json_th_low(v: float) -> str:
    return json.dumps({"th_low": round(v, 1)})

def _json_th_high(v: float) -> str:
    return json.dumps({"th_high": round(v, 1)})


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
        command_payload_fn=_fmt_sp,
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
        # Optimistic update — show the new value in HA immediately.
        self._current_value = value
        self.async_write_ha_state()

        from homeassistant.components import mqtt

        desc = self.entity_description
        topic = desc.command_topic_tpl.format(mac=self._coordinator.mac)
        payload = desc.command_payload_fn(value) if desc.command_payload_fn else _fmt_sp(value)
        await mqtt.async_publish(self.hass, topic, payload, qos=1)
        _LOGGER.debug("NanoPID %s → %s : %s", self._coordinator.mac, topic, payload)

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self._coordinator.async_add_listener(
            self._async_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()

    @callback
    def _async_update(self) -> None:
        """Sync with the latest device status payload."""
        raw = self._coordinator.data.get(self.entity_description.status_key)
        if raw is not None:
            value = float(raw)
            if self.entity_description.is_setpoint:
                # Firmware reports sp=0 in IDLE and briefly during setpoint
                # transitions — ignore those, keep the last meaningful value.
                if value != 0.0:
                    self._current_value = value
            else:
                self._current_value = value
        self.async_write_ha_state()
