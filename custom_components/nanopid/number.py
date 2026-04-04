"""Number platform for NanoPID — setpoint and alarm thresholds."""
from __future__ import annotations

import json
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
        from homeassistant.components import mqtt

        desc = self.entity_description
        topic = desc.command_topic_tpl.format(mac=self._coordinator.mac)
        payload = desc.command_payload_fn(value) if desc.command_payload_fn else str(value)
        await mqtt.async_publish(self.hass, topic, payload, qos=1)
        # Optimistic update: reflect new value immediately without waiting
        # for the next coordinator push from the device
        self._current_value = value
        self.async_write_ha_state()

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
            self._current_value = float(raw)
        self.async_write_ha_state()
