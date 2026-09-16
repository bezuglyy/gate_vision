"""Сенсоры gate_vision: диагностика и статистика."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .binary_sensor import GateVisionBase
from .const import DOMAIN, STATE_CLOSED, STATE_OPEN, STATE_UNKNOWN
from .coordinator import GateVisionCoordinator

MODE_NAMES = {"day": "день (цвет)", "ir": "ночь/ИК (ч/б)"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GateVisionCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            FrameModeSensor(coordinator, entry),
            ZoneOpenBrightnessSensor(coordinator, entry),
            ZoneClosedBrightnessSensor(coordinator, entry),
            OpenDurationSensor(coordinator, entry),
            LastChangeSensor(coordinator, entry),
            LastEventSensor(coordinator, entry),
            DetectionInfoSensor(coordinator, entry),
        ]
    )


class FrameModeSensor(GateVisionBase, SensorEntity):
    """Режим кадра: день или ночь/ИК."""

    _attr_translation_key = "frame_mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:theme-light-dark"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_frame_mode"

    @property
    def native_value(self) -> str | None:
        mode = self.data.get("mode")
        return MODE_NAMES.get(mode, mode) if mode else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "mode": self.data.get("mode"),
            "mean_sat": self.data.get("mean_sat"),
            "frame_mean": self.data.get("frame_mean"),
        }


class _BrightnessSensor(GateVisionBase, SensorEntity):
    """Яркость зоны (диагностика)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "ярк."
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:brightness-6"

    def __init__(
        self, coordinator: GateVisionCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def native_value(self) -> float | None:
        value = self.data.get(self._key)
        return float(value) if isinstance(value, (int, float)) else None


class ZoneOpenBrightnessSensor(_BrightnessSensor):
    """Яркость зоны «открыто» (низ проёма)."""

    _attr_translation_key = "zone_open_brightness"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "b_open")


class ZoneClosedBrightnessSensor(_BrightnessSensor):
    """Яркость зоны «закрыто» (окна полотна)."""

    _attr_translation_key = "zone_closed_brightness"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "b_closed")


class OpenDurationSensor(GateVisionBase, SensorEntity):
    """Сколько ворот открыты (секунды)."""

    _attr_translation_key = "open_duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_open_duration"

    @property
    def native_value(self) -> int | None:
        if self.data.get("state") != STATE_OPEN:
            return 0
        return int(self.data.get("open_seconds") or 0)


class LastChangeSensor(GateVisionBase, SensorEntity):
    """Когда состояние изменилось."""

    _attr_translation_key = "last_change"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_change"

    @property
    def native_value(self):
        value = self.data.get("changed_at")
        if not value:
            return None
        from homeassistant.util import dt as dt_util

        return dt_util.parse_datetime(value)


class LastEventSensor(GateVisionBase, SensorEntity):
    """Последнее событие ворот."""

    _attr_translation_key = "last_event"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:history"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_event"

    @property
    def native_value(self) -> str | None:
        event = self.data.get("last_event") or {}
        return event.get("event")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        event = self.data.get("last_event") or {}
        return {"ts": event.get("ts"), "reason": event.get("reason")}


class DetectionInfoSensor(GateVisionBase, SensorEntity):
    """Текстовое описание текущего решения детектора."""

    _attr_translation_key = "detection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:text-box-search-outline"

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_detection"

    @property
    def native_value(self) -> str | None:
        state = self.data.get("state")
        if state == STATE_OPEN:
            return "открыто"
        if state == STATE_CLOSED:
            return "закрыто"
        if state == STATE_UNKNOWN:
            return "неизвестно"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "reason": self.data.get("reason"),
            "zones": self.data.get("zones"),
            "profile_diff": self.data.get("profile_diff"),
            "bands": (self.data.get("bands") or [])[:3],
        }
