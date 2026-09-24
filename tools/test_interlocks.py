"""Тесты логики запретов (interlocks) — без Home Assistant.

Запуск: /opt/ha-testenv/bin/python projects/vorota/gate_vision/tools/test_interlocks.py
(нужен HA-venv: модуль импортирует homeassistant.core для типов)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components"
sys.path.insert(0, str(ROOT))

# Пакет регистрируем «пустым»: иначе выполнится __init__.py интеграции
# и потянет HA-камеру (turbojpeg), которого в тестовом venv нет.
_pkg = types.ModuleType("gate_vision")
_pkg.__path__ = [str(ROOT / "gate_vision")]
sys.modules.setdefault("gate_vision", _pkg)

from gate_vision.interlocks import (  # noqa: E402
    active_for_actions,
    condition_met,
    describe,
    evaluate,
    normalize_interlock,
    normalize_interlocks,
)

PASS = FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


class State:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class Hass:
    """Минимальная заглушка hass.states."""

    def __init__(self, states: dict):
        self.states = {k: State(*v) if isinstance(v, tuple) else State(v) for k, v in states.items()}


def main() -> None:
    rules = normalize_interlocks([
        {"name": "Мороз", "entity_id": "sensor.t_out", "op": "below", "value": -25, "actions": ["close"], "mode": "block"},
        {"name": "Жара", "entity_id": "sensor.t_out", "op": "above", "value": 40, "actions": [], "mode": "warn"},
        {"name": "Дверь", "entity_id": "binary_sensor.dver", "op": "is_on", "actions": ["open"]},
        {"name": "Атрибут", "entity_id": "sensor.climate", "attribute": "temperature", "op": "above", "value": 10, "actions": ["open"]},
        {"name": "Выкл", "entity_id": "sensor.t_out", "op": "below", "value": 100, "enabled": False},
    ])
    check("нормализация: 5 правил", len(rules) == 5)
    check("нормализация: оп из списка", all(r["op"] in ("below", "above", "is_on") for r in rules))
    check("нормализация: режим по умолчанию block", rules[2]["mode"] == "block")
    check("нормализация: числовое значение float", rules[0]["value"] == -25.0 and rules[2]["value"] is None)

    h = Hass({
        "sensor.t_out": ("-30.5", {}),
        "binary_sensor.dver": ("on", {}),
        "sensor.climate": ("5", {"temperature": 21.5}),
    })
    blk, warn = evaluate(h, rules, "close")
    check("мороз < −25 и команда close → блок", len(blk) == 1 and blk[0]["name"] == "Мороз")
    check("описание содержит значение сенсора", "сейчас -30.5" in describe(blk[0], blk[0]["current"]))
    check("warn-правило «жара» не срабатывает", not warn)

    blk, warn = evaluate(h, rules, "open")
    check("дверь is_on → блокирует open", any(b["name"] == "Дверь" for b in blk))
    check("атрибут temperature>10 → блокирует open", any(b["name"] == "Атрибут" for b in blk))

    h2 = Hass({"sensor.t_out": ("45", {}), "binary_sensor.dver": ("off", {}), "sensor.climate": ("5", {"temperature": 5})})
    blk, warn = evaluate(h2, rules, "stop")
    check("пустой список действий = все команды (жара, warn)", not blk and any(w["name"] == "Жара" for w in warn))
    blk, warn = evaluate(h2, rules, "close")
    check("мороз не срабатывает при +45", not blk)

    h3 = Hass({"binary_sensor.dver": ("off", {})})
    blk, warn = evaluate(h3, rules, "open")
    check("недоступный сенсор (нет t_out) не блокирует", not blk)

    check("условие equal", condition_met({"op": "equal", "value": 5.0}, "5"))
    check("условие not_equal", condition_met({"op": "not_equal", "value": 5.0}, "6"))
    check("is_off для 'off'", condition_met({"op": "is_off"}, "off"))
    check("нечисловое значение для above = False", not condition_met({"op": "above", "value": 1.0}, "unavailable"))

    act = active_for_actions(h, rules, ["open", "close", "stop"])
    check("active_for_actions объединяет по всем командам", len(act) >= 3)
    check("выключенное правило не попадает", all(r["name"] != "Выкл" for r in act))

    print(f"\n  ИТОГ: PASS={PASS} FAIL={FAIL}")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
