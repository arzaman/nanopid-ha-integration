"""Button platform for NanoPID — start / stop / pause / resume commands."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NanoPIDCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL, TOPIC_COMMAND


@dataclass(frozen=True)
class NanoPIDButtonDescription(ButtonEntityDescription):
    """Extends ButtonEntityDescription with the MQTT payload string."""
    payload: str = ""


BUTTON_DESCRIPTIONS: tuple[NanoPIDButtonDescription, ...] = (
    NanoPIDButtonDescription(
        key="start",
        name="Start",
        icon="mdi:play-circle",
        payload="start",
    ),
    NanoPIDButtonDescription(
        key="stop",
        name="Stop",
        icon="mdi:stop-circle",
        payload="stop",
    ),
    NanoPIDButtonDescription(
        key="pause",
        name="Pause",
        icon="mdi:pause-circle",
        payload="pause",
    ),
    NanoPIDButtonDescription(
        key="resume",
        name="Resume",
        icon="mdi:play-pause",
        payload="resume",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NanoPIDCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NanoPIDButton(coordinator, desc) for desc in BUTTON_DESCRIPTIONS
    )


class NanoPIDButton(ButtonEntity):
    """A button entity that publishes a plain-string command to the device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: NanoPIDCoordinator,
        description: NanoPIDButtonDescription,
    ) -> None:
        self.entity_description: NanoPIDButtonDescription = description
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.mac}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.mac)},
            name=self._coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_press(self) -> None:
        from homeassistant.components import mqtt

        topic = TOPIC_COMMAND.format(mac=self._coordinator.mac)
        await mqtt.async_publish(self.hass, topic, self.entity_description.payload, qos=1)
