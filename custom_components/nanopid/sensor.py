"""Sensor platform for NanoPID — reads nanopid/<mac>/status JSON."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NanoPIDCoordinator
from .const import DOMAIN, FSM_MAP, MANUFACTURER, MODEL


@dataclass(frozen=True)
class NanoPIDSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value extractor."""
    value_fn: Callable[[dict], str | float | int | None] = lambda _: None


def _fsm_to_text(data: dict) -> str | None:
    raw = data.get("fsm")
    if raw is None:
        return None
    return FSM_MAP.get(int(raw), f"Unknown ({raw})")


SENSOR_DESCRIPTIONS: tuple[NanoPIDSensorDescription, ...] = (
    NanoPIDSensorDescription(
        key="temperature",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("temp"),
    ),
    NanoPIDSensorDescription(
        key="power_output",
        name="Power Output",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda d: d.get("pwr"),
    ),
    NanoPIDSensorDescription(
        key="process_status",
        name="Process Status",
        icon="mdi:state-machine",
        value_fn=_fsm_to_text,
    ),
    NanoPIDSensorDescription(
        key="alarm_status",
        name="Alarm Status",
        icon="mdi:alarm-light",
        value_fn=lambda d: d.get("alarm"),
    ),
    NanoPIDSensorDescription(
        key="free_heap",
        name="Free Heap",
        native_unit_of_measurement="B",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("heap"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NanoPIDCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NanoPIDSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class NanoPIDSensor(SensorEntity):
    """A sensor entity backed by the NanoPID status MQTT topic."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: NanoPIDCoordinator,
        description: NanoPIDSensorDescription,
    ) -> None:
        self.entity_description: NanoPIDSensorDescription = description
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.mac}_{description.key}"
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
    def native_value(self) -> str | float | int | None:
        return self.entity_description.value_fn(self._coordinator.data)

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
