#!/usr/bin/env python3
"""Регресс-тест детектора gate_vision («Обнаружение»).

Прогоняет детектор по:
  1) эталонным кадрам (день/ночь/рассвет — закрыто; день — открыто/приоткрыто);
  2) собранным сериям движения (frames/collect/burst-*) — проверяет, что в серии
     нет «дребезга» состояния (частых переключений) и есть осмысленный переход.

Запуск:
    python3 tools/regression.py [--frames DIR] [--verbose]

Код возврата 0 — всё хорошо, 1 — есть проблемы.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import ssl
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

from PIL import Image

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "gate_vision"
PROJ = Path(__file__).resolve().parent.parent.parent  # /opt/harness/projects/vorota

REFERENCE = [
    ("frames/c6.jpg", "closed", "день, закрыто (15.09)"),
    ("frames/c6_live_20260916.jpg", "open", "день, открыто (16.09)"),
    ("frames/c6_night_closed_20260916.jpg", "closed", "ночь/ИК, закрыто"),
]

MAX_SWITCHES = 3  # допустимое число переключений состояния внутри серии


def load_detector():
    pkg = types.ModuleType("gv")
    pkg.__path__ = [str(PKG)]
    sys.modules["gv"] = pkg
    for name in ("const", "detector"):
        spec = importlib.util.spec_from_file_location(f"gv.{name}", PKG / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"gv.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["gv.detector"], sys.modules["gv.const"]


class Stub:
    """Настройки по умолчанию (как у новой записи интеграции)."""

    def __init__(self, const):
        self.thresholds = dict(const.DEFAULT_THRESHOLDS)
        self.zones = [dict(z) for z in const.DEFAULT_ZONES]


def live_settings() -> Stub | None:
    """Попробовать взять текущие настройки из работающей интеграции."""
    try:
        token = subprocess.run(
            [str(PROJ / "tools" / "ha_token.sh")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            "https://192.168.103.200:38123/api/gate_vision/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=20).read())
        stub = Stub.__new__(Stub)
        stub.thresholds = data["thresholds"]
        stub.zones = data["zones"]
        return stub
    except Exception as err:  # noqa: BLE001
        print(f"  (настройки с «Базы» не получены: {err} — беру по умолчанию)")
        return None


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # noqa: E501
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default=str(PROJ / "frames"))
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--limit", type=int, default=10, help="сколько последних серий проверять")
    ap.add_argument("--live", action="store_true", help="взять настройки с работающей интеграции")
    ap.add_argument("--per-series", type=int, default=6, help="кадров на серию")
    args = ap.parse_args()
    frames = Path(args.frames)

    detector, const = load_detector()
    # по умолчанию проверяем ЛОГИКУ на настройках по умолчанию (чистая установка);
    # --live — проверить с текущими настройками пользователя
    stub = (live_settings() if args.live else None) or Stub(const)
    print(
        f"зоны: {[(z['id'], z.get('name'), z.get('role'), len((z.get('samples') or {}).get('closed') or [])) for z in stub.zones]}"
    )
    print(
        f"обучение: {'есть' if any((z.get('samples') or {}).get('closed') or (z.get('samples') or {}).get('open') for z in stub.zones) else 'нет'}\n"
    )

    ok = fail = 0

    print("1) эталонные кадры")
    for rel, expected, label in REFERENCE:
        path = frames / Path(rel).name
        if not path.exists():
            print(f"   ПРОПУСК  {label} — нет файла")
            continue
        res = detector.analyze(Image.open(path).convert("RGB"), stub)
        good = res["state"] == expected
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        print(
            f"   {'OK  ' if good else 'FAIL'}  {label:26s} -> {res['state']:7s} (ждём {expected})"
        )
        if not good:
            print(f"         причина: {res['reason']}")

    print("\n2) серии движения (нет дребезга состояния)")
    bursts = (
        sorted((frames / "collect").glob("burst-*"))
        if (frames / "collect").exists()
        else []
    )
    bursts = bursts[-args.limit:]
    print(f"   (проверяю {len(bursts)} последних серий, до {args.per_series} кадров на серию)")
    for burst in bursts:
        shots = sorted(burst.glob("*.jpg"))
        if len(shots) < 4:
            continue
        if len(shots) > args.per_series:
            step = len(shots) / args.per_series
            shots = [shots[int(i * step)] for i in range(args.per_series)]
        states = []
        for shot in shots:
            try:
                states.append(
                    detector.analyze(Image.open(shot).convert("RGB"), stub)["state"]
                )
            except Exception:  # noqa: BLE001
                continue
        if not states:
            continue
        switches = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])
        uniq = sorted(set(states))
        good = switches <= MAX_SWITCHES
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
        mark = "OK  " if good else "FAIL"
        print(
            f"   {mark}  {burst.name}: {len(states)} кадров, состояния {uniq}, переключений {switches}"
        )
        if args.verbose or not good:
            print(f"         {''.join(s[0] for s in states)}")

    print(f"\nитог: OK={ok} FAIL={fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
