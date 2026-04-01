"""Select platform for NanoPID — read/write process configuration selectors."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NanoPIDCoordinator
from .const import (
    BEHAVIOUR_OPTIONS,
    CONTROL_MODE_OPTIONS,
    DIRECTION_OPTIONS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    PROFILE_OPTIONS,
    TARGET_MODE_OPTIONS,
    TOPIC_CONFIG,
    TOPIC_TARGET_MODE,
)


@dataclass(frozen=True)
class NanoPIDSelectDescription(SelectEntityDescription):
    """Extends SelectEntityDescription with value/command helpers."""
    options: list[str] = field(default_factory=list)
    status_key: str = ""                      # key inside status JSON
    command_topic_tpl: str = TOPIC_CONFIG     # topic template (uses .format(mac=…))
    # If command_payload_fn is None, value is sent as plain string to command_topic
    command_payload_fn: Callable[[str], str] | None = None


def _json_mode(v: str) -> str:
    return json.dumps({"mode": v})

def _json_dir(v: str) -> str:
    return json.dumps({"dir": v})

def _json_start(v: str) -> str:
    return json.dumps({"start": v})

def _json_prof(v: str) -> str:
    return json.dumps({"prof": v})


SELECT_DESCRIPTIONS: tuple[NanoPIDSelectDescription, ...] = (
    NanoPIDSelectDescription(
        key="target_mode",
        name="Target Mode",
        icon="mdi:swap-horizontal",
        options=TARGET_MODE_OPTIONS,
        status_key="tgt",
        command_topic_tpl=TOPIC_TARGET_MODE,
        command_payload_fn=None,          # plain string
    ),
    NanoPIDSelectDescription(
        key="control_mode",
        name="Control Mode",
        icon="mdi:tune-variant",
        options=CONTROL_MODE_OPTIONS,
        status_key="ctrl",
        command_topic_tpl=TOPIC_CONFIG,
        command_payload_fn=_json_mode,
    ),
    NanoPIDSelectDescription(
        key="direction",
        name="Direction",
        icon="mdi:thermometer-lines",
        options=DIRECTION_OPTIONS,
        status_key="dir",
        command_topic_tpl=TOPIC_CONFIG,
        command_payload_fn=_json_dir,
    ),
    NanoPIDSelectDescription(
        key="start_behaviour",
        name="Start Behaviour",
        icon="mdi:play-speed",
        options=BEHAVIOUR_OPTIONS,
        status_key="beh",
        command_topic_tpl=TOPIC_CONFIG,
        command_payload_fn=_json_start,
    ),
    NanoPIDSelectDescription(
        key="profile_type",
        name="Profile Type",
        icon="mdi:chart-timeline-variant",
        options=PROFILE_OPTIONS,
        status_key="prof",
        command_topic_tpl=TOPIC_CONFIG,
        command_payload_fn=_json_prof,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NanoPIDCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NanoPIDSelect(coordinator, desc) for desc in SELECT_DESCRIPTIONS
    )


class NanoPIDSelect(SelectEntity):
    """A select entity for NanoPID process configuration."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: NanoPIDCoordinator,
        description: NanoPIDSelectDescription,
    ) -> None:
        self.entity_description: NanoPIDSelectDescription = description
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.mac}_{description.key}"
        self._attr_options = description.options
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
    def current_option(self) -> str | None:
        return self._coordinator.data.get(self.entity_description.status_key)

    async def async_select_option(self, option: str) -> None:
        from homeassistant.components import mqtt

        desc = self.entity_description
        topic = desc.command_topic_tpl.format(mac=self._coordinator.mac)
        payload = desc.command_payload_fn(option) if desc.command_payload_fn else option
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
        self.async_write_ha_state()
