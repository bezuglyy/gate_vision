"""HTTP API интеграции gate_vision (используется панелью и внешними инструментами)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import (
    AUTOCAL_COUNT,
    AUTOCAL_DELAY,
    CONF_CONTROL,
    MAX_SAMPLES,
    CONF_LEARN_MODE,
    CONF_REACTIONS,
    CONF_SCHEDULES,
    CONF_INTERLOCKS,
    URL_INTERLOCKS,
    CONF_THRESHOLDS,
    CONF_ZONES,
    DOMAIN,
    URL_EVENTS,
    URL_FRAME,
    URL_SETTINGS,
    URL_STATE,
    URL_TEST,
    URL_LEARN,
    URL_CAMERAS,
)
from .coordinator import GateVisionCoordinator
from .settings import Settings

_LOGGER = logging.getLogger(__name__)


def _all_coordinators(hass: HomeAssistant) -> dict[str, GateVisionCoordinator]:
    """Все запущенные координаторы интеграции: entry_id -> координатор."""
    out: dict[str, GateVisionCoordinator] = {}
    for key, value in hass.data.get(DOMAIN, {}).items():
        if isinstance(key, str) and key.startswith("settings:"):
            continue
        if isinstance(value, GateVisionCoordinator):
            out[str(key)] = value
    return out


def _coordinator(
    hass: HomeAssistant, entry_id: str | None = None
) -> GateVisionCoordinator | None:
    """Координатор выбранной камеры (или первой, если не указана)."""
    items = _all_coordinators(hass)
    if entry_id and entry_id in items:
        return items[entry_id]
    return next(iter(items.values()), None)


def _settings(hass: HomeAssistant, entry_id: str | None = None) -> Settings | None:
    """Настройки выбранной камеры (или первой)."""
    if entry_id:
        store = hass.data.get(DOMAIN, {})
        settings = store.get(f"settings:{entry_id}")
        if isinstance(settings, Settings):
            return settings
    for key, value in hass.data.get(DOMAIN, {}).items():
        if isinstance(key, str) and key.startswith("settings:") and isinstance(value, Settings):
            return value
    return None


def _cover_entity(hass: HomeAssistant, entry_id: str | None) -> str | None:
    """entity_id сущности cover этой записи (по unique_id ``<entry_id>_cover``)."""
    if not entry_id:
        return None
    try:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(hass)
        return reg.async_get_entity_id("cover", DOMAIN, f"{entry_id}_cover")
    except Exception:  # noqa: BLE001
        return None

def _entry_id(request: web.Request, hass: HomeAssistant) -> str | None:
    """entry_id из запроса или из самого координатора."""
    entry_id = request.query.get("entry_id")
    if entry_id:
        return entry_id
    coordinator = _coordinator(hass)
    return coordinator.entry.entry_id if coordinator else None


class GateFrameView(HomeAssistantView):
    """Отдать последний кадр камеры ворот (для панели)."""

    url = URL_FRAME
    name = f"{DOMAIN}:frame"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = _coordinator(hass, _entry_id(request, hass))
        if coordinator is None:
            return web.Response(status=404, text="gate_vision не настроен")
        if request.query.get("fresh"):
            try:
                await coordinator.async_request_refresh()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("gate_vision: обновление кадра не удалось: %s", err)
        frame = coordinator.last_frame
        if not frame:
            return web.Response(status=503, text="кадр недоступен")
        return web.Response(
            body=frame,
            content_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )


class GateStateView(HomeAssistantView):
    """Текущее состояние и результат детектора."""

    url = URL_STATE
    name = f"{DOMAIN}:state"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = _coordinator(hass, _entry_id(request, hass))
        settings = _settings(hass, _entry_id(request, hass))
        if coordinator is None:
            return web.json_response({"error": "не настроено"}, status=404)
        if request.query.get("fresh"):
            try:
                await coordinator.async_request_refresh()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("gate_vision: обновление не удалось: %s", err)
        entry_id = _entry_id(request, hass)
        payload = {
            "state": (coordinator.data or {}).get("state"),
            "analysis": coordinator.data or {},
            "settings": settings.as_dict() if settings else {},
            "event_log": list(coordinator.event_log)[-50:],
            "cover_entity": _cover_entity(hass, entry_id),
            "interlocks": _interlocks_payload(hass, settings),
            "control_enabled": bool(
                settings and settings.control.get("enabled")
            ),
        }
        return web.json_response(payload)


class GateSettingsView(HomeAssistantView):
    """Чтение и запись настроек (зоны, пороги, реагирования, управление)."""

    url = URL_SETTINGS
    name = f"{DOMAIN}:settings"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        settings = _settings(hass, _entry_id(request, hass))
        if settings is None:
            return web.json_response({"error": "не настроено"}, status=404)
        return web.json_response(settings.as_dict())

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        settings = _settings(hass)
        coordinator = _coordinator(hass)
        if settings is None:
            return web.json_response({"error": "не настроено"}, status=404)
        try:
            body: dict[str, Any] = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "некорректный JSON"}, status=400)

        patch: dict[str, Any] = {}
        for key in (
            CONF_ZONES,
            CONF_THRESHOLDS,
            CONF_REACTIONS,
            CONF_CONTROL,
            CONF_SCHEDULES,
            CONF_INTERLOCKS,
            CONF_LEARN_MODE,
            "left_open_min",
            "scan_interval",
            "camera_entity",
            "snapshot_url",
            "go2rtc_base",
        ):
            if key in body:
                patch[key] = body[key]
        if not patch:
            return web.json_response({"error": "нечего менять"}, status=400)

        await settings.async_save(patch)

        if coordinator is not None:
            if "scan_interval" in patch:
                coordinator.update_interval = coordinator.update_interval.__class__(
                    seconds=max(1, settings.scan_interval)
                )
            if request.query.get("refresh"):
                try:
                    await coordinator.async_request_refresh()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug(
                        "gate_vision: обновление после настроек не удалось: %s", err
                    )
        return web.json_response({"ok": True, "settings": settings.as_dict()})


class GateTestView(HomeAssistantView):
    """Принудительная проверка кадра (кнопка «Проверить сейчас»)."""

    url = URL_TEST
    name = f"{DOMAIN}:test"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = _coordinator(hass, _entry_id(request, hass))
        if coordinator is None:
            return web.json_response({"error": "не настроено"}, status=404)
        try:
            await coordinator.async_request_refresh()
        except Exception as err:  # noqa: BLE001
            return web.json_response({"error": str(err)}, status=502)
        return web.json_response({"ok": True, "analysis": coordinator.data or {}})


class GateEventsView(HomeAssistantView):
    """Журнал событий интеграции."""

    url = URL_EVENTS
    name = f"{DOMAIN}:events"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = _coordinator(hass, _entry_id(request, hass))
        if coordinator is None:
            return web.json_response({"error": "не настроено"}, status=404)
        limit = int(request.query.get("limit", 50))
        return web.json_response({"events": list(coordinator.event_log)[-limit:]})


class GateLearnView(HomeAssistantView):
    """Обучение состояний зоны: запомнить текущий замер как «открыто» или «закрыто»."""

    url = URL_LEARN
    name = f"{DOMAIN}:learn"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        coordinator = _coordinator(hass, _entry_id(request, hass))
        settings = _settings(hass, _entry_id(request, hass))
        if settings is None or coordinator is None:
            return web.json_response({"error": "не настроено"}, status=404)
        try:
            body: dict[str, Any] = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "некорректный JSON"}, status=400)

        zone_id = str(body.get("zone_id") or "")
        action = str(body.get("action") or "learn")
        zone = next((z for z in settings.zones if z["id"] == zone_id), None)
        if zone is None:
            return web.json_response({"error": f"зона {zone_id} не найдена"}, status=404)

        # сколько замеров собрать: 1 или автокалибровка (медиана из N)
        try:
            count = max(1, min(AUTOCAL_COUNT, int(body.get("count") or 1)))
        except (TypeError, ValueError):
            count = 1

        measured = None
        values: list[float] = []
        bands: list[bool] = []
        for attempt in range(count):
            if attempt:
                await asyncio.sleep(AUTOCAL_DELAY)
            try:
                await coordinator.async_request_refresh()
            except Exception as err:  # noqa: BLE001
                return web.json_response({"error": f"кадр недоступен: {err}"}, status=502)
            measured = next(
                (z for z in (coordinator.data or {}).get("zones", []) if z.get("id") == zone_id), None
            )
            if measured is None:
                return web.json_response({"error": "нет замера зоны"}, status=502)
            values.append(float(measured["mean"]))
            bands.append(bool(measured.get("band")))

        import statistics

        value = round(statistics.median(values), 1) if values else 0.0

        zones = [dict(z) for z in settings.zones]
        target = next(z for z in zones if z["id"] == zone_id)
        samples = {k: list(v or []) for k, v in (target.get("samples") or {}).items()}
        samples.setdefault("open", [])
        samples.setdefault("closed", [])

        if action == "reset":
            samples = {"open": [], "closed": []}
            message = "обучение сброшено"
        else:
            state = str(body.get("state") or "")
            if state not in ("open", "closed"):
                return web.json_response({"error": "state должен быть open или closed"}, status=400)
            entry = {"mean": value, "band": (sum(bands) * 2 >= len(bands)) if bands else None}
            samples[state] = (samples[state] + [entry])[-MAX_SAMPLES:]
            message = (
                f"запомнено «{state}» = {value}"
                + (f" (медиана из {count} замеров)" if count > 1 else "")
                + f", всего замеров {len(samples[state])}"
            )

        target["samples"] = samples
        await settings.async_save({"zones": zones})
        return web.json_response({"ok": True, "message": message, "zones": settings.as_dict()["zones"]})


class GateCamerasView(HomeAssistantView):
    """Список всех камер интеграции (для сетки в панели)."""

    url = URL_CAMERAS
    name = f"{DOMAIN}:cameras"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        items = []
        for entry_id, coordinator in _all_coordinators(hass).items():
            data = coordinator.data or {}
            settings = _settings(hass, entry_id)
            items.append(
                {
                    "entry_id": entry_id,
                    "title": coordinator.entry.title,
                    "state": data.get("state"),
                    "moving": bool(data.get("moving")),
                    "camera_ok": data.get("camera_ok"),
                    "frame": data.get("frame"),
                    "reason": data.get("reason"),
                    "has_frame": coordinator.last_frame is not None,
                    "camera_entity": getattr(settings, "camera_entity", "") if settings else "",
                    "cover_entity": _cover_entity(hass, entry_id),
                    "control_enabled": bool(settings and settings.control.get("enabled")),
                    "zones": [
                        {
                            "id": z.get("id"),
                            "name": z.get("name"),
                            "role": z.get("role"),
                            "x": z.get("x"),
                            "y": z.get("y"),
                            "w": z.get("w"),
                            "h": z.get("h"),
                            "state": z.get("state"),
                        }
                        for z in (data.get("zones") or [])
                    ],
                }
            )
        items.sort(key=lambda i: i["title"])
        return web.json_response({"cameras": items, "count": len(items)})


def _interlocks_payload(hass: HomeAssistant, settings: Any) -> dict[str, Any]:
    """Правила запретов + какие срабатывают сейчас."""
    if settings is None:
        return {"rules": [], "active": []}
    from .interlocks import active_for_actions

    rules = list(getattr(settings, "interlocks", []) or [])
    active = active_for_actions(hass, rules, ["open", "close", "stop", "impulse"])
    return {
        "rules": rules,
        "active": active,
        "active_text": [item["text"] for item in active],
    }


class GateInterlocksView(HomeAssistantView):
    """Запреты по сенсорам: чтение и запись списка правил."""

    url = URL_INTERLOCKS
    name = f"{DOMAIN}:interlocks"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        settings = _settings(hass, _entry_id(request, hass))
        if settings is None:
            return web.json_response({"error": "не настроено"}, status=404)
        return web.json_response(_interlocks_payload(hass, settings))

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        settings = _settings(hass, _entry_id(request, hass))
        if settings is None:
            return web.json_response({"error": "не настроено"}, status=404)
        try:
            body: dict[str, Any] = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "некорректный JSON"}, status=400)
        rules = body.get(CONF_INTERLOCKS)
        if not isinstance(rules, list):
            return web.json_response({"error": "нужен список interlocks"}, status=400)
        await settings.async_save({CONF_INTERLOCKS: rules})
        return web.json_response({"ok": True, **_interlocks_payload(hass, settings)})
