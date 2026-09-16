"""Настройки интеграции gate_vision: зоны, пороги, реагирования, управление.

Настройки живут в options записи конфигурации, но кэшируются в памяти, чтобы
панель могла менять их «на лету» без перезагрузки интеграции.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CAMERA_ENTITY,
    CONF_SCHEDULES,
    CONF_CONTROL,
    DEFAULT_ZONES,
    KIND_OPEN_CLOSED,
    MAX_SAMPLES,
    ZONE_KINDS,
    CONF_LEARN_MODE,
    CONF_REACTIONS,
    CONF_THRESHOLDS,
    CONF_ZONES,
    DEFAULT_CONTROL,
    DEFAULT_LEFT_OPEN_MIN,
    DEFAULT_REACTIONS,
    DEFAULT_THRESHOLDS,
    DOMAIN,
    ROLE_IGNORE,
    ROLE_OPEN,
    ZONE_ROLES,
)

_LOGGER = logging.getLogger(__name__)


def normalize_zone(zone: dict[str, Any], index: int) -> dict[str, Any]:
    """Привести зону к корректному виду (доли 0..1, роль из списка)."""

    def _f(value: Any, default: float) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return default

    role = str(zone.get("role", ROLE_OPEN))
    if role not in ZONE_ROLES:
        role = ROLE_IGNORE
    x = _f(zone.get("x"), 0.5)
    y = _f(zone.get("y"), 0.5)
    w = _f(zone.get("w"), 0.1) or 0.01
    h = _f(zone.get("h"), 0.1) or 0.01
    if x + w > 1.0:
        w = 1.0 - x
    if y + h > 1.0:
        h = 1.0 - y
    samples_raw = zone.get("samples") if isinstance(zone.get("samples"), dict) else {}

    def _sample(value):
        """Замер: старый формат (число) или новый ({mean, band})."""
        if _is_number(value):
            return {"mean": float(value), "band": None}
        if isinstance(value, dict) and _is_number(value.get("mean")):
            band = value.get("band")
            return {"mean": float(value["mean"]), "band": None if band is None else bool(band)}
        return None

    samples = {
        state: [s for s in (_sample(v) for v in (samples_raw.get(state) or [])) if s][-MAX_SAMPLES:]
        for state in ("open", "closed")
    }
    kind = str(zone.get("kind") or KIND_OPEN_CLOSED)
    if kind not in ZONE_KINDS:
        kind = KIND_OPEN_CLOSED
    return {
        "id": str(zone.get("id") or f"z{index}"),
        "name": str(zone.get("name") or f"Зона {index}"),
        "role": role,
        "x": round(x, 4),
        "y": round(y, 4),
        "w": round(max(w, 0.005), 4),
        "h": round(max(h, 0.005), 4),
        "entity": bool(zone.get("entity", False)),
        "kind": kind,
        "samples": samples,
    }


class Settings:
    """Текущие настройки интеграции (с кэшем в памяти)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.reload()

    # ------------------------------------------------------------------ загрузка
    def reload(self) -> None:
        merged = {**self.entry.data, **self.entry.options}
        self.raw = merged

        self.camera_entity: str = merged.get(CONF_CAMERA_ENTITY, "")
        self.snapshot_url: str = merged.get("snapshot_url", "")
        self.go2rtc_base: str = merged.get("go2rtc_base", "")
        self.scan_interval: int = int(merged.get("scan_interval", 5))

        thresholds = dict(DEFAULT_THRESHOLDS)
        thresholds.update(
            {
                k: v
                for k, v in (merged.get(CONF_THRESHOLDS) or {}).items()
                if k in DEFAULT_THRESHOLDS
            }
        )
        self.thresholds: dict[str, float] = {
            k: float(v) for k, v in thresholds.items() if _is_number(v)
        }

        zones = merged.get(CONF_ZONES)
        if not isinstance(zones, list) or not zones:
            zones = copy.deepcopy(DEFAULT_ZONES)
        self.zones: list[dict[str, Any]] = [
            normalize_zone(z, i + 1) for i, z in enumerate(zones) if isinstance(z, dict)
        ]
        if not self.zones:
            self.zones = copy.deepcopy(DEFAULT_ZONES)

        reactions = copy.deepcopy(DEFAULT_REACTIONS)
        for event, cfg in (merged.get(CONF_REACTIONS) or {}).items():
            if event in reactions and isinstance(cfg, dict):
                reactions[event].update(cfg)
        self.reactions: dict[str, dict] = reactions

        control = dict(DEFAULT_CONTROL)
        control.update(merged.get(CONF_CONTROL) or {})
        self.control: dict[str, Any] = control

        from .schedules import normalize_schedule

        raw_schedules = merged.get(CONF_SCHEDULES)
        if not isinstance(raw_schedules, list):
            raw_schedules = []
        self.schedules: list[dict] = [
            normalize_schedule(item, i + 1) for i, item in enumerate(raw_schedules) if isinstance(item, dict)
        ]

        self.learn_mode: bool = bool(merged.get(CONF_LEARN_MODE, False))
        self.left_open_min: int = int(
            merged.get("left_open_min", DEFAULT_LEFT_OPEN_MIN)
        )

    # ------------------------------------------------------------------ зоны
    def zones_by_role(self, role: str) -> list[dict[str, Any]]:
        return [z for z in self.zones if z["role"] == role]

    # ------------------------------------------------------------------ сохранение
    async def async_save(self, patch: dict[str, Any]) -> None:
        """Сохранить часть настроек в options записи и обновить кэш."""
        options = dict(self.entry.options)
        for key, value in patch.items():
            if key == CONF_ZONES and isinstance(value, list):
                options[CONF_ZONES] = [
                    normalize_zone(z, i + 1)
                    for i, z in enumerate(value)
                    if isinstance(z, dict)
                ]
            elif key == CONF_THRESHOLDS and isinstance(value, dict):
                thresholds = dict(self.thresholds)
                thresholds.update(
                    {k: float(v) for k, v in value.items() if _is_number(v)}
                )
                options[CONF_THRESHOLDS] = thresholds
            elif key == CONF_REACTIONS and isinstance(value, dict):
                reactions = copy.deepcopy(self.reactions)
                for event, cfg in value.items():
                    if event in reactions and isinstance(cfg, dict):
                        reactions[event].update(cfg)
                options[CONF_REACTIONS] = reactions
            elif key == CONF_SCHEDULES and isinstance(value, list):
                from .schedules import normalize_schedule

                options[CONF_SCHEDULES] = [
                    normalize_schedule(item, i + 1) for i, item in enumerate(value) if isinstance(item, dict)
                ]
            elif key == CONF_CONTROL and isinstance(value, dict):
                control = dict(self.control)
                control.update(value)
                options[CONF_CONTROL] = control
            elif key in ("camera_entity", "snapshot_url", "go2rtc_base", "learn_mode", "left_open_min"):
                options[key] = value
            elif key == "scan_interval":
                options[key] = int(value)
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        self.reload()

    def as_dict(self) -> dict[str, Any]:
        """Текущие настройки для панели."""
        return {
            "camera_entity": self.camera_entity,
            "snapshot_url": self.snapshot_url,
            "go2rtc_base": self.go2rtc_base,
            "scan_interval": self.scan_interval,
            "thresholds": self.thresholds,
            "zones": self.zones,
            "reactions": self.reactions,
            "control": self.control,
            "schedules": self.schedules,
            "learn_mode": self.learn_mode,
            "left_open_min": self.left_open_min,
        }


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def get_settings(hass: HomeAssistant, entry: ConfigEntry) -> Settings:
    """Получить (или создать) объект настроек записи."""
    store = hass.data.setdefault(DOMAIN, {})
    settings = store.get(f"settings:{entry.entry_id}")
    if settings is None:
        settings = Settings(hass, entry)
        store[f"settings:{entry.entry_id}"] = settings
    return settings
