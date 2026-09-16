"""gate_vision — определение состояния подъёмных ворот по камере (Home Assistant).

Интеграция читает кадр камеры ворот и определяет бинарное состояние ЗАКРЫТО/ОТКРЫТО,
публикует сущности, события и (опционально) управление. Настройка зон и реагирований —
в панели «Ворота» в боковом меню.
"""

from __future__ import annotations

import logging

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import GateVisionCoordinator
from .panel import async_register_panel, async_unregister_panel
from .schedules import ScheduleRunner
from .settings import get_settings
from .views import (
    GateEventsView,
    GateFrameView,
    GateLearnView,
    GateSettingsView,
    GateStateView,
    GateTestView,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.COVER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настроить интеграцию по записи конфигурации."""
    settings = get_settings(hass, entry)
    coordinator = GateVisionCoordinator(hass, entry, settings)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await _async_register_http(hass)
    await async_register_panel(hass)

    runner = ScheduleRunner(hass, coordinator)
    runner.start()
    hass.data[DOMAIN][f"runner:{entry.entry_id}"] = runner

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.info("gate_vision: интеграция запущена (%s)", entry.title)
    return True


async def _async_register_http(hass: HomeAssistant) -> None:
    """Зарегистрировать HTTP-эндпоинты интеграции (один раз)."""
    if hass.data.get(f"{DOMAIN}_views_registered"):
        return
    for view in (
        GateFrameView(),
        GateStateView(),
        GateSettingsView(),
        GateTestView(),
        GateEventsView(),
        GateLearnView(),
    ):
        hass.http.register_view(view)
    hass.data[f"{DOMAIN}_views_registered"] = True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузить интеграцию."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: GateVisionCoordinator | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator is not None:
            await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(f"settings:{entry.entry_id}", None)
        if not [k for k in hass.data[DOMAIN] if not str(k).startswith("settings:")]:
            await async_unregister_panel(hass)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Применить изменённые настройки без полной перезагрузки.

    Зоны/пороги/реагирования читаются координатором «на лету»; интервал опроса
    обновляем здесь; полная перезагрузка нужна только при включении/выключении
    управления (тогда появляется/исчезает сущность cover).
    """
    settings = get_settings(hass, entry)
    settings.reload()
    coordinator: GateVisionCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return
    coordinator.update_interval = timedelta(seconds=max(1, settings.scan_interval))
    enabled = bool(settings.control.get("enabled"))
    if enabled != coordinator.control_enabled_applied:
        _LOGGER.info("gate_vision: управление %s — перезагружаю запись", "включено" if enabled else "выключено")
        await hass.config_entries.async_reload(entry.entry_id)
        return
    # набор сущностей зон изменился (включили/выключили сущность зоны) — нужна перезагрузка
    zone_entities = sorted(z["id"] for z in settings.zones if z.get("entity"))
    if zone_entities != coordinator.zone_entities_applied:
        _LOGGER.info("gate_vision: сущности зон %s — перезагружаю запись", zone_entities)
        await hass.config_entries.async_reload(entry.entry_id)
