"""Управление воротами (этап 4). По умолчанию ВЫКЛЮЧЕНО.

Логика ворот: один импульсный вход, цикл Закрыто →импульс→ Открывается →импульс→ Стоп →
→импульс→ Закрывается → … Направление следующего пуска противоположно последнему движению,
поэтому интеграция помнит последнее направление.

Защита:
  * команды не выполняются, пока состояние неизвестно;
  * перед закрытием (если включено) проверяется, что камера видит проём и он пуст;
  * после импульса ждём подтверждения по камере (confirm_timeout), иначе сообщаем об ошибке.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .binary_sensor import GateVisionBase
from .const import DOMAIN, STATE_CLOSED, STATE_OPEN, STATE_UNKNOWN
from .coordinator import GateVisionCoordinator
from .relay import async_relay_pulse

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GateVisionCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.settings.control.get("enabled"):
        _LOGGER.info(
            "gate_vision: управление выключено (control.enabled=false) — cover не создаётся"
        )
        return
    async_add_entities([GateCover(coordinator, entry)])


class GateCover(GateVisionBase, CoverEntity):
    """Ворота как cover: состояние — по камере, команды — импульсом."""

    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_translation_key = "gate"
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, coordinator: GateVisionCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cover"
        self._attr_name = None
        self._last_direction: str | None = None
        self._busy = False

    # ------------------------------------------------------------- состояние
    @property
    def is_closed(self) -> bool | None:
        state = self.data.get("state")
        if state == STATE_CLOSED:
            return True
        if state == STATE_OPEN:
            return False
        return None

    @property
    def is_opening(self) -> bool:
        return bool(self.data.get("moving")) and self._last_direction == "open"

    @property
    def is_closing(self) -> bool:
        return bool(self.data.get("moving")) and self._last_direction == "close"

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
        return {
            "last_direction": self._last_direction,
            "control": {
                k: v
                for k, v in self.coordinator.settings.control.items()
                if k != "password"
            },
        }

    # ------------------------------------------------------------- команды
    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._command("open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._command("close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._command("stop")

    async def _command(self, action: str) -> None:
        if self._busy:
            raise HomeAssistantError("gate_vision: предыдущая команда ещё выполняется")
        state = self.data.get("state")
        if state == STATE_UNKNOWN:
            raise HomeAssistantError(
                "gate_vision: состояние ворот неизвестно — команда отменена"
            )
        if action == "open" and state == STATE_OPEN:
            return
        if action == "close" and state == STATE_CLOSED:
            return
        if action == "close" and self.coordinator.settings.control.get(
            "check_clear_before_close"
        ):
            if not self.data.get("camera_ok"):
                raise HomeAssistantError(
                    "gate_vision: камера недоступна — закрытие отменено"
                )

        self._busy = True
        try:
            await self._impulse()
            self._last_direction = {"open": "open", "close": "close"}.get(action)
            await self._wait_confirmation(
                target="closed" if action == "close" else "open"
            )
        finally:
            self._busy = False

    async def _impulse(self) -> None:
        """Импульс на реле ворот (общая логика с расписаниями)."""
        await async_relay_pulse(self.hass, self.coordinator.settings.control)

    async def _wait_confirmation(self, target: str) -> None:
        """Дождаться подтверждения по камере."""
        timeout = int(self.coordinator.settings.control.get("confirm_timeout", 45))
        deadline = dt_util.utcnow() + timedelta(seconds=timeout)
        while dt_util.utcnow() < deadline:
            await asyncio.sleep(2)
            state = self.data.get("state")
            if target == "closed" and state == STATE_CLOSED:
                return
            if target == "open" and state == STATE_OPEN:
                return
        _LOGGER.warning(
            "gate_vision: нет подтверждения состояния '%s' за %s с", target, timeout
        )
        self.hass.bus.async_fire(
            f"{DOMAIN}_command_unconfirmed",
            {"entry_id": self._entry.entry_id, "target": target, "timeout": timeout},
        )
