"""Расписания запуска реле ворот.

Позволяет задать несколько автоматизаций: время, дни недели и действие
(импульс / открыть / закрыть / стоп). Проверка раз в 20 секунд, срабатывание
один раз в сутки на каждое расписание.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_CLOSE,
    ACTION_IMPULSE,
    ACTION_OPEN,
    ACTION_STOP,
    EVENT_SCHEDULE,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_UNKNOWN,
)
from .relay import async_relay_pulse

_LOGGER = logging.getLogger(__name__)

TICK = 20  # период проверки, с


def normalize_schedule(item: dict[str, Any], index: int) -> dict[str, Any]:
    """Привести расписание к корректному виду."""
    days = item.get("days")
    if not isinstance(days, list):
        days = [0, 1, 2, 3, 4, 5, 6]
    days = sorted(
        {int(d) for d in days if isinstance(d, (int, float)) and 0 <= int(d) <= 6}
    ) or list(range(7))

    time_value = str(item.get("time") or "10:00")
    try:
        hh, mm = (int(x) for x in time_value.split(":")[:2])
        time_value = f"{min(23, max(0, hh)):02d}:{min(59, max(0, mm)):02d}"
    except (ValueError, TypeError):
        time_value = "10:00"

    action = str(item.get("action") or ACTION_IMPULSE)
    if action not in (ACTION_IMPULSE, ACTION_OPEN, ACTION_CLOSE, ACTION_STOP):
        action = ACTION_IMPULSE

    return {
        "id": str(item.get("id") or f"s{index}"),
        "name": str(item.get("name") or f"Автоматизация {index}"),
        "enabled": bool(item.get("enabled", True)),
        "time": time_value,
        "days": days,
        "action": action,
        "last_run": item.get("last_run"),
    }


class ScheduleRunner:
    """Фоновый цикл расписаний."""

    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_background_task(
                self._loop(), name="gate_vision schedules"
            )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        _LOGGER.debug("gate_vision: планировщик запущен")
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("gate_vision: ошибка планировщика: %s", err)
            await asyncio.sleep(TICK)

    async def _tick(self) -> None:
        now = dt_util.now()
        weekday = now.weekday()  # 0 = понедельник
        hhmm = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")
        schedules = list(getattr(self.coordinator.settings, "schedules", []) or [])
        changed = False

        for item in schedules:
            if not item.get("enabled") or item.get("time") != hhmm:
                continue
            if weekday not in (item.get("days") or []):
                continue
            if (item.get("last_run") or "").startswith(today):
                continue
            await self._run(item)
            item["last_run"] = now.isoformat(timespec="seconds")
            changed = True

        if changed:
            try:
                await self.coordinator.settings.async_save({"schedules": schedules})
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("gate_vision: не сохранил last_run расписаний: %s", err)

    async def _run(self, item: dict[str, Any]) -> None:
        """Выполнить действие расписания."""
        action = item.get("action", ACTION_IMPULSE)
        state = (self.coordinator.data or {}).get("state")
        name = item.get("name") or item.get("id")

        # «привести в состояние»: импульс только если это нужно
        if action == ACTION_OPEN and state == STATE_OPEN:
            _LOGGER.info("gate_vision: расписание «%s» — уже открыто, пропуск", name)
            return
        if action == ACTION_CLOSE and state == STATE_CLOSED:
            _LOGGER.info("gate_vision: расписание «%s» — уже закрыто, пропуск", name)
            return
        if action == ACTION_STOP and state not in (
            STATE_OPEN,
            STATE_CLOSED,
            STATE_UNKNOWN,
        ):
            return
        if action == ACTION_STOP and not (self.coordinator.data or {}).get("moving"):
            _LOGGER.info(
                "gate_vision: расписание «%s» — ворота не движутся, пропуск", name
            )
            return
        if action in (ACTION_OPEN, ACTION_CLOSE) and state == STATE_UNKNOWN:
            _LOGGER.warning(
                "gate_vision: расписание «%s» — состояние неизвестно, пропуск", name
            )
            return

        try:
            text = await async_relay_pulse(self.hass, self.coordinator.settings.control)
        except HomeAssistantError as err:
            _LOGGER.warning("gate_vision: расписание «%s» не выполнено: %s", name, err)
            return

        entry = {
            "ts": dt_util.utcnow().isoformat(),
            "event": EVENT_SCHEDULE,
            "state": state,
            "reason": f"расписание «{name}» ({item.get('time')}) — {action}; {text}",
            "schedule": item.get("id"),
            "action": action,
        }
        self.coordinator.event_log.append(entry)
        self.coordinator._last_event = entry  # noqa: SLF001
        self.hass.bus.async_fire(
            EVENT_SCHEDULE, {"entry_id": self.coordinator.entry.entry_id, **entry}
        )
        _LOGGER.info("gate_vision: расписание «%s» — %s", name, action)
