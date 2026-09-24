"""Запреты по сенсорам (interlocks) для gate_vision.

Правило запрета — это условие на сенсор Home Assistant, при выполнении
которого выбранные команды (открыть/закрыть/стоп/импульс расписания)
блокируются (`mode: block`) или только сопровождаются предупреждением
(`mode: warn`).

Формат правила (хранится в options записи, ключ ``interlocks``)::

    {
      "id": "i1",
      "name": "Мороз: не закрывать",
      "entity_id": "sensor.ulichnaia_temperatura",
      "attribute": "",           # пусто — состояние сущности, иначе атрибут
      "op": "below",             # above | below | equal | not_equal | is_on | is_off
      "value": -25.0,            # порог для числовых условий
      "actions": ["close"],      # какие команды запрещать; пусто = все
      "mode": "block",           # block — запрещать | warn — только предупреждать
      "enabled": True,
    }
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    INTERLOCK_MODES,
    INTERLOCK_OPS,
    IS_OFF_STATES,
    ISH_ON_STATES,
)

_LOGGER = logging.getLogger(__name__)

MAX_INTERLOCKS = 20


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def normalize_interlock(rule: dict[str, Any], index: int) -> dict[str, Any]:
    """Привести правило к корректному виду (безопасные значения по умолчанию)."""
    op = str(rule.get("op") or "below")
    if op not in INTERLOCK_OPS:
        op = "below"
    mode = str(rule.get("mode") or "block")
    if mode not in INTERLOCK_MODES:
        mode = "block"
    actions_raw = rule.get("actions")
    if isinstance(actions_raw, str):
        actions_raw = [actions_raw]
    actions = [str(a) for a in (actions_raw or []) if a]
    value: Any = rule.get("value")
    if op in ("above", "below", "equal", "not_equal"):
        if not _is_number(value):
            value = 0.0
        value = float(value)
    else:
        value = None
    return {
        "id": str(rule.get("id") or f"i{index}"),
        "name": str(rule.get("name") or f"Правило {index}")[:120],
        "entity_id": str(rule.get("entity_id") or "").strip(),
        "attribute": str(rule.get("attribute") or "").strip(),
        "op": op,
        "value": value,
        "actions": actions,
        "mode": mode,
        "enabled": bool(rule.get("enabled", True)),
    }


def normalize_interlocks(rules: Any) -> list[dict[str, Any]]:
    if not isinstance(rules, list):
        return []
    out = [
        normalize_interlock(item, i + 1)
        for i, item in enumerate(rules)
        if isinstance(item, dict)
    ]
    return out[:MAX_INTERLOCKS]


def _state_value(hass: HomeAssistant, rule: dict[str, Any]) -> tuple[Any, str | None]:
    """Текущее значение правила: (значение, ошибка)."""
    entity_id = rule.get("entity_id") or ""
    if not entity_id:
        return None, "не выбран сенсор"
    state = hass.states.get(entity_id)
    if state is None:
        return None, f"сущность {entity_id} не найдена"
    attr = rule.get("attribute") or ""
    if attr:
        if attr not in state.attributes:
            return None, f"нет атрибута {attr}"
        return state.attributes.get(attr), None
    if state.state in ("unknown", "unavailable", ""):
        return None, f"сенсор недоступен ({state.state})"
    return state.state, None


def condition_met(rule: dict[str, Any], raw: Any) -> bool:
    """Выполнено ли условие правила для значения сенсора."""
    op = rule.get("op")
    if op in ("above", "below", "equal", "not_equal"):
        if not _is_number(raw):
            return False
        value = float(raw)
        threshold = float(rule.get("value") or 0.0)
        if op == "above":
            return value > threshold
        if op == "below":
            return value < threshold
        if op == "equal":
            return abs(value - threshold) < 1e-9
        return abs(value - threshold) >= 1e-9
    text = str(raw).strip().lower()
    if op == "is_on":
        return text in ISH_ON_STATES
    if op == "is_off":
        return text in IS_OFF_STATES
    return False


def rule_applies(rule: dict[str, Any], action: str) -> bool:
    """Относится ли правило к команде (пустой список действий = ко всем)."""
    actions = rule.get("actions") or []
    return not actions or action in actions


def describe(rule: dict[str, Any], raw: Any = None) -> str:
    """Человеческое описание правила (для журнала и ошибок)."""
    op_text = INTERLOCK_OPS.get(rule.get("op"), rule.get("op"))
    if rule.get("op") in ("above", "below", "equal", "not_equal"):
        cond = f"{op_text} {rule.get('value')}"
    else:
        cond = op_text
    sensor = rule.get("entity_id") or "—"
    if rule.get("attribute"):
        sensor = f"{sensor}.{rule['attribute']}"
    now = "" if raw is None else f", сейчас {raw}"
    return f"«{rule.get('name')}»: {sensor} {cond}{now}"


def evaluate(
    hass: HomeAssistant, rules: list[dict[str, Any]], action: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Проверить правила для команды.

    Возвращает ``(blocking, warning)``: списки сработавших правил
    (со значением сенсора и текстом причины).
    """
    blocking: list[dict[str, Any]] = []
    warning: list[dict[str, Any]] = []
    for rule in rules or []:
        if not rule.get("enabled", True) or not rule_applies(rule, action):
            continue
        raw, error = _state_value(hass, rule)
        if error:
            # Недоступный сенсор запретом НЕ считаем — иначе управление «встанет».
            _LOGGER.debug("gate_vision: запрет «%s»: %s", rule.get("name"), error)
            continue
        if not condition_met(rule, raw):
            continue
        item = {**rule, "current": raw, "text": describe(rule, raw)}
        if rule.get("mode") == "warn":
            warning.append(item)
        else:
            blocking.append(item)
    return blocking, warning


async def async_check(
    hass: HomeAssistant, settings: Any, action: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Проверка запретов для команды (обёртка над :func:`evaluate`)."""
    rules = getattr(settings, "interlocks", None) or []
    return evaluate(hass, rules, action)


def active_for_actions(
    hass: HomeAssistant, rules: list[dict[str, Any]], actions: list[str]
) -> list[dict[str, Any]]:
    """Сработавшие правила по любому из действий (для атрибутов/панели)."""
    seen: dict[str, dict[str, Any]] = {}
    for action in actions:
        blocking, _ = evaluate(hass, rules, action)
        for item in blocking:
            seen[item["id"]] = item
    return list(seen.values())
