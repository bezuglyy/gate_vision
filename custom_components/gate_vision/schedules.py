"""Расписания запуска реле ворот.

Позволяет задать несколько автоматизаций: время, дни недели и действие
(импульс / открыть / закрыть / стоп). Проверка раз в 20 секунд, срабатывание
один раз в сутки на каждое расписание.
"""

from __future__ import annotations

import asyncio
import logging
import time
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
    EVENT_SCHEDULE_UNCONFIRMED,
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

    require = str(item.get("require_state") or "any")
    if require not in ("any", STATE_OPEN, STATE_CLOSED, "moving"):
        require = "any"

    return {
        "id": str(item.get("id") or f"s{index}"),
        "name": str(item.get("name") or f"Автоматизация {index}"),
        "enabled": bool(item.get("enabled", True)),
        "time": time_value,
        "days": days,
        "action": action,
        "require_state": require,  # выполнять, только если состояние совпадает
        "verify": bool(item.get("verify", True)),  # проверять результат по камере
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
        """Выполнить действие расписания с проверкой состояния до и после."""
        action = item.get("action", ACTION_IMPULSE)
        data = self.coordinator.data or {}
        state = data.get("state")
        moving = bool(data.get("moving"))
        name = item.get("name") or item.get("id")

        # --- проверка состояния ПЕРЕД выполнением ---
        require = item.get("require_state", "any")
        if require != "any":
            if require == "moving" and not moving:
                _LOGGER.info("gate_vision: «%s» — ворота не движутся, пропуск", name)
                await self._log_skip(item, "ворота не движутся")
                return
            if require in (STATE_OPEN, STATE_CLOSED) and state != require:
                _LOGGER.info("gate_vision: «%s» — состояние не «%s», пропуск", name, require)
                await self._log_skip(item, f"состояние {state} ≠ {require}")
                return

        # «привести в состояние»: импульс только если это нужно
        if action == ACTION_OPEN and state == STATE_OPEN:
            await self._log_skip(item, "уже открыто")
            return
        if action == ACTION_CLOSE and state == STATE_CLOSED:
            await self._log_skip(item, "уже закрыто")
            return
        if action == ACTION_STOP and not moving:
            await self._log_skip(item, "ворота не движутся")
            return
        if action in (ACTION_OPEN, ACTION_CLOSE, ACTION_STOP) and state == STATE_UNKNOWN:
            await self._log_skip(item, "состояние неизвестно")
            return

        try:
            text = await async_relay_pulse(self.hass, self.coordinator.settings.control)
        except HomeAssistantError as err:
            _LOGGER.warning("gate_vision: расписание «%s» не выполнено: %s", name, err)
            await self._log_skip(item, f"ошибка реле: {err}")
            return

        self._entry(
            item,
            f"расписание «{name}» ({item.get('time')}) — {action}; {text}",
            state,
            action,
        )

        # --- проверка результата ПОСЛЕ выполнения ---
        if item.get("verify", True):
            expected = {ACTION_OPEN: STATE_OPEN, ACTION_CLOSE: STATE_CLOSED}.get(action)
            if expected:
                ok = await self._wait_state(expected, timeout=int(item.get("verify_timeout", 45)))
                if ok:
                    _LOGGER.info("gate_vision: «%s» — подтверждено: %s", name, expected)
                    self._entry(item, f"расписание «{name}»: подтверждено состояние «{expected}»",
                                expected, action)
                else:
                    current = (self.coordinator.data or {}).get("state")
                    _LOGGER.warning(
                        "gate_vision: «%s» — состояние не стало «%s» (сейчас %s)", name, expected, current
                    )
                    self._entry(item, f"расписание «{name}»: НЕ подтверждено «{expected}» (сейчас {current})",
                                current, action, event=EVENT_SCHEDULE_UNCONFIRMED)

    async def _wait_state(self, expected: str, timeout: int = 45) -> bool:
        """Дождаться состояния по камере (опрос каждые 2 с)."""
        deadline = time.monotonic() + max(5, timeout)
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            try:
                await self.coordinator.async_request_refresh()
            except Exception:  # noqa: BLE001
                continue
            if (self.coordinator.data or {}).get("state") == expected:
                return True
        return False

    async def _log_skip(self, item: dict[str, Any], why: str) -> None:
        name = item.get("name") or item.get("id")
        self._entry(item, f"расписание «{name}» пропущено: {why}", None, item.get("action"))

    def _entry(
        self,
        item: dict[str, Any],
        reason: str,
        state: str | None,
        action: str | None,
        event: str = EVENT_SCHEDULE,
    ) -> None:
        entry = {
            "ts": dt_util.utcnow().isoformat(),
            "event": event,
            "state": state,
            "reason": reason,
            "schedule": item.get("id"),
            "action": action,
        }
        self.coordinator.event_log.append(entry)
        self.coordinator._last_event = entry  # noqa: SLF001
        self.hass.bus.async_fire(event, {"entry_id": self.coordinator.entry.entry_id, **entry})
