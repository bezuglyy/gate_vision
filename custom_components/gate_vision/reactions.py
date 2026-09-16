"""Движок реагирований gate_vision: события -> каналы (notify/TTS/script/MQTT/webhook)."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.util import dt as dt_util

from .const import CHANNELS, EVENT_TITLES

_LOGGER = logging.getLogger(__name__)

DEFAULT_MESSAGES: dict[str, str] = {
    "opened": "Ворота открылись",
    "closed": "Ворота закрылись",
    "left_open": "Ворота оставлены открытыми",
    "moving": "Движение полотна ворот",
    "unknown": "Состояние ворот не определяется",
    "camera_lost": "Камера ворот недоступна",
    "camera_back": "Камера ворот снова доступна",
}


def _in_quiet_hours(cfg: dict[str, Any], now=None) -> bool:
    """Проверить тихие часы (поддерживает интервал через полночь)."""
    start = (cfg.get("quiet_from") or "").strip()
    end = (cfg.get("quiet_to") or "").strip()
    if not start or not end:
        return False
    try:
        sh, sm = (int(x) for x in start.split(":")[:2])
        eh, em = (int(x) for x in end.split(":")[:2])
    except (ValueError, AttributeError):
        return False
    local = now or dt_util.now()
    minutes = local.hour * 60 + local.minute
    start_m, end_m = sh * 60 + sm, eh * 60 + em
    if start_m == end_m:
        return False
    if start_m < end_m:
        return start_m <= minutes < end_m
    return minutes >= start_m or minutes < end_m  # через полночь


def _render(
    cfg: dict[str, Any], event: str, context: dict[str, Any]
) -> tuple[str, str]:
    """Заголовок и текст сообщения."""
    title = EVENT_TITLES.get(event, event)
    template = (cfg.get("message") or "").strip() or DEFAULT_MESSAGES.get(event, title)
    try:
        text = template.format(
            **{k: v for k, v in context.items() if isinstance(v, (str, int, float))}
        )
    except (KeyError, IndexError, ValueError):
        text = template
    if context.get("reason"):
        text = f"{text}. {context['reason']}"
    return title, text


def _image_path(hass: HomeAssistant) -> str | None:
    """Путь к последнему кадру события, доступный для уведомлений (/local/...)."""
    path = hass.config.path("www", "gate_vision", "last_event.jpg")
    import os

    return "/local/gate_vision/last_event.jpg" if os.path.exists(path) else None


async def fire_reactions(
    hass: HomeAssistant,
    settings: Any,
    event: str,
    context: dict[str, Any],
) -> None:
    """Выполнить настроенные реакции на событие."""
    cfg = (getattr(settings, "reactions", {}) or {}).get(event)
    if not cfg or not cfg.get("enabled"):
        return
    if _in_quiet_hours(cfg):
        _LOGGER.debug("gate_vision: событие %s подавлено тихими часами", event)
        return

    title, message = _render(cfg, event, context)
    channels = [c for c in (cfg.get("channels") or []) if c in CHANNELS]
    if not channels:
        return

    image = _image_path(hass)
    for channel in channels:
        try:
            await _dispatch(hass, channel, cfg, event, title, message, image, context)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("gate_vision: канал %s не сработал: %s", channel, err)


async def _dispatch(
    hass: HomeAssistant,
    channel: str,
    cfg: dict[str, Any],
    event: str,
    title: str,
    message: str,
    image: str | None,
    context: dict[str, Any],
) -> None:
    if channel == "notify":
        target = (cfg.get("notify_service") or "").strip()
        if not target:
            return
        if "." in target:
            domain, service = target.split(".", 1)
        else:
            domain, service = "notify", target
        data: dict[str, Any] = {"title": title, "message": message}
        if image:
            data["data"] = {"image": image}
        await hass.services.async_call(domain, service, data, blocking=False)
        _LOGGER.debug("gate_vision: notify.%s отправлен", service)

    elif channel == "persistent":
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": title,
                "message": message,
                "notification_id": f"gate_vision_{event}",
            },
            blocking=False,
        )

    elif channel == "tts":
        entity = (cfg.get("tts_entity") or "").strip()
        media_player = (cfg.get("tts_media_player") or "").strip()
        if not entity:
            return
        service = "speak"
        data = {"entity_id": entity, "message": message}
        if entity.startswith("tts."):
            if media_player:
                data["media_player_entity_id"] = media_player
            await hass.services.async_call("tts", service, data, blocking=False)
        else:
            # media_player: озвучить через tts:// (требует tts-платформу в HA)
            await hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": entity,
                    "media_content_id": f"tts://{message}",
                    "media_content_type": "music",
                },
                blocking=False,
            )

    elif channel == "script":
        target = (cfg.get("script_entity") or "").strip()
        if not target:
            return
        await hass.services.async_call(
            "homeassistant", "turn_on", {"entity_id": target}, blocking=False
        )

    elif channel == "mqtt":
        topic = (cfg.get("mqtt_topic") or "gate_vision/event").strip()
        payload = json.dumps(
            {
                "event": event,
                "title": title,
                "message": message,
                "state": context.get("state"),
                "ts": context.get("ts"),
            },
            ensure_ascii=False,
        )
        try:
            await hass.services.async_call(
                "mqtt",
                "publish",
                {"topic": topic, "payload": payload, "retain": False},
                blocking=False,
            )
        except ServiceNotFound:
            _LOGGER.debug("gate_vision: MQTT не настроен, канал пропущен")

    elif channel == "webhook":
        url = (cfg.get("webhook_url") or "").strip()
        if not url:
            return
        body = json.dumps(
            {
                "event": event,
                "title": title,
                "message": message,
                "state": context.get("state"),
                "reason": context.get("reason"),
                "ts": context.get("ts"),
            },
            ensure_ascii=False,
        ).encode()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                _LOGGER.debug("gate_vision: webhook %s -> %s", url, resp.status)
