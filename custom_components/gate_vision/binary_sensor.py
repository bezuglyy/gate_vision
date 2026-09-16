"""Бинарные сенсоры gate_vision: ворота, движение полотна, камера."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_UNKNOWN,
    VERSION,
)
from .coordinator import GateVisionCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GateVisionCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            GateBinarySensor(coordinator, entry),
            GateMovingBinarySensor(coordinator, entry),
            GateCameraBinarySensor(coordinator, entry),
        ]
    )


class GateVisionBase(CoordinatorEntity[GateVisionCoordinator]):
    """Общая часть сущностей gate_vision."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": MANUFACTURER,
            "model": "Определение состояния по камере",
            "sw_version": VERSION,
        }

    @property
    def data(self) -> dict[str, Any]:
        return self.coordinator.data or {}


class GateBinarySensor(GateVisionBase, BinarySensorEntity):
    """Главный сенсор: ворота открыты / закрыты (device_class garage_door)."""

    _attr_device_class = BinarySensorDeviceClass.GARAGE_DOOR
    _attr_translation_key = "gate"
    _attr_icon = "mdi:garage-variant"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_gate"
        self._attr_name = None  # имя устройства

    @property
    def is_on(self) -> bool | None:
        state = self.data.get("state")
        if state == STATE_OPEN:
            return True
        if state == STATE_CLOSED:
            return False
        return None

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success) and self.data.get(
            "state"
        ) in (
            STATE_OPEN,
            STATE_CLOSED,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.data
        return {
            "state": data.get("state"),
            "raw_state": data.get("raw_state"),
            "reason": data.get("reason"),
            "mode": data.get("mode"),
            "moving": data.get("moving"),
            "stale": data.get("stale"),
            "camera_ok": data.get("camera_ok"),
            "b_open": data.get("b_open"),
            "b_closed": data.get("b_closed"),
            "windows": data.get("windows"),
            "changed_at": data.get("changed_at"),
            "open_since": data.get("open_since"),
            "open_seconds": data.get("open_seconds"),
            "left_open": data.get("left_open"),
            "frame": data.get("frame"),
            "zones_used": data.get("zones_used"),
            "snapshot_url": data.get("url"),
            "learn_mode": getattr(self.coordinator.settings, "learn_mode", False),
        }


class GateMovingBinarySensor(GateVisionBase, BinarySensorEntity):
    """Диагностика: полотно ворот движется."""

    _attr_device_class = BinarySensorDeviceClass.MOVING
    _attr_translation_key = "moving"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_moving"

    @property
    def is_on(self) -> bool:
        return bool(self.data.get("moving"))

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success)


class GateCameraBinarySensor(GateVisionBase, BinarySensorEntity):
    """Диагностика: камера ворот отвечает."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "camera"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_camera"

    @property
    def is_on(self) -> bool:
        return bool(self.data.get("camera_ok"))

    @property
    def available(self) -> bool:
        return True
