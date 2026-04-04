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
    # When True, two independent guards are active for the setpoint entity:
    #
    # Guard A — coordinator → entity:
    #   Reject sp=0 from the status topic.  The firmware reports sp=0 in
    #   IDLE and occasionally during RUN transitions; accepting these would
    #   corrupt the slider position and trigger guard B below.
    #
    # Guard B — slider → device:
    #   Reject set_value(0) when the entity already holds a non-zero SP and
    #   the device is not in Manual Dimmer mode (where 0 % power is valid).
    #   The mushroom-number-card slider emits a ghost set_value(0) event
    #   before the real value on every interaction; without this guard that
    #   zero is published to the controller and resets the running setpoint.
    filter_zero_sp: bool = False


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
        filter_zero_sp=True,
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
        """Handle user input from the dashboard slider or box."""
        desc = self.entity_description

        # Guard B: reject ghost set_value(0) from the mushroom slider.
        # The slider emits a spurious zero before the real value on every
        # drag interaction.  We drop it when:
        #   - filter_zero_sp is set (main_setpoint only)
        #   - we already hold a valid non-zero setpoint
        #   - the device is not in Manual Dimmer mode (sp=0 → 0 % power is valid)
        if desc.filter_zero_sp and value == 0.0:
            current = self._current_value
            in_dimmer = "Dimmer" in str(self._coordinator.data.get("tgt", ""))
            if current is not None and current != 0.0 and not in_dimmer:
                _LOGGER.debug(
                    "NanoPID %s: dropping ghost set_value(0) — current SP %.1f, mode=%s",
                    self._coordinator.mac,
                    current,
                    self._coordinator.data.get("tgt", "?"),
                )
                return

        # Optimistic update: reflect the new value in HA before device confirms.
        self._current_value = value
        self.async_write_ha_state()

        from homeassistant.components import mqtt

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
        """Receive a new status payload from the coordinator."""
        raw = self._coordinator.data.get(self.entity_description.status_key)
        if raw is None:
            self.async_write_ha_state()
            return

        value = float(raw)

        if self.entity_description.filter_zero_sp:
            # Guard A: reject sp=0 from the coordinator.
            # The firmware always reports sp=0 in IDLE and may briefly report
            # sp=0 during RUN transitions.  Accepting these would reposition
            # the slider to 0 and trigger guard B ghost events on next interaction.
            # Accept only non-zero values; the user-set value is preserved otherwise.
            if value != 0.0:
                self._current_value = value
        else:
            self._current_value = value

        self.async_write_ha_state()
