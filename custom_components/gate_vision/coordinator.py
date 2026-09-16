"""Координатор: опрос камеры ворот, определение состояния и рассылка событий."""

from __future__ import annotations

import asyncio
import io
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from homeassistant.components import camera as camera_component
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from PIL import Image

from .const import (
    DEFAULT_GO2RTC_BASE,
    DOMAIN,
    EVENT_CAMERA_BACK,
    EVENT_CAMERA_LOST,
    EVENT_CLOSED,
    EVENT_LEFT_OPEN,
    EVENT_MOVING,
    EVENT_OPENED,
    EVENT_STATE,
    EVENT_UNKNOWN,
    FETCH_RETRIES,
    FETCH_TIMEOUT,
    MAX_EVENT_LOG,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_UNKNOWN,
    UNKNOWN_GRACE,
)
from .detector import analyze, zone_measurements
from .reactions import fire_reactions
from .settings import Settings

_LOGGER = logging.getLogger(__name__)

EVENT_DIR_NAME = "gate_vision"
EVENT_KEEP = 100


def build_snapshot_url(value: str, go2rtc_base: str = DEFAULT_GO2RTC_BASE) -> str:
    """Собрать URL снапшота из готового URL, имени потока go2rtc или RTSP-адреса."""
    value = (value or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    base = (go2rtc_base or DEFAULT_GO2RTC_BASE).rstrip("/")
    return f"{base}/api/frame.jpeg?src={quote(value, safe='')}"


class GateVisionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Опрашивает камеру и определяет состояние ворот."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, settings: Settings
    ) -> None:
        self.entry = entry
        self.settings = settings
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.title}",
            update_interval=timedelta(seconds=max(1, settings.scan_interval)),
        )
        self._session: aiohttp.ClientSession | None = None
        self._prev_rows: Any = None
        self._last_state: str | None = None
        self._last_change: datetime | None = None
        self._unknown_streak = 0
        self._camera_ok: bool | None = None
        self._open_since: datetime | None = None
        self._left_open_fired = False
        self._moving_until = 0.0
        self._last_event: dict[str, Any] | None = None
        self._last_emit: dict[str, datetime] = {}
        self._moving_active = False
        self.event_log: deque[dict[str, Any]] = deque(maxlen=MAX_EVENT_LOG)
        self.last_frame: bytes | None = None
        self.last_analysis: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.control_enabled_applied = bool(settings.control.get("enabled"))
        self.zone_entities_applied = sorted(z["id"] for z in settings.zones if z.get("entity"))

    # ---------------------------------------------------------------- сервис
    @property
    def url(self) -> str:
        return build_snapshot_url(self.settings.snapshot_url, self.settings.go2rtc_base)

    async def async_shutdown(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        await super().async_shutdown()

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
            )
        return self._session

    async def _fetch(self) -> bytes:
        """Кадр: из сущности camera.* (если выбрана) или по URL с повторами."""
        if self.settings.camera_entity:
            return await self._fetch_from_camera(self.settings.camera_entity)
        return await self._fetch_url()

    async def _fetch_from_camera(self, entity_id: str) -> bytes:
        """Кадр через компонент camera Home Assistant."""
        try:
            image = await camera_component.async_get_image(
                self.hass, entity_id, timeout=FETCH_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"камера {entity_id} недоступна: {err}") from err
        content = getattr(image, "content", None)
        if not content or len(content) < 5000:
            raise UpdateFailed(f"камера {entity_id} вернула пустой кадр")
        return content

    async def _fetch_url(self) -> bytes:
        """Кадр по URL с повторами (go2rtc иногда отвечает 500/404 на «холодном» старте)."""
        session = await self._session_get()
        last_error: Exception | None = None
        for attempt in range(1, FETCH_RETRIES + 1):
            try:
                async with session.get(self.url) as resp:
                    if resp.status != 200:
                        raise UpdateFailed(f"HTTP {resp.status}")
                    data = await resp.read()
                if len(data) < 5000:
                    raise UpdateFailed(f"слишком маленький кадр: {len(data)} байт")
                return data
            except Exception as err:  # noqa: BLE001
                last_error = err
                if attempt < FETCH_RETRIES:
                    await asyncio.sleep(1.5 * attempt)
        raise UpdateFailed(f"кадр не получен за {FETCH_RETRIES} попыток: {last_error}")

    # ---------------------------------------------------------------- цикл
    async def _async_update_data(self) -> dict[str, Any]:
        async with self._lock:
            return await self._update_once()

    async def _update_once(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        try:
            raw = await self._fetch()
            img = await self.hass.async_add_executor_job(
                lambda: Image.open(io.BytesIO(raw)).convert("RGB")
            )
            result = await self.hass.async_add_executor_job(analyze, img, self.settings)
            zones_info = result.get("zones_detail") or await self.hass.async_add_executor_job(
                zone_measurements, img, self.settings
            )
        except Exception as err:  # noqa: BLE001
            return await self._handle_failure(err)

        if self._camera_ok is not True:
            was_lost = self._camera_ok is False
            self._camera_ok = True
            if was_lost:
                await self._emit(EVENT_CAMERA_BACK, {"reason": "камера снова отвечает"})

        self.last_frame = raw
        self.last_analysis = {**result, "zones": zones_info, "ts": now.isoformat()}

        # --- «движется» по разнице профилей ---
        rows = await self.hass.async_add_executor_job(_rows, img)
        moving = False
        diff = 0.0
        if self._prev_rows is not None and getattr(rows, "shape", None) == getattr(
            self._prev_rows, "shape", None
        ):
            # сравниваем ФОРМУ профиля (без общего уровня яркости), иначе смена освещения
            # на рассвете/закате выглядит как движение полотна
            cur_norm = rows - rows.mean()
            prev_norm = self._prev_rows - self._prev_rows.mean()
            diff = float(abs(cur_norm - prev_norm).mean())
            moving = diff >= self.settings.thresholds.get("move_diff", 6.0)
        self._prev_rows = rows
        if moving:
            self._moving_until = time.monotonic() + 8.0
        still_moving = moving or time.monotonic() < self._moving_until

        state = result["state"]
        raw_state = state

        # --- сглаживание «неизвестно» ---
        stale = False
        if state == STATE_UNKNOWN:
            self._unknown_streak += 1
            if self._unknown_streak <= UNKNOWN_GRACE and self._last_state:
                state = self._last_state
                stale = True
            else:
                self._last_state = None
                self._open_since = None
                if self._unknown_streak == UNKNOWN_GRACE + 1:
                    await self._emit(
                        EVENT_UNKNOWN, {"reason": result.get("reason", "")}
                    )
        else:
            self._unknown_streak = 0

        # --- смена состояния ---
        if state != self._last_state and state != STATE_UNKNOWN:
            prev = self._last_state
            self._last_state = state
            self._last_change = now
            if state == STATE_OPEN:
                self._open_since = now
                self._left_open_fired = False
            else:
                self._open_since = None
                self._left_open_fired = False
            if prev is not None:
                await self._emit(
                    EVENT_OPENED if state == STATE_OPEN else EVENT_CLOSED,
                    {"from": prev, "reason": result.get("reason", "")},
                )

        # --- движение полотна (фронт) ---
        if moving and not getattr(self, "_moving_active", False):
            self._moving_active = True
            await self._emit(
                EVENT_MOVING, {"reason": f"профиль изменился (diff {diff:.1f})"}
            )
        elif not still_moving:
            self._moving_active = False

        # --- оставлены открытыми ---
        left_open_min = max(1, int(self.settings.left_open_min))
        repeat_left = self._repeat_min(EVENT_LEFT_OPEN)
        if (
            state == STATE_OPEN
            and self._open_since is not None
            and (now - self._open_since) >= timedelta(minutes=left_open_min)
            and (
                not self._left_open_fired
                or (repeat_left > 0 and self._repeat_due(EVENT_LEFT_OPEN, repeat_left))
            )
        ):
            self._left_open_fired = True
            await self._emit(
                EVENT_LEFT_OPEN,
                {
                    "minutes": left_open_min,
                    "reason": f"ворота открыты более {left_open_min} мин",
                },
            )

        open_for = (
            int((now - self._open_since).total_seconds()) if self._open_since else 0
        )

        return {
            **result,
            "state": state,
            "raw_state": raw_state,
            "stale": stale,
            "moving": still_moving,
            "profile_diff": round(diff, 2),
            "camera_ok": True,
            "changed_at": self._last_change.isoformat() if self._last_change else None,
            "open_since": self._open_since.isoformat() if self._open_since else None,
            "open_seconds": open_for,
            "left_open": bool(self._open_since and open_for >= left_open_min * 60),
            "zones": zones_info,
            "url": self.url,
            "camera_entity": self.settings.camera_entity or None,
            "last_event": self._last_event,
        }

    async def _handle_failure(self, err: Exception) -> dict[str, Any]:
        """Камера недоступна: держим последнее состояние, сообщаем один раз."""
        self._unknown_streak += 1
        first_failure = self._camera_ok is not False
        self._camera_ok = False
        repeat_lost = self._repeat_min(EVENT_CAMERA_LOST)
        if first_failure or (repeat_lost > 0 and self._repeat_due(EVENT_CAMERA_LOST, repeat_lost)):
            await self._emit(EVENT_CAMERA_LOST, {"reason": str(err)})
        _LOGGER.debug("gate_vision: кадр недоступен: %s", err)
        if self._last_state is None and not self.data:
            raise UpdateFailed(str(err))
        base = dict(self.data or {})
        base.update(
            {
                "state": self._last_state or STATE_UNKNOWN,
                "raw_state": STATE_UNKNOWN,
                "stale": True,
                "camera_ok": False,
                "moving": False,
                "reason": f"кадр недоступен: {err}",
                "zones": base.get("zones", []),
                "url": self.url,
                "last_event": self._last_event,
            }
        )
        return base

    # ---------------------------------------------------------------- события
    def _repeat_min(self, event: str) -> int:
        cfg = (self.settings.reactions or {}).get(event) or {}
        try:
            return max(0, int(cfg.get("repeat_min", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _repeat_due(self, event: str, minutes: int) -> bool:
        if minutes <= 0:
            return False
        last = self._last_emit.get(event)
        if last is None:
            return True
        return (dt_util.utcnow() - last) >= timedelta(minutes=minutes)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        entry = {
            "ts": dt_util.utcnow().isoformat(),
            "event": event,
            "state": self._last_state,
            "reason": payload.get("reason", ""),
            **payload,
        }
        self.event_log.append(entry)
        self._last_event = entry
        self._last_emit[event] = dt_util.utcnow()
        self.hass.bus.async_fire(
            EVENT_STATE, {"event": event, "entry_id": self.entry.entry_id, **entry}
        )
        await self._async_save_event_frame(event)
        _LOGGER.info("gate_vision: событие %s (%s)", event, payload.get("reason", ""))
        if not self.settings.learn_mode:
            await fire_reactions(self.hass, self.settings, event, entry)

    async def _async_save_event_frame(self, event: str) -> None:
        """Сохранить кадр события (кольцо на диске) — вне цикла событий."""
        if not self.last_frame:
            return
        try:
            await self.hass.async_add_executor_job(self._save_event_frame_sync, event)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("gate_vision: не сохранил кадр события: %s", err)

    def _save_event_frame_sync(self, event: str) -> None:
        """Запись кадра события на диск (вызывается в executor)."""
        directory = Path(self.hass.config.path(EVENT_DIR_NAME, "events"))
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (directory / f"{stamp}-{event}.jpg").write_bytes(self.last_frame)
        # копия для картинки в уведомлениях (/local/gate_vision/last_event.jpg)
        www_dir = Path(self.hass.config.path("www", EVENT_DIR_NAME))
        www_dir.mkdir(parents=True, exist_ok=True)
        (www_dir / "last_event.jpg").write_bytes(self.last_frame)
        files = sorted(directory.glob("*.jpg"))
        for old in files[:-EVENT_KEEP]:
            old.unlink(missing_ok=True)


def _rows(img: Image.Image):
    """Профиль яркости кадра (для детекции движения полотна)."""
    import numpy as np

    gray = np.asarray(img.convert("L")).astype(np.float32)
    height, width = gray.shape
    return gray[:, int(width * 0.57) : int(width * 0.82)].mean(1)
