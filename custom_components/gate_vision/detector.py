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


def _zone_box(zone: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    """Границы зоны в пикселях."""
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
    return float(np.mean(values)) if values else None


def _apply_ignore(gray: np.ndarray, zones: list[dict[str, Any]]) -> np.ndarray:
    """Заменить пиксели зон-исключений на NaN (исключаются из расчётов)."""
    if not zones:
        return gray
    out = gray.astype(np.float32).copy()
    height, width = gray.shape
    for zone in zones:
        x0, y0, x1, y1 = _zone_box(zone, width, height)
        out[y0:y1, x0:x1] = np.nan
    return out


def classify_zone(
    samples: dict[str, list] | None,
    current: float,
    band: bool | None = None,
) -> tuple[str, float]:
    """Определить состояние зоны по обученным замерам.

    Обученный замер хранит **два признака**: среднюю яркость и наличие полосы окон
    (структуру). Яркость одного состояния может совпадать с другим (рассвет, ночь),
    а структура — нет: у закрытого полотна в зоне видна полоса окон, у открытых
    ворот зона показывает улицу (ровный участок).

    Возвращает (состояние, уверенность 0..1).
    """
    samples = samples or {}

    def refs(state: str) -> list[dict[str, float | bool | None]]:
        out: list[dict[str, float | bool | None]] = []
        for item in samples.get(state) or []:
            if isinstance(item, dict):
                out.append({"mean": float(item.get("mean", 0)), "band": item.get("band")})
            else:  # старый формат — только яркость
                out.append({"mean": float(item), "band": None})
        return out

    open_refs, closed_refs = refs("open"), refs("closed")
    if not open_refs and not closed_refs:
        return "unknown", 0.0

    # --- приоритет: структурный признак, если он различает состояния ---
    if band is not None:
        open_bands = [r["band"] for r in open_refs if r["band"] is not None]
        closed_bands = [r["band"] for r in closed_refs if r["band"] is not None]
        if open_bands and closed_bands:
            open_major = sum(bool(b) for b in open_bands) * 2 >= len(open_bands)
            closed_major = sum(bool(b) for b in closed_bands) * 2 >= len(closed_bands)
            if open_major != closed_major:
                state = "closed" if band == closed_major else "open"
                return state, 0.8

    # --- если структура размечена только у одного состояния, решаем по ней ---
    if band is not None:
        open_bands = [r["band"] for r in open_refs if r["band"] is not None]
        closed_bands = [r["band"] for r in closed_refs if r["band"] is not None]
        single = open_bands or closed_bands
        if single and not (open_bands and closed_bands):
            ref_band = sum(bool(b) for b in single) * 2 >= len(single)
            known_state = "open" if open_bands else "closed"
            other = "closed" if known_state == "open" else "open"
            return (known_state, 0.6) if band == ref_band else (other, 0.5)

    # --- иначе по яркости (ближайший замер) ---
    if open_refs and closed_refs:
        d_open = min(abs(current - r["mean"]) for r in open_refs)
        d_closed = min(abs(current - r["mean"]) for r in closed_refs)
        mean_open = sum(r["mean"] for r in open_refs) / len(open_refs)
        mean_closed = sum(r["mean"] for r in closed_refs) / len(closed_refs)
        spread = abs(mean_open - mean_closed)
        conf = min(1.0, abs(d_open - d_closed) / spread) if spread > 1e-6 else 0.0
        return ("open" if d_open < d_closed else "closed"), round(conf, 2)

    # обучено только одно состояние: состояния бинарные, поэтому «не похоже» = другое состояние
    only = open_refs or closed_refs
    state = "open" if open_refs else "closed"
    other = "closed" if state == "open" else "open"
    margin = max(8.0, 0.12 * (sum(r["mean"] for r in only) / len(only)))
    near_mean = min(abs(current - r["mean"]) for r in only) <= margin
    ref_bands = [r["band"] for r in only if r["band"] is not None]
    if band is not None and ref_bands:
        ref_band = sum(bool(b) for b in ref_bands) * 2 >= len(ref_bands)
        near = near_mean and (band == ref_band)
    else:
        near = near_mean
    return (state, 0.6) if near else (other, 0.5)


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
            # фон меряем на отступе от края полосы и медианой: у самой границы идёт
            # переходное значение (рассвет/закат), из-за которого полоса терялась
            above_slice = rows[max(0, start - 80):max(0, start - 25)]
            below_slice = rows[min(len(rows) - 1, end + 25):min(len(rows), end + 100)]
            above = float(np.nanmedian(above_slice)) if above_slice.size else 0.0
            below = float(np.nanmedian(below_slice)) if below_slice.size else 0.0
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

    # --- замеры по зонам и классификация по обучению ---
    zones_detail: list[dict[str, Any]] = []
    for zone in zones:
        zx0, zy0, zx1, zy1 = _zone_box(zone, width, height)
        patch = gray[zy0:zy1, zx0:zx1]
        z_mean = float(np.nanmean(patch)) if patch.size else 0.0
        # структурный признак: полоса окон ищется в области НЕМНОГО ШИРЕ зоны —
        # иначе, если зона нарисована только по окнам, рядом нет полотна и «зажатость» не видна
        z_pad = int((zy1 - zy0) * 0.6)
        z_band, _ = _window_band(
            rows, max(0, zy0 - z_pad), min(floor_y, zy1 + z_pad), thresholds, span=floor_y
        )
        z_state, z_conf = classify_zone(zone.get("samples"), z_mean, band=bool(z_band))
        zones_detail.append(
            {
                "id": zone.get("id"),
                "name": zone.get("name"),
                "role": zone.get("role"),
                "kind": zone.get("kind"),
                "entity": bool(zone.get("entity")),
                "mean": round(z_mean, 1),
                "std": round(float(np.nanstd(patch)), 1) if patch.size else 0.0,
                "px": int(patch.size),
                "state": z_state,
                "conf": z_conf,
                "band": bool(z_band),
                "samples": {k: len(v or []) for k, v in (zone.get("samples") or {}).items()},
            }
        )
    result["zones_detail"] = zones_detail

    # --- приоритет 1: обученные зоны (если пользователь обучил состояния) ---
    learned = [
        z for z in result.get("zones_detail", [])
        if z.get("state") in ("open", "closed") and (z.get("conf") or 0) > 0.15
    ]
    if learned:
        best = max(learned, key=lambda z: (z.get("conf") or 0))
        result["state"] = best["state"]
        result["reason"] = (
            f"обученная зона «{best.get('name')}»: замер {best.get('mean')} → {best['state']} "
            f"(уверенность {best.get('conf')})"
        )
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
