"""Детектор состояния ворот по кадру камеры (без зависимости от Home Assistant).

Логика проверена на реальных кадрах 15–16.09.2026 (4196 замеров, зона неоднозначности пуста):

  * ЗАКРЫТО — низ проёма занят полотном; улицы под полотном нет.
    Окна полотна видны: днём пересветом, ночью в ИК — тёмными ячейками.
  * ОТКРЫТО — под полотном видна улица: днём светлая зона, ночью/ИК — тёмная.
  * НЕИЗВЕСТНО — кадр нечитаем (чёрный, ни улицы, ни окон).

Зоны (задаются в панели):
  * ``open``   — где при открытых воротах появляется улица (основной признак);
  * ``closed`` — область окон полотна (подтверждение «закрыто»);
  * ``ignore`` — маска помех (тележка, столбы, край кадра) — исключается из расчётов.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from .const import (
    DEFAULT_THRESHOLDS,
    ROLE_CLOSED,
    ROLE_IGNORE,
    ROLE_OPEN,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_UNKNOWN,
)


def classify_zone(samples: dict[str, list[float]] | None, current: float) -> tuple[str, float]:
    """Определить состояние зоны по обученным замерам яркости.

    Обучение: пользователь приводит объект в состояние и «запоминает» замер.
    Замеров на состояние может быть несколько (день, ночь, разное освещение) —
    берём ближайший. Возвращает (состояние, уверенность 0..1).
    """
    samples = samples or {}
    open_refs = [float(v) for v in (samples.get("open") or [])]
    closed_refs = [float(v) for v in (samples.get("closed") or [])]
    if not open_refs and not closed_refs:
        return "unknown", 0.0
    if open_refs and closed_refs:
        d_open = min(abs(current - v) for v in open_refs)
        d_closed = min(abs(current - v) for v in closed_refs)
        mean_open = sum(open_refs) / len(open_refs)
        mean_closed = sum(closed_refs) / len(closed_refs)
        spread = abs(mean_open - mean_closed)
        conf = min(1.0, abs(d_open - d_closed) / spread) if spread > 1e-6 else 0.0
        return ("open" if d_open < d_closed else "closed"), round(conf, 2)
    refs = open_refs or closed_refs
    state = "open" if open_refs else "closed"
    margin = max(8.0, 0.12 * (sum(refs) / len(refs)))
    near = min(abs(current - v) for v in refs) <= margin
    return (state, 0.6) if near else ("unknown", 0.3)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Непрерывные отрезки True в одномерной маске."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(mask):
        if value and start is None:
            start = i
        if not value and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def _zone_box(
    zone: dict[str, Any], width: int, height: int
) -> tuple[int, int, int, int]:
    x0 = max(0, min(width - 1, int(round(zone["x"] * width))))
    y0 = max(0, min(height - 1, int(round(zone["y"] * height))))
    x1 = max(x0 + 1, min(width, int(round((zone["x"] + zone["w"]) * width))))
    y1 = max(y0 + 1, min(height, int(round((zone["y"] + zone["h"]) * height))))
    return x0, y0, x1, y1


def _mean_in_zones(gray: np.ndarray, zones: list[dict[str, Any]]) -> float | None:
    """Средняя яркость по зонам (None, если зон нет)."""
    if not zones:
        return None
    height, width = gray.shape
    values = []
    for zone in zones:
        x0, y0, x1, y1 = _zone_box(zone, width, height)
        patch = gray[y0:y1, x0:x1]
        if patch.size:
            values.append(float(np.nanmean(patch)))
    if not values:
        return None
    return float(np.mean(values))


def _apply_ignore(gray: np.ndarray, zones: list[dict[str, Any]]) -> np.ndarray:
    """Заменить пиксели зон-исключений на NaN."""
    if not zones:
        return gray
    out = gray.astype(np.float32).copy()
    height, width = gray.shape
    for zone in zones:
        x0, y0, x1, y1 = _zone_box(zone, width, height)
        out[y0:y1, x0:x1] = np.nan
    return out


def _merge_runs(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Склеить отрезки, разделённые промежутком не больше gap.

    Между рядами окон проходит перемычка полотна — она рвёт «полосу окон» на части.
    """
    if not runs:
        return []
    merged = [runs[0]]
    for start, end in runs[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end - 1 <= gap:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def _window_band(
    rows: np.ndarray,
    y_from: int,
    y_to: int,
    thresholds: dict[str, float],
    span: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Найти полосу окон полотна.

    Окна — это участок, который резко отличается от полотна и **зажат полотном
    сверху и снизу**. Работает в обе стороны:
      * днём окна светятся (яркая полоса на тёмном полотне);
      * ночью/в ИК окна тёмные (тёмная полоса на подсвеченном полотне).

    ``span`` — высота проёма до линии пола: по ней считаются допустимые размеры
    полосы (а не по зоне поиска, иначе большая зона ломает проверку).
    """
    bright = thresholds.get("bright", DEFAULT_THRESHOLDS["bright"])
    dark_band = thresholds.get("dark_band", DEFAULT_THRESHOLDS["dark_band"])
    frame_t = thresholds.get("frame_t", DEFAULT_THRESHOLDS["frame_t"])
    min_band_f = thresholds.get("min_band_f", DEFAULT_THRESHOLDS["min_band_f"])
    max_band_f = thresholds.get("max_band_f", DEFAULT_THRESHOLDS["max_band_f"])

    merge_gap = max(24, int(0.08 * span))  # перемычка между рядами окон
    bands: list[dict[str, Any]] = []
    checks = (
        ("bright", rows > bright, lambda band: (band["above"] < frame_t * band["mean"]
                                                and band["below"] < frame_t * band["mean"])),
        ("dark", rows < dark_band, lambda band: (band["above"] > band["mean"] / max(frame_t, 0.01)
                                                 and band["below"] > band["mean"] / max(frame_t, 0.01))),
    )
    for kind, mask, ok_fn in checks:
        runs = _merge_runs(_runs(np.nan_to_num(mask[y_from:y_to], nan=0.0)), merge_gap)
        for start, end in runs:
            start += y_from
            end += y_from
            height_px = end - start + 1
            if height_px < min_band_f * span or height_px > max_band_f * span:
                continue
            band_mean = float(np.nanmean(rows[start:end + 1]))
            if band_mean <= 1:
                continue
            above_slice = rows[max(0, start - 18):start]
            below_slice = rows[end + 20:min(len(rows), end + 90)]
            above = float(np.nanmean(above_slice)) if above_slice.size else 0.0
            below = float(np.nanmean(below_slice)) if below_slice.size else 0.0
            band = {
                "kind": kind,
                "y0": int(start),
                "y1": int(end),
                "h": int(height_px),
                "mean": round(band_mean),
                "above": round(above),
                "below": round(below),
                "windows": bool(ok_fn({"mean": band_mean, "above": above, "below": below})),
            }
            bands.append(band)

    bands.sort(key=lambda b: (not b["windows"], -b["h"]))
    return next((b for b in bands if b["windows"]), None), bands


def analyze(img: Image.Image, settings: Any | None = None) -> dict[str, Any]:
    """Определить состояние ворот по кадру.

    ``settings`` — объект с полями ``thresholds``, ``zones_by_role()``;
    при ``None`` используются пороги и геометрия по умолчанию.
    """
    thresholds: dict[str, float] = dict(DEFAULT_THRESHOLDS)
    zones: list[dict[str, Any]] = []
    if settings is not None:
        thresholds.update(getattr(settings, "thresholds", {}) or {})
        try:
            zones = list(settings.zones)
        except Exception:  # noqa: BLE001
            zones = []

    open_zones = [z for z in zones if z["role"] == ROLE_OPEN]
    closed_zones = [z for z in zones if z["role"] == ROLE_CLOSED]
    ignore_zones = [z for z in zones if z["role"] == ROLE_IGNORE]

    rgb = np.asarray(img.convert("RGB")).astype(np.float32) / 255.0
    gray_raw = np.asarray(img.convert("L")).astype(np.float32)
    height, width = gray_raw.shape

    gray = _apply_ignore(gray_raw, ignore_zones)

    # насыщенность — цветной кадр или ч/б (ИК/ночь)
    mx = rgb.max(2)
    mn = rgb.min(2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    mean_sat = float(np.nanmean(sat))

    # профиль по строкам (плоскость ворот: зоны open/closed, иначе полоса по умолчанию)
    if open_zones or closed_zones:
        xs0 = min(z["x"] for z in (open_zones + closed_zones))
        xs1 = max(z["x"] + z["w"] for z in (open_zones + closed_zones))
    else:
        xs0, xs1 = 0.57, 0.82
    cx0 = max(0, min(width - 1, int(xs0 * width)))
    cx1 = max(cx0 + 1, min(width, int(xs1 * width)))
    with np.errstate(invalid="ignore"):
        rows = np.nanmean(gray[:, cx0:cx1], axis=1)

    floor_y = int(height * 0.545)
    top_y = int(height * 0.02)

    # --- основной признак: яркость «зоны открытого» (или низа проёма) ---
    b_open = _mean_in_zones(gray, open_zones)
    if b_open is None:
        low_slice = rows[int(0.70 * floor_y) : int(0.95 * floor_y)]
        b_open = float(np.nanmean(low_slice)) if low_slice.size else 0.0

    b_closed = _mean_in_zones(gray, closed_zones)
    if b_closed is None:
        top_slice = rows[top_y : int(0.25 * floor_y)]
        b_closed = float(np.nanmean(top_slice)) if top_slice.size else 0.0

    frame_mean = float(np.nanmean(rows[:floor_y])) if floor_y > 0 else 0.0

    # --- полоса окон (подтверждение «закрыто») ---
    if closed_zones:
        win_from = min(int(z["y"] * height) for z in closed_zones)
        win_to = max(int((z["y"] + z["h"]) * height) for z in closed_zones)
    else:
        win_from, win_to = top_y, max(top_y + 2, int(0.6 * floor_y))
    window_band, bands = _window_band(rows, win_from, win_to, thresholds, span=floor_y)

    result: dict[str, Any] = {
        "state": STATE_UNKNOWN,
        "reason": "",
        "frame": f"{width}x{height}",
        "b_open": round(float(b_open), 1),
        "b_closed": round(float(b_closed), 1),
        "frame_mean": round(frame_mean, 1),
        "mean_sat": round(mean_sat, 4),
        "color": bool(mean_sat >= thresholds.get("gray_sat", 0.02)),
        "mode": "day" if mean_sat >= thresholds.get("gray_sat", 0.02) else "ir",
        "windows": bool(window_band),
        "window_band": window_band,
        "bands": bands,
        "zones_used": {
            "open": len(open_zones),
            "closed": len(closed_zones),
            "ignore": len(ignore_zones),
        },
    }

    if frame_mean < thresholds.get("frame_min_mean", 15.0):
        result["reason"] = "кадр почти чёрный — камера или свет недоступны"
        return result

    if window_band:
        # окна полотна на месте => полотно опущено => закрыто
        result["state"] = STATE_CLOSED
        result["reason"] = (
            f"окна полотна видны ({'яркая' if window_band['kind'] == 'bright' else 'тёмная'} полоса "
            f"y {window_band['y0']}–{window_band['y1']}, яркость {window_band['mean']}) — проём закрыт полотном"
        )
    elif result["color"]:
        # ДЕНЬ: улица в зоне «открыто» светлая
        if b_open > thresholds.get("street_low_t", 140.0):
            result["state"] = STATE_OPEN
            result["reason"] = (
                f"окон не видно; зона «открыто» светлая ({b_open:.0f} > "
                f"{thresholds.get('street_low_t', 140):.0f}) — видна улица"
            )
        else:
            result["state"] = STATE_CLOSED
            result["reason"] = f"окон не видно, зона «открыто» тёмная ({b_open:.0f}) — проём закрыт полотном"
    else:
        # НОЧЬ / ИК: улица тёмная, полотно подсвечено камерой
        if b_open < thresholds.get("dark_low_t", 60.0):
            result["state"] = STATE_OPEN
            result["reason"] = (
                f"окон не видно; ч/б (ИК): зона «открыто» тёмная ({b_open:.0f} < "
                f"{thresholds.get('dark_low_t', 60):.0f}) — видна улица"
            )
        else:
            result["state"] = STATE_CLOSED
            result["reason"] = f"окон не видно; ч/б (ИК): зона «открыто» светлая ({b_open:.0f}) — проём закрыт полотном"

    return result


def zone_measurements(
    img: Image.Image, settings: Any | None = None
) -> list[dict[str, Any]]:
    """Измерения по каждой зоне (для живого предпросмотра в панели)."""
    gray = np.asarray(img.convert("L")).astype(np.float32)
    height, width = gray.shape
    zones = list(getattr(settings, "zones", []) or []) if settings is not None else []
    out: list[dict[str, Any]] = []
    for zone in zones:
        x0, y0, x1, y1 = _zone_box(zone, width, height)
        patch = gray[y0:y1, x0:x1]
        mean = round(float(patch.mean()), 1) if patch.size else 0.0
        state, conf = classify_zone(zone.get("samples"), mean)
        out.append(
            {
                "id": zone["id"],
                "name": zone.get("name"),
                "role": zone["role"],
                "kind": zone.get("kind"),
                "entity": bool(zone.get("entity")),
                "mean": mean,
                "std": round(float(patch.std()), 1) if patch.size else 0.0,
                "px": int(patch.size),
                "state": state,
                "conf": conf,
                "samples": {k: len(v or []) for k, v in (zone.get("samples") or {}).items()},
            }
        )
    return out
