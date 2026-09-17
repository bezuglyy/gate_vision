"""Настройка gate_vision через интерфейс Home Assistant."""

from __future__ import annotations

import io
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from PIL import Image

from .const import (
    CONF_CAMERA_ENTITY,
    CONF_GO2RTC_BASE,
    CONF_LEARN_MODE,
    CONF_SCAN_INTERVAL,
    CONF_SNAPSHOT_URL,
    DEFAULT_GO2RTC_BASE,
    DEFAULT_LEFT_OPEN_MIN,
    DEFAULT_RTSP,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    NAME,
)
from .coordinator import build_snapshot_url
from .detector import analyze

_LOGGER = logging.getLogger(__name__)


async def _check_source(
    hass, camera_entity: str, snapshot_url: str, go2rtc_base: str
) -> dict[str, Any] | None:
    """Проверить источник кадра: сущность camera.* (приоритет) или URL/поток.

    Возвращает результат анализа кадра или None, если источник не читается.
    """
    if camera_entity:
        try:
            from homeassistant.components import camera as camera_component

            image = await camera_component.async_get_image(hass, camera_entity, timeout=15)
            raw = getattr(image, "content", b"") or b""
            if len(raw) < 5000:
                _LOGGER.warning(
                    "gate_vision: камера %s вернула пустой кадр (%s байт)", camera_entity, len(raw)
                )
                return None
            img = await hass.async_add_executor_job(
                lambda: Image.open(io.BytesIO(raw)).convert("RGB")
            )
            return await hass.async_add_executor_job(analyze, img, None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("gate_vision: камера %s не проверена: %s", camera_entity, err)
            return None
    if not (snapshot_url or "").strip():
        _LOGGER.warning("gate_vision: не указаны ни камера, ни адрес кадра")
        return None
    return await _check_camera(hass, snapshot_url, go2rtc_base)


async def _check_camera(
    hass, snapshot_url: str, go2rtc_base: str
) -> dict[str, Any] | None:
    """Проверить, что кадр с камеры читается и детектор даёт результат."""
    url = build_snapshot_url(snapshot_url, go2rtc_base)
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            for attempt in range(3):
                try:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        raw = await resp.read()
                    if len(raw) < 5000:
                        raise RuntimeError("слишком маленький кадр")
                    img = await hass.async_add_executor_job(
                        lambda: Image.open(io.BytesIO(raw)).convert("RGB")
                    )
                    return await hass.async_add_executor_job(analyze, img, None)
                except Exception:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    import asyncio

                    await asyncio.sleep(2)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("gate_vision: камера не проверена: %s", err)
        return None
    return None


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("name", NAME)): str,
            vol.Optional(
                CONF_CAMERA_ENTITY, default=defaults.get(CONF_CAMERA_ENTITY, "")
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="camera")),
            vol.Optional(
                CONF_SNAPSHOT_URL, default=defaults.get(CONF_SNAPSHOT_URL, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_GO2RTC_BASE,
                default=defaults.get(CONF_GO2RTC_BASE, DEFAULT_GO2RTC_BASE),
            ): selector.TextSelector(),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=1,
                    unit_of_measurement="с",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


class GateVisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Первичная настройка интеграции."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.pop("name", NAME)
            await self.async_set_unique_id(
                f"{user_input.get(CONF_SNAPSHOT_URL)}|{user_input.get(CONF_GO2RTC_BASE)}"
            )
            self._abort_if_unique_id_configured()
            result = await _check_source(
                self.hass,
                user_input.get(CONF_CAMERA_ENTITY, ""),
                user_input.get(CONF_SNAPSHOT_URL, ""),
                user_input.get(CONF_GO2RTC_BASE, DEFAULT_GO2RTC_BASE),
            )
            if result is None:
                errors["base"] = (
                    "no_source"
                    if not (user_input.get(CONF_CAMERA_ENTITY) or "").strip()
                    and not (user_input.get(CONF_SNAPSHOT_URL) or "").strip()
                    else "cannot_connect"
                )
            else:
                return self.async_create_entry(title=name, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema({}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GateVisionOptionsFlow()


class GateVisionOptionsFlow(OptionsFlow):
    """Изменение настроек: камера, интервал, режим обучения, порог «оставлены открытыми»."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        merged = {**entry.data, **entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(user_input)
            data.pop("name", None)
            if await _check_source(
                self.hass,
                data.get(CONF_CAMERA_ENTITY, ""),
                data.get(CONF_SNAPSHOT_URL, ""),
                data.get(CONF_GO2RTC_BASE, DEFAULT_GO2RTC_BASE),
            ) is None and not data.get("skip_check"):
                errors["base"] = "cannot_connect"
            else:
                data.pop("skip_check", None)
                return self.async_create_entry(title="", data=data)

        schema = _schema(merged).extend(
            {
                vol.Optional(
                    CONF_LEARN_MODE, default=bool(merged.get(CONF_LEARN_MODE, False))
                ): selector.BooleanSelector(),
                vol.Optional(
                    "left_open_min",
                    default=int(merged.get("left_open_min", DEFAULT_LEFT_OPEN_MIN)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=720,
                        step=1,
                        unit_of_measurement="мин",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional("skip_check", default=False): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
