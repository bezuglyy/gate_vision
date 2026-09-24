#!/usr/bin/env python3
"""Тест детектора gate_vision на эталонных кадрах (без Home Assistant).

Запуск:
    python3 tools/test_detector.py [--frames DIR]

Проверяет, что детектор (с зонами и порогами по умолчанию) правильно определяет
состояние на эталонных кадрах: день/ночь, закрыто/открыто/приоткрыто.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import types
from pathlib import Path

from PIL import Image

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "gate_vision"

CASES = [
    ("c6.jpg", "closed", "день, закрыто (15.09)"),
    ("c6_live_20260916.jpg", "open", "день, открыто (16.09)"),
    ("c6_night_closed_20260916.jpg", "closed", "ночь/ИК, закрыто (20:14)"),
]


def load_package():
    """Загрузить пакет интеграции без Home Assistant (только const/detector/settings-часть)."""
    pkg = types.ModuleType("gv")
    pkg.__path__ = [str(PKG_DIR)]
    sys.modules["gv"] = pkg
    for name in ("const", "detector"):
        spec = importlib.util.spec_from_file_location(
            f"gv.{name}", PKG_DIR / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"gv.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["gv.detector"], sys.modules["gv.const"]


def normalize_zone(zone, index):
    """Локальная копия settings.normalize_zone (без импорта HA)."""
    role = zone.get("role", "open")
    x, y = float(zone.get("x", 0.5)), float(zone.get("y", 0.5))
    w, h = float(zone.get("w", 0.1)), float(zone.get("h", 0.1))
    return {
        "id": zone.get("id", f"z{index}"),
        "name": zone.get("name", f"Зона {index}"),
        "role": role,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
    }


class Stub:
    def __init__(self, thresholds, zones):
        self.thresholds = thresholds
        self.zones = zones


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--frames", default=str(Path(__file__).resolve().parent.parent / "frames")
    )
    args = ap.parse_args()
    frames = Path(args.frames)

    detector, const = load_package()
    stub = Stub(
        const.DEFAULT_THRESHOLDS,
        [normalize_zone(z, i + 1) for i, z in enumerate(const.DEFAULT_ZONES)],
    )

    ok = fail = skip = 0
    print(f"кадры: {frames}")
    for name, expected, label in CASES:
        path = frames / name
        if not path.exists():
            print(f"  ПРОПУСК  {label:26s} — нет файла {name}")
            skip += 1
            continue
        res = detector.analyze(Image.open(path).convert("RGB"), stub)
        good = res["state"] == expected
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        print(
            f"  {'OK  ' if good else 'FAIL'}  {label:26s} -> {res['state']:7s} "
            f"(ждём {expected:7s}) b_open={res['b_open']:6.1f} mode={res['mode']:3s}"
        )
        if not good:
            print(f"        причина: {res['reason']}")
    print(f"\nитог: OK={ok} FAIL={fail} SKIP={skip}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
