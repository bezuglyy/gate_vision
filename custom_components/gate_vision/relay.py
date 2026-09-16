"""Команда на реле ворот (общая для cover и расписаний)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound

_LOGGER = logging.getLogger(__name__)


async def async_relay_pulse(hass: HomeAssistant, control: dict[str, Any]) -> str:
    """Один импульс на реле ворот.

    * ``relay_type = impulse`` — реле само выдаёт импульс: только включаем.
    * ``relay_type = constant`` — включаем, держим ``impulse_ms`` и выключаем.
    * ``mode`` — куда отправлять: ``switch_impulse`` (сущность switch.*) или ``mqtt_impulse`` (топик).

    Возвращает текст для журнала. Бросает HomeAssistantError, если реле не настроено.
    """
    relay_type = str(control.get("relay_type", "impulse"))
    hold = relay_type != "impulse"
    impulse_ms = int(control.get("impulse_ms", 800))
    mode = control.get("mode", "switch_impulse")

    if mode == "mqtt_impulse":
        topic = (control.get("mqtt_topic") or "").strip()
        if not topic:
            raise HomeAssistantError("не задан MQTT-топик реле ворот")
        try:
            await hass.services.async_call(
                "mqtt", "publish", {"topic": topic, "payload": "ON"}, blocking=True
            )
            if hold:
                await asyncio.sleep(impulse_ms / 1000)
                await hass.services.async_call(
                    "mqtt", "publish", {"topic": topic, "payload": "OFF"}, blocking=True
                )
        except ServiceNotFound as err:
            raise HomeAssistantError("MQTT не настроен в Home Assistant") from err
    else:
        entity = (control.get("switch_entity") or "").strip()
        if not entity:
            raise HomeAssistantError("не задана сущность реле ворот")
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity}, blocking=True
        )
        if hold:
            await asyncio.sleep(impulse_ms / 1000)
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": entity}, blocking=True
            )

    text = f"реле: {mode}, тип {relay_type}" + (
        f", удержание {impulse_ms} мс" if hold else ", импульс даёт само реле"
    )
    _LOGGER.info("gate_vision: %s", text)
    return text
